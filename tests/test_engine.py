"""The transcription engine: window by window it is transkun's own transcription, note for note, and hands on the
notes as they are settled (with a stand-in for the model, its notes made up; and with the real model once); a piece
the GPU fails goes on on the CPU. And a piece transcribed in a process of its own (pipeline.Transcription), started
before its recording is there, with its notes as they are made."""
import contextlib
import io
import json
import math
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

import mido
import numpy as np
import pretty_midi

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import pipeline
from pianasoul.engine import TranskunEngine
from pianasoul.growing import Growing

WITH_MODEL = unittest.skipUnless(find_spec('transkun'), 'the advanced extra is not installed')


def engine(model, device='cpu'):
    e = TranskunEngine.__new__(TranskunEngine)  # without loading the model
    e.model, e.device, e._lock = model, device, threading.Lock()
    return e


class MadeUp:
    """transkun's model as its transcribe uses it, with notes made up: the same window (its audio, and where each
    pitch was left off) gives the same notes. Many, as a piano with the pedal down gives: running into the window
    after (no offset seen), on from the window before (no onset seen), and of the pedals."""
    fs, hopSize, windowSize = 400, 8, 32  # small numbers, small windows: 16 s of 400 samples a second
    segmentSizeInSecond, segmentHopSizeInSecond = 16, 8
    targetMIDIPitch = [-64, -67] + list(range(21, 108 + 1))

    def __init__(self, seed=0, fails=None):
        self.seed, self.fails, self.devices, self.windows = seed, fails, [], 0

    def to(self, device):
        self.devices.append(device)
        return self

    def transcribeFrames(self, frames, forcedStartPos=None, velocityCriteron='hamming', onsetBound=None,
                         lastFrameIdx=None):
        from transkun.Data import Note

        self.windows += 1
        if self.fails and self.fails(self.windows, frames.device.type):
            raise RuntimeError('MPS backend out of memory')
        heard = frames.flatten()[::997][:64].tolist()  # some of its audio (as it is on any device, unlike a sum)
        rng = random.Random(hash((self.seed, *heard, *forcedStartPos)))
        notes, last = [], [0] * len(self.targetMIDIPitch)
        duration = self.hopSize / self.fs
        for k, pitch in enumerate(self.targetMIDIPitch):
            if pitch > 0 and rng.random() < 0.85:  # most keys do nothing in a window
                continue
            at = max(0, forcedStartPos[k] + rng.randint(-3, 3) if rng.random() < 0.5 else rng.randint(0, 40))
            end = 0.0
            while at < lastFrameIdx:
                to = min(lastFrameIdx, at + rng.choice((1, 2, 5, 20, 120, 400, 2000)))
                start = max((at + rng.uniform(-0.5, 0.5)) * duration, end)
                end = max((to + rng.uniform(-0.5, 0.5)) * duration, start + 1e-8)
                onset, offset = at > 0 or rng.random() < 0.3, to < lastFrameIdx or rng.random() < 0.3
                notes.append(Note(start, end, pitch, rng.randint(0, 127), onset, offset))
                if offset:
                    last[k] = to
                at = to + rng.choice((1, 3, 30, 300))
        notes.sort(key=lambda n: (n.start, n.end, n.pitch))
        return [notes], last


def notes_of(midi):
    piano = midi.instruments[0]
    return (sorted((n.start, n.end, n.pitch, n.velocity) for n in piano.notes),
            sorted((c.time, c.number, c.value) for c in piano.control_changes))


def transkuns(model, audio, device='cpu'):
    """What transkun's own transcribe makes of it on the device, as its MIDI writer has it."""
    import torch
    from transkun.Data import writeMidi
    from transkun.ModelTransformer import TransKun

    with torch.no_grad(), contextlib.redirect_stdout(io.StringIO()):  # it says when it drops a note cut to nothing
        return writeMidi(TransKun.transcribe(model, torch.from_numpy(audio).to(device)))


def audio_of(seconds, fs=MadeUp.fs, seed=0):
    return np.random.default_rng(seed).standard_normal((round(seconds * fs), 2)).astype(np.float32)


