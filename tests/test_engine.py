"""The transcription engine's devices: a piece the GPU fails is transcribed again on the CPU; no model is loaded."""
import contextlib
import io
import threading
import unittest
from unittest.mock import patch

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
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


if __name__ == '__main__':
    unittest.main()
