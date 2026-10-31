"""Transkun piano transcription engine (loaded once, reused for every file)."""

from __future__ import annotations

import threading
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

    def transcribe(self, audio) -> tuple[pretty_midi.PrettyMIDI, str]:
        """audio: float32 numpy array (samples, channels) at self.sample_rate. Returns the MIDI and the device that
        made it: when the GPU (mps) fails, this piece is transcribed again on the CPU, and the next one is tried on
        the GPU again (the model goes to the device each piece is transcribed on)."""
        with self._lock:
            try:
                return self._transcribe(audio, self.device), self.device
            except Exception as exc:
                if self.device != "mps":
                    raise
                print(f"  (transcription on the GPU failed: {type(exc).__name__}: {exc}; this piece again on the CPU)",
                      flush=True)
                return self._transcribe(audio, "cpu"), "cpu"

    def _transcribe(self, audio, device: str) -> pretty_midi.PrettyMIDI:
        import torch
        from transkun.Data import writeMidi

        self.model.to(device)
        with torch.no_grad():
            x = torch.from_numpy(audio).to(device)
            notes = self.model.transcribe(x, discardSecondHalf=False)
        return writeMidi(notes)
