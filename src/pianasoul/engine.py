"""Transkun piano transcription engine: loaded once and reused for every file of a run (the command line), or once
for a piece in a process of its own (pipeline.Transcription: the service).

Transkun transcribes a recording in overlapping windows of 16 seconds, 8 apart, each picking up where the one before
left off, and joins their notes. This does the same, window by window, and hands on as it goes the notes no later
window can change (parts): a piece can play while the rest of it is transcribed. The whole is those parts together,
note for note what transkun's own transcribe gives."""

from __future__ import annotations

import contextlib
import math
import threading
from collections import defaultdict
from importlib import resources
from importlib.metadata import version

import pretty_midi


def pick_device(requested: str = "auto") -> str:
    import torch

    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    # Apple Silicon's GPU: about three times as fast as its CPU, with the same notes (now and then a note's end a
    # MIDI tick apart, rounded differently), and the CPU to fall back on for a piece it fails (transcribe).
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class TranskunEngine:
    def __init__(self, device: str = "auto", threads: int | None = None):
        import moduleconf
        import torch

        self.device = pick_device(device)
        if threads:
            torch.set_num_threads(threads)
        base = resources.files("transkun") / "pretrained"
        with resources.as_file(base / "2.0.conf") as conf_path:
            conf_manager = moduleconf.parseFromFile(str(conf_path))
        model_cls = conf_manager["Model"].module.TransKun
        self.model = model_cls(conf=conf_manager["Model"].config)
        with resources.as_file(base / "2.0.pt") as weight_path:
            checkpoint = torch.load(weight_path, map_location="cpu", weights_only=False)
        state = checkpoint.get("best_state_dict", checkpoint.get("state_dict"))
        self.model.load_state_dict(state)  # every weight must match the model, or loading fails
        self.model.to(self.device).eval()
        self.sample_rate = int(self.model.fs)
        self.version = f"transkun {version('transkun')} (pretrained 2.0)"
        self._lock = threading.Lock()

    def transcribe(self, audio, part=None, without=()) -> tuple[pretty_midi.PrettyMIDI, str]:
        """audio: float32 numpy array (samples, channels) at self.sample_rate. Returns the MIDI and the device that
        made it. part(midi, until), when given, is called after each window with the notes settled since (a MIDI of
        them) and the time before which every note of the piece is now there: math.inf with the last. without: the
        controls (MIDI numbers) the parts leave out, which the MIDI keeps: a pedal no one keeps (the soft pedal, which
        the clean-up drops) would hold them back, as one held down is not settled until it is seen let up. When the
        GPU (mps) fails, the rest of this piece is transcribed on the CPU, and the next one is tried on the GPU again
        (the model goes to the device each piece starts on)."""
        from transkun.Data import writeMidi

        left = {-number for number in without}  # transkun has a pedal as a note, its number below zero

        def parts(notes, until):
            if part:
                part(writeMidi(_in_order(n for n in notes if n.pitch not in left)), until)

        with self._lock:
            notes, device = self._transcribe(audio, parts, left)
        return writeMidi(_in_order(notes)), device

    def warm(self) -> None:
        """A window of silence through the model, for nothing: on the GPU a piece's first window takes three times as
        long as the rest (some 1.2 s, not 0.4: the GPU gets ready for the work), better spent while its recording is
        still on its way. A failure here is the piece's to meet, and it goes on on the CPU (transcribe)."""
        import torch
        from transkun.Util import makeFrame

        model = self.model
        size = math.ceil(model.segmentSizeInSecond * model.fs)
        with self._lock, torch.no_grad(), contextlib.suppress(Exception):
            model.to(self.device)
            model.transcribeFrames(makeFrame(torch.zeros(2, size, device=self.device), model.hopSize,
                                             model.windowSize).unsqueeze(0),
                                   forcedStartPos=[0] * len(model.targetMIDIPitch), velocityCriteron="hamming",
                                   lastFrameIdx=round(size / model.hopSize))

    def _transcribe(self, audio, part, left) -> tuple[list, str]:
        """Transkun's transcribe (transkun.ModelTransformer.TransKun.transcribe), a window at a time: the same
        windows, each decoded from where the one before left off, and the same joining of their notes."""
        import torch
        import torch.nn.functional as F
        from transkun.Util import makeFrame

        model, device = self.model, self.device
        model.to(device)
        fs, hop = model.fs, model.hopSize
        lead = model.segmentSizeInSecond - model.segmentHopSizeInSecond  # silence before the first window, and after
        step = math.ceil(model.segmentHopSizeInSecond * fs / hop) * hop
        size = math.ceil(model.segmentSizeInSecond * fs)
        joined, made = Joined(left), []
        with torch.no_grad():
            x = torch.from_numpy(audio).to(device).transpose(-1, -2)
            x = F.pad(x, (math.ceil(lead * fs), math.ceil(fs * lead)))
            start_pos = [math.floor(lead * fs / hop)] * len(model.targetMIDIPitch)  # where each pitch's notes go on
            for at in range(0, x.shape[-1], step):
                window = x[:, at:at + size]
                if window.shape[-1] < size:
                    window = F.pad(window, (0, size - window.shape[-1]))
                try:
                    events, ended = model.transcribeFrames(makeFrame(window, hop, model.windowSize).unsqueeze(0),
                                                           forcedStartPos=start_pos, velocityCriteron="hamming",
                                                           lastFrameIdx=round(size / hop))
                except Exception as exc:
                    if device != "mps":
                        raise
                    print(f"  (transcription on the GPU failed: {type(exc).__name__}: {exc}; the rest of this piece "
                          "on the CPU)", flush=True)
                    device = "cpu"
                    model.to(device)
                    x, window = x.to(device), window.to(device)
                    events, ended = model.transcribeFrames(makeFrame(window, hop, model.windowSize).unsqueeze(0),
                                                           forcedStartPos=start_pos, velocityCriteron="hamming",
                                                           lastFrameIdx=round(size / hop))
                start_pos = [max(k - step // hop, 0) for k in ended]
                begins = at / fs - lead
                for e in events[0]:
                    e.start += begins
                    e.end += begins
                    e.start = max(e.start, 0)
                    e.end = max(e.end, e.start)
                joined.add(events[0])
                last = at + step >= x.shape[-1]
                settled, until = joined.settle(None if last else (at + step) / fs - lead)
                made += settled
                part(settled, until)
        return made, device


class Joined:
    """The notes of a piece as transkun joins its windows' (its mergeIncompleteEvent and resolveOverlapping), and
    which of them are settled: a later window can still join a pitch's last note to its own, take its place, or drop
    it (when no window has seen it end); and a note's end is cut where the next of its pitch begins. The pitches left
    are settled all at once, after the last window, and hold nothing back meanwhile."""

    def __init__(self, left=()):
        self.by_pitch: dict[int, list] = defaultdict(list)  # each pitch's notes, in the order they were found
        self.given: set = set()  # the notes settled and handed on
        self.left = set(left)

    def add(self, events) -> None:
        """A window's notes, its times the piece's."""
        for e in events:
            notes = self.by_pitch[e.pitch]
            if notes and e.start < notes[-1].end:
                if e.hasOnset:
                    notes[-1] = e
                else:
                    notes[-1].hasOffset = e.hasOffset
                    notes[-1].end = max(e.end, notes[-1].end)
                continue
            if e.hasOnset:
                notes.append(e)

    def settle(self, later: float | None) -> tuple[list, float]:
        """The notes settled since the last call, each with its end as it is in the whole, and the time before which
        every note is settled. later: where the next window begins (none of its notes, nor any after, starts before
        it); None after the last window, when every note is settled.

        Each pitch on its own: no note of it still to come (its last, while it may change, or a later window's) starts
        before its first unsure point; a note there before it is known, unless it ends past that point with none
        known after it, so that one still to come may cut it."""
        from transkun.Data import Note

        pitches = []
        for pitch, notes in self.by_pitch.items():
            if later is not None and pitch in self.left:
                continue
            open_ = later is not None and bool(notes) and (notes[-1].end > later or not notes[-1].hasOffset)
            unsure = math.inf if later is None else min(later, notes[-1].start) if open_ else later
            kept = [n for n in (notes[:-1] if open_ else notes) if n.hasOffset or n is notes[-1]]
            kept = sorted((n for n in kept if n not in self.given), key=lambda n: (n.start, n.end))
            known = unsure
            for note, after in zip(kept, kept[1:] + [None]):
                if note.start >= unsure:
                    break
                if (after is None or after.start >= unsure) and note.end > unsure:
                    known = note.start
                    break
            pitches.append((kept, unsure, known))
        until = min((known for _, _, known in pitches), default=math.inf if later is None else later)
        settled = []
        for kept, unsure, _ in pitches:
            for note, after in zip(kept, kept[1:] + [None]):
                if note.start >= until:
                    break
                self.given.add(note)
                end = min(note.end, after.start) if after is not None and after.start < unsure else note.end
                if note.start < end:
                    settled.append(Note(note.start, end, note.pitch, note.velocity))
        return settled, until


def _in_order(notes) -> list:
    """Notes as transkun hands them to its MIDI writer: by their start, then end, then pitch."""
    return sorted(notes, key=lambda n: (n.start, n.end, n.pitch))