@WITH_MODEL
class WindowTests(unittest.TestCase):
    def test_window_by_window_it_is_transkuns_own_transcription_note_for_note(self):
        for seconds in (3, 8, 15.99, 16, 40, 97.3):  # shorter than a window, a window's step, many
            for seed in range(6):
                with self.subTest(seconds=seconds, seed=seed):
                    audio = audio_of(seconds, seed=seed)
                    parts = []
                    mine, device = engine(MadeUp(seed)).transcribe(audio, lambda midi, until: parts.append(
                        (notes_of(midi), until)))
                    whole = notes_of(transkuns(MadeUp(seed), audio))
                    self.assertEqual(notes_of(mine), whole)
                    self.assertGreater(len(whole[0]), 20, 'Notes enough to join')
                    self.assertEqual(device, 'cpu')
                    self.assertEqual(parts[-1][1], math.inf, 'The last part has the rest')
                    together = (sorted(n for (ns, _), _ in parts for n in ns), sorted(c for (_, cs), _ in parts for c in cs))
                    self.assertEqual(together, whole, 'The parts are the whole')
                    before = 0.0
                    for (ns, cs), until in parts:  # each settled: no later part brings a note before its until
                        self.assertGreaterEqual(until, before)
                        self.assertTrue(all(before <= n[0] < until for n in ns), (before, until))
                        self.assertTrue(all(before <= c[0] for c in cs))
                        before = until

    def test_a_pedal_the_parts_leave_out_holds_them_back_no_more_and_the_whole_keeps_it(self):
        class SoftPedalDown(MadeUp):  # short notes, and the soft pedal down from the start, never seen let up
            def transcribeFrames(self, frames, forcedStartPos=None, lastFrameIdx=None, **given):
                from transkun.Data import Note
                [notes], last = super().transcribeFrames(frames, forcedStartPos, lastFrameIdx=lastFrameIdx, **given)
                notes = [n for n in notes if n.pitch > 0 and n.hasOnset and n.hasOffset and n.end - n.start < 1]
                notes.append(Note(0.0, lastFrameIdx * self.hopSize / self.fs, -67, 90, self.windows == 1, False))
                return [notes], last
        audio = audio_of(60)
        held, left = [], []
        engine(SoftPedalDown()).transcribe(audio, lambda midi, until: held.append((until, notes_of(midi))))
        mine, _ = engine(SoftPedalDown()).transcribe(audio, lambda midi, until: left.append((until, notes_of(midi))),
                                                     without=(67,))
        self.assertEqual([until for until, _ in held[:-1]], [0.0] * (len(held) - 1), 'Held back till the end')
        self.assertGreater(left[-2][0], 40, 'Not held back')
        self.assertFalse([c for _, (_, cs) in left for c in cs if c[1] == 67], 'The parts leave it out')
        self.assertEqual(notes_of(mine), notes_of(transkuns(SoftPedalDown(), audio)), 'The whole keeps it')
        self.assertIn(67, {c[1] for c in notes_of(mine)[1]})

    def test_its_parts_come_as_the_windows_go(self):
        parts = []
        engine(MadeUp()).transcribe(audio_of(60), lambda midi, until: parts.append(until))
        self.assertEqual(len(parts), math.ceil((8 + 60 + 8) / 8), 'One after each window, 8 s apart, over the piece '
                         'and the silence put before and after it')
        self.assertGreater(sum(1 for until in parts[:-1] if until > 0), 4, 'Most settle some of it')

    def test_a_piece_the_gpu_fails_goes_on_on_the_cpu_and_said(self):
        import torch
        if not torch.backends.mps.is_available():
            self.skipTest('no GPU (mps) here')
        model = MadeUp(fails=lambda window, device: window == 3 and device == 'mps')
        e, parts = engine(model, 'mps'), []
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            midi, device = e.transcribe(audio_of(40), lambda midi, until: parts.append(until))
        self.assertEqual(device, 'cpu')
        self.assertIn('GPU failed: RuntimeError: MPS backend out of memory; the rest of this piece on the CPU',
                      said.getvalue())
        self.assertEqual(notes_of(midi), notes_of(transkuns(MadeUp(), audio_of(40))), 'Nothing lost or made twice')
        self.assertEqual(len(parts), 7, 'Window 3 on the CPU, not the piece again')
        model.devices.clear()
        e.transcribe(audio_of(20))
        self.assertEqual(model.devices[0], 'mps', 'The next piece on the GPU again')

    def test_on_the_cpu_a_failure_is_the_pieces(self):
        e = engine(MadeUp(fails=lambda window, device: window == 2))
        with self.assertRaises(RuntimeError):
            e.transcribe(audio_of(40))

    def test_the_real_model_window_by_window_is_its_own_transcription(self):
        e = TranskunEngine()
        t = np.arange(int(44100 * 30)) / 44100  # a few notes struck in turn, long ones over the windows' joins
        audio = sum(0.2 * np.sin(2 * np.pi * f * t) * np.exp(-0.3 * ((t - at) % 10)) * (t >= at)
                    for f, at in ((262, 0.5), (330, 6.9), (392, 7.6), (523, 15.8), (440, 23.9)))
        audio = np.stack([audio, audio], axis=1).astype(np.float32)
        parts = []
        with contextlib.redirect_stderr(io.StringIO()):
            mine, device = e.transcribe(audio, lambda midi, until: parts.append(notes_of(midi)))
            whole = transkuns(e.model, audio, device)
        self.assertEqual(notes_of(mine), notes_of(whole))
        self.assertTrue(notes_of(mine)[0], 'Notes heard')
        self.assertEqual(sorted(n for ns, _ in parts for n in ns), notes_of(mine)[0])


