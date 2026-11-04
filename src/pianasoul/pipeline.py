"""Audio file -> raw MIDI -> cleaned MIDI + info.json; in this process (convert_file), or in one of its own
(Transcription, which runs this module), which can hand on the notes as they are made (parts)."""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pretty_midi

from .audio import load_audio
from .cleanup import RESOLUTION, SOFT, SUSTAIN, TEMPO, clean, pedal_intervals

log = logging.getLogger(__spec__.name)
EXTRAS_DIR = "extras"
PART = '{"part": '  # how a part's line begins, among what Transcription's process says


@dataclass
class Options:
    legato: bool = False
    keep_soft_pedal: bool = False
    pedal_ramps: bool = False


def summarize(midi: pretty_midi.PrettyMIDI) -> dict:
    notes = [n for i in midi.instruments for n in i.notes]
    ccs = [c for i in midi.instruments for c in i.control_changes]
    sustain = sorted((c for c in ccs if c.number == SUSTAIN), key=lambda c: c.time)
    vel = [n.velocity for n in notes]
    return {
        "notes": len(notes),
        "velocity_min": min(vel) if vel else None,
        "velocity_max": max(vel) if vel else None,
        "distinct_velocities": len(set(vel)),
        "pitch_range": [min(n.pitch for n in notes), max(n.pitch for n in notes)] if notes else None,
        "sustain_pedal_presses": len(pedal_intervals(ccs)),
        "sustain_pedal_released_at_end": (not sustain) or sustain[-1].value < 64,
        "soft_pedal_events": sum(1 for c in ccs if c.number == 67),
        "end_time_s": round(midi.get_end_time(), 3),
    }


def transcribe_audio(engine, audio_path, options: Options, part=None):
    """Run the model and clean-up. Returns (raw_midi, clean_midi, info). part(midi, until, length), when given, has
    the clean notes as the model makes them (engine.TranskunEngine.transcribe: each time those that start before
    until), and how long the recording is; the last, until math.inf, with what the clean-up of the whole adds at its
    end. Together they are the clean MIDI: the clean-up goes a note at a time (legato and pedal ramps, which look at
    the notes around, are for a whole piece only)."""
    if part and (options.legato or options.pedal_ramps):
        raise ValueError("legato and pedal ramps clean up a whole piece, not its parts")
    t0 = time.time()
    audio = load_audio(audio_path, engine.sample_rate)
    length = len(audio) / engine.sample_rate
    t1 = time.time()
    last = []  # the last part, held for the clean-up of the whole

    def in_parts(midi, until):
        midi = clean(midi, keep_soft_pedal=options.keep_soft_pedal)[0]
        if until == math.inf:
            last.append(midi)
        else:
            part(midi, until, length)

    raw, device = engine.transcribe(audio, in_parts if part else None, () if options.keep_soft_pedal else (SOFT,))
    t2 = time.time()
    cleaned, report = clean(raw, keep_soft_pedal=options.keep_soft_pedal,
                            legato=options.legato, pedal_ramps=options.pedal_ramps)
    if part:
        if report.final_pedal_release_added:  # the pedal down at the end: let up after the last note
            last[0].instruments[0].control_changes.append(
                max((c for c in cleaned.instruments[0].control_changes if c.number == SUSTAIN), key=lambda c: c.time))
        part(last[0], math.inf, length)
    info = {
        "source": Path(audio_path).name,
        "engine": engine.version,
        "device": device,
        "audio_duration_s": round(length, 3),
        "transcribe_seconds": round(t2 - t1, 1),
        "total_seconds": round(time.time() - t0, 1),
        "raw": summarize(raw),
        "output": summarize(cleaned),
        "cleanup": report.as_dict(),
        "options": vars(options),
    }
    log.debug("transcribed %s: %.0f s of music in %.0f s on %s (loading %.1f s); %d notes, %d pedal presses",
              info["source"], info["audio_duration_s"], info["transcribe_seconds"], device, t1 - t0,
              info["output"]["notes"], info["output"]["sustain_pedal_presses"])
    return raw, cleaned, info


