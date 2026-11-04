"""The transcription engine's devices: a piece the GPU fails is transcribed again on the CPU (no model is loaded);
and a piece transcribed in a process of its own (pipeline.convert_apart), with the real model once."""
import contextlib
import io
import tempfile
import threading
import time
import unittest
import wave
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

import numpy as np

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import pipeline
from pianasoul.engine import TranskunEngine


def engine(device):
    e = TranskunEngine.__new__(TranskunEngine)  # without loading the model
    e.device, e._lock = device, threading.Lock()
    return e


class EngineTests(unittest.TestCase):
    def test_a_piece_the_gpu_fails_is_transcribed_again_on_the_cpu_and_said(self):
        e = engine('mps')
        tried = []
        def transcribe(audio, device):
            tried.append(device)
            if device == 'mps' and len(tried) == 1:
                raise RuntimeError('MPS backend out of memory')
            return f'midi from {device}'
        said = io.StringIO()
        with patch.object(e, '_transcribe', side_effect=transcribe), contextlib.redirect_stdout(said):
            self.assertEqual(e.transcribe('audio'), ('midi from cpu', 'cpu'))
            self.assertEqual(e.transcribe('audio'), ('midi from mps', 'mps'), 'The next piece on the GPU again')
        self.assertEqual(tried, ['mps', 'cpu', 'mps'])
        self.assertIn('GPU failed: RuntimeError: MPS backend out of memory', said.getvalue())

    def test_on_the_cpu_a_failure_is_the_pieces(self):
        e = engine('cpu')
        with patch.object(e, '_transcribe', side_effect=RuntimeError('bad audio')):
            with self.assertRaises(RuntimeError):
                e.transcribe('audio')


class ApartTests(unittest.TestCase):
    """convert_apart, with a stand-in for the process (a shell script in Python's place) or with the real model."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def child(self, script):
        fake = self.root / 'python'
        fake.write_text('#!/bin/sh\n' + script)
        fake.chmod(0o755)
        return patch.object(pipeline.sys, 'executable', str(fake))

    def test_its_info_is_its_last_line_and_what_it_said_before_is_passed_on(self):
        said = io.StringIO()
        with self.child('echo "transcription on the GPU failed; this piece again on the CPU"\n'
                        'echo \'{"device": "cpu", "files": {"midi": "piece.mid"}}\'\n'), contextlib.redirect_stdout(said):
            info = pipeline.convert_apart('a.m4a', self.root, pipeline.Options(), output_name='piece')
        self.assertEqual(info, {'device': 'cpu', 'files': {'midi': 'piece.mid'}})
        self.assertIn('again on the CPU', said.getvalue())

    def test_its_failure_is_said_whole(self):
        with self.child('echo "could not decode audio: [mp3] Failed to find two frames" >&2\necho "Invalid data" >&2\n'
                        'exit 1\n'), self.assertRaisesRegex(RuntimeError, '^could not decode audio: .+\nInvalid data$'):
            pipeline.convert_apart('a.m4a', self.root, pipeline.Options())

    def test_stopped_it_ends_at_once(self):
        stopped = threading.Event()
        threading.Timer(0.3, stopped.set).start()
        started = time.monotonic()
        with self.child('exec sleep 30\n'), self.assertRaises(InterruptedError):
            pipeline.convert_apart('a.m4a', self.root, pipeline.Options(), stopped=stopped)
        self.assertLess(time.monotonic() - started, 3)

    @unittest.skipUnless(find_spec('transkun'), 'the advanced extra is not installed')
    def test_its_error_is_its_own_and_it_ends_when_the_program_that_started_it_does(self):
        with self.assertRaisesRegex(RuntimeError, '^could not decode audio'):
            pipeline.convert_apart(self.root / 'missing.mp3', self.root / 'out', pipeline.Options())
        import subprocess
        import sys
        long = self.tone('long.wav', 120)  # some ten seconds to transcribe
        self.addCleanup(subprocess.run, ['pkill', '-f', str(long)])
        starter = subprocess.Popen([sys.executable, '-c', 'import subprocess, sys, time; subprocess.Popen([sys.executable, '
                                    '"-I", "-B", "-m", "pianasoul.pipeline", sys.argv[1], sys.argv[2], "{}", ""]); time.sleep(1)',
                                    str(long), str(self.root / 'out')])
        starter.wait()
        time.sleep(3)  # the starter gone: its child sees it within a second
        left = subprocess.run(['pgrep', '-f', str(long)], capture_output=True, text=True).stdout
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

    @unittest.skipUnless(find_spec('transkun'), 'the advanced extra is not installed')
    def test_the_real_model_in_its_own_process(self):
        audio = self.tone('a.wav', 2)
        info = pipeline.convert_apart(audio, self.root / 'out', pipeline.Options(), output_name='piece')
        self.assertTrue(Path(info['files']['midi']).is_file())
        self.assertEqual(Path(info['files']['midi']).name, 'piece.mid')
        self.assertAlmostEqual(info['audio_duration_s'], 2.0, delta=0.1)


if __name__ == '__main__':
    unittest.main()