class ApartTests(unittest.TestCase):
    """Transcription, with a stand-in for the process (a shell script in Python's place) or with the real model."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def child(self, script):
        fake = self.root / 'python'
        fake.write_text('#!/bin/sh\n' + script)
        fake.chmod(0o755)
        return patch.object(pipeline.sys, 'executable', str(fake))

    def convert(self, audio, out=None, part=None, **given):
        with pipeline.Transcription(out or self.root, pipeline.Options(), **given) as apart:
            return apart.convert(audio, part)

    def test_its_info_is_its_last_line_and_what_it_said_before_is_passed_on(self):
        said = io.StringIO()
        with self.child('echo "transcription on the GPU failed; the rest of this piece on the CPU"\n'
                        'echo \'{"device": "cpu", "files": {"midi": "piece.mid"}}\'\n'), contextlib.redirect_stdout(said):
            info = self.convert('a.m4a', output_name='piece')
        self.assertEqual(info, {'device': 'cpu', 'files': {'midi': 'piece.mid'}})
        self.assertIn('the rest of this piece on the CPU', said.getvalue())

    def test_its_parts_are_handed_on_as_they_come_and_not_said(self):
        part = pipeline._part_said(midi_of([(0.5, 1.0, 60, 70)], [(0.4, 64, 100), (1.2, 64, 0)]), 2.5, 30.0)
        said, parts = io.StringIO(), []
        with self.child(f"read task\necho '{part}'\necho 'said meanwhile'\n"
                        'echo \'{"files": {"midi": "piece.mid"}}\'\n'), contextlib.redirect_stdout(said):
            self.convert('a.m4a', part=lambda midi, until, length: parts.append((notes_of(midi), until, length)))
        self.assertEqual(parts, [(([(0.5, 1.0, 60, 70)], [(0.4, 64, 100), (1.2, 64, 0)]), 2.5, 30.0)])
        self.assertEqual(said.getvalue(), 'said meanwhile\n')

    def test_its_failure_is_said_whole(self):
        with self.child('echo "could not decode audio: [mp3] Failed to find two frames" >&2\necho "Invalid data" >&2\n'
                        'exit 1\n'), self.assertRaisesRegex(RuntimeError, '^could not decode audio: .+\nInvalid data$'):
            self.convert('a.m4a')

    def test_stopped_it_ends_at_once(self):
        stopped = threading.Event()
        threading.Timer(0.3, stopped.set).start()
        started = time.monotonic()
        with self.child('exec sleep 30\n'), self.assertRaises(InterruptedError):
            self.convert('a.m4a', stopped=stopped)
        self.assertLess(time.monotonic() - started, 3)

    def test_it_starts_before_its_recording_is_there(self):
        with self.child('echo started > "$5/started"\nread task\necho "$task" > "$5/task"\n'
                        'echo \'{"files": {}}\'\n'):
            with pipeline.Transcription(self.root, pipeline.Options()) as apart:
                deadline = time.monotonic() + 5
                while not (self.root / 'started').exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((self.root / 'started').exists(), 'Under way, its recording not given yet')
                apart.convert(self.root / 'a.m4a')
        self.assertEqual(json.loads((self.root / 'task').read_text()), {'audio': str(self.root / 'a.m4a'), 'parts': False})

    def test_not_given_its_recording_it_ends(self):
        with self.child('read task || exit 0\nexec sleep 30\n'):
            apart = pipeline.Transcription(self.root, pipeline.Options())
            started = time.monotonic()
            apart.close()
        self.assertLess(time.monotonic() - started, 3)

    @WITH_MODEL
    def test_its_error_is_its_own_and_it_ends_when_the_program_that_started_it_does(self):
        with self.assertRaisesRegex(RuntimeError, '^could not decode audio'):
            self.convert(self.root / 'missing.mp3', self.root / 'out')
        long, out = self.tone('long.wav', 120), self.root / 'out-of-a-program-gone'  # some ten seconds to transcribe
        self.addCleanup(subprocess.run, ['pkill', '-f', str(out)])
        starter = subprocess.Popen([sys.executable, '-c', 'import json, subprocess, sys, time; child = subprocess.Popen(['
                                    'sys.executable, "-I", "-B", "-m", "pianasoul.pipeline", sys.argv[2], "{}", ""], '
                                    'stdin=subprocess.PIPE, text=True); child.stdin.write(json.dumps({"audio": sys.argv[1], '
                                    '"parts": False}) + "\\n"); child.stdin.close(); time.sleep(1)', str(long), str(out)])
        starter.wait()
        time.sleep(3)  # the starter gone: its child sees it within a second
        left = subprocess.run(['pgrep', '-f', str(out)], capture_output=True, text=True).stdout
        self.assertEqual(left, '', 'Nothing left running')

    def tone(self, name, seconds):
        """A note struck twice a second, as audio to transcribe."""
        audio = self.root / name
        t = np.arange(int(44100 * seconds)) / 44100
        tone = (0.3 * np.sin(2 * np.pi * 440 * t) * np.exp(-2 * (t % 0.5))).astype(np.float32)
        with wave.open(str(audio), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes((tone * 32767).astype('<i2').tobytes())
        return audio

    @WITH_MODEL
    def test_the_real_model_in_its_own_process(self):
        audio, piece = self.tone('a.wav', 20), Growing()
        info = self.convert(audio, self.root / 'out', piece.add, output_name='piece')
        self.assertTrue(Path(info['files']['midi']).is_file())
        self.assertEqual(Path(info['files']['midi']).name, 'piece.mid')
        self.assertAlmostEqual(info['audio_duration_s'], 20.0, delta=0.1)
        self.assertTrue(piece.whole)
        self.assertAlmostEqual(piece.length, 20.0, delta=0.1)
        file = [m for m in mido.MidiFile(info['files']['midi']) if not m.is_meta and m.type != 'program_change']
        made = list(piece.messages(threading.Event()))
        self.assertEqual(made, file, 'Its parts, as they came, are its file, message for message')
        self.assertGreater(len(made), 40)


def midi_of(notes, controls=()):
    midi = pretty_midi.PrettyMIDI(resolution=960, initial_tempo=120.0)
    piano = pretty_midi.Instrument(program=0, name='Piano')
    piano.notes = [pretty_midi.Note(velocity, pitch, start, end) for start, end, pitch, velocity in notes]
    piano.control_changes = [pretty_midi.ControlChange(number, value, at) for at, number, value in controls]
    midi.instruments.append(piano)
    return midi


if __name__ == '__main__':
    unittest.main()