def unique_path(path: Path, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many existing copies of {path}")


class Transcription:
    """convert_file in a process of its own, which ends with the piece (the model's memory with it): its loading and
    its work then never hold this process's interpreter, where the thread that sends the piano its notes must run on
    time (in the service's own process, a transcription held it for seconds at a time and the piece playing
    stalled). It starts before its recording is there, so the model loads while the recording downloads. What it
    says (the GPU failing over to the CPU, say) is passed on. It ends by itself when this process does, and at once
    when closed (with) or stopped."""

    def __init__(self, out_dir, options: Options, *, output_name: str | None = None,
                 stopped: threading.Event | None = None):
        self.stopped = stopped
        self._child = subprocess.Popen([sys.executable, "-I", "-B", "-m", __spec__.name, str(out_dir),
                                        json.dumps(vars(options)), output_name or ""],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._lines: queue.Queue = queue.Queue()  # what it says, line by line; None once it has said all
        self._error: list[str] = []
        self._readers = [threading.Thread(target=self._read, daemon=True),
                         threading.Thread(target=lambda: self._error.append(self._child.stderr.read()), daemon=True)]
        for reader in self._readers:
            reader.start()

    def _read(self) -> None:
        for line in self._child.stdout:
            self._lines.put(line.rstrip("\n"))
        self._lines.put(None)

    def __enter__(self) -> Transcription:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._child.poll() is None:
            self._child.kill()
        self._child.wait()
        for reader in self._readers:  # what it said is read to its end
            reader.join()
        for pipe in (self._child.stdin, self._child.stdout, self._child.stderr):
            pipe.close()

    def convert(self, audio_path, part=None) -> dict:
        """The recording transcribed: its info, once the piece is made. part(midi, until, length), when given, has its
        notes as they are made (transcribe_audio), on this thread. InterruptedError once stopped is set: the process
        is ended at once. RuntimeError with its error when it fails."""
        try:
            self._child.stdin.write(json.dumps({"audio": str(audio_path), "parts": part is not None}) + "\n")
            self._child.stdin.close()
        except OSError:  # it has ended already: what it said says why
            pass
        said = None  # its latest line: its info, once it is done
        while True:
            if self.stopped is not None and self.stopped.is_set():
                self.close()
                raise InterruptedError("stopped")
            try:
                line = self._lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                break
            if line.startswith(PART):
                if part:
                    part(*_part_heard(json.loads(line)["part"]))
                continue
            if said is not None:
                print(said, flush=True)
            said = line
        self.close()
        if self._child.returncode != 0 or said is None:
            if said is not None:
                print(said, flush=True)
            error = "".join(self._error).strip()
            raise RuntimeError(error or f"the transcription ended with {self._child.returncode}")
        return json.loads(said)


def _part_said(midi: pretty_midi.PrettyMIDI, until: float, length: float) -> str:
    """A part (transcribe_audio's) as Transcription's process says it: one line, which _part_heard reads."""
    piano = midi.instruments[0]
    return PART + json.dumps({"until": None if until == math.inf else until, "length": length,
                              "notes": [[n.start, n.end, n.pitch, n.velocity] for n in piano.notes],
                              "controls": [[c.time, c.number, c.value] for c in piano.control_changes]}) + "}"


def _part_heard(part: dict) -> tuple[pretty_midi.PrettyMIDI, float, float]:
    midi = pretty_midi.PrettyMIDI(resolution=RESOLUTION, initial_tempo=TEMPO)
    piano = pretty_midi.Instrument(program=0, name="Piano")
    piano.notes = [pretty_midi.Note(velocity, pitch, start, end) for start, end, pitch, velocity in part["notes"]]
    piano.control_changes = [pretty_midi.ControlChange(number, value, at) for at, number, value in part["controls"]]
    midi.instruments.append(piano)
    return midi, math.inf if part["until"] is None else part["until"], part["length"]


def convert_file(engine, audio_path, out_dir, options: Options, overwrite: bool = False,
                 *, output_name: str | None = None, part=None) -> dict:
    """part: as transcribe_audio's."""
    audio_path, out_dir = Path(audio_path), Path(out_dir)
    if output_name is not None and (not output_name or output_name in {".", ".."}
                                   or "/" in output_name or "\\" in output_name):
        raise ValueError("output_name must be a filename stem")
    raw, cleaned, info = transcribe_audio(engine, audio_path, options, part)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / EXTRAS_DIR).mkdir(exist_ok=True)
    midi_path = unique_path(out_dir / f"{output_name or audio_path.stem}.mid", overwrite)
    stem = midi_path.stem
    raw_path = out_dir / EXTRAS_DIR / f"{stem}.raw.mid"
    info_path = out_dir / EXTRAS_DIR / f"{stem}.json"
    info["files"] = {"midi": str(midi_path), "raw_midi": str(raw_path), "info": str(info_path)}
    # Publish the final MIDI last: an interrupted write must not look completed.
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".transcribe-") as tmp:
        stage = Path(tmp)
        cleaned.write(str(stage / "final.mid"))
        raw.write(str(stage / "raw.mid"))
        pretty_midi.PrettyMIDI(str(stage / "final.mid"))
        (stage / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(stage / "raw.mid", raw_path)
        os.replace(stage / "info.json", info_path)
        os.replace(stage / "final.mid", midi_path)
    return info


if __name__ == "__main__":  # Transcription's process: the model, then the piece it is given, its info as the last line
    import select
    import signal
    import warnings

    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Control+C is for the program that started it, which ends it
    parent = os.getppid()

    def orphaned():  # the program that started it gone (quit, restarted, killed): this one goes too
        while os.getppid() == parent:
            time.sleep(1)
        os._exit(1)

    threading.Thread(target=orphaned, daemon=True).start()
    warnings.filterwarnings("ignore")  # the model's notes on stderr would read as its error
    try:
        from .engine import TranskunEngine

        out_dir, options, name = sys.argv[1:4]
        engine = TranskunEngine()  # meanwhile, its recording comes
        if engine.device == "mps" and not select.select([sys.stdin], [], [], 0)[0]:
            engine.warm()  # while the recording is not there yet
        task = sys.stdin.readline()
        if not task:  # none after all (its download failed): nothing to do
            sys.exit(0)
        task = json.loads(task)
        made = convert_file(engine, task["audio"], out_dir, Options(**json.loads(options)), output_name=name or None,
                            part=(lambda *part: print(_part_said(*part), flush=True)) if task["parts"] else None)
    except Exception as exc:
        sys.exit(str(exc))  # its error, whole, for Transcription to say
    print(json.dumps(made), flush=True)
