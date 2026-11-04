"""Audio file -> raw MIDI -> cleaned MIDI + info.json; in this process (convert_file), or in one of its own
(convert_apart, which runs this module)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pretty_midi

from .audio import load_audio
from .cleanup import SUSTAIN, clean, pedal_intervals

log = logging.getLogger(__spec__.name)
EXTRAS_DIR = "extras"


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


def transcribe_audio(engine, audio_path, options: Options):
    """Run the model and clean-up. Returns (raw_midi, clean_midi, info)."""
    t0 = time.time()
    audio = load_audio(audio_path, engine.sample_rate)
    t1 = time.time()
    raw, device = engine.transcribe(audio)
    t2 = time.time()
    cleaned, report = clean(raw, keep_soft_pedal=options.keep_soft_pedal,
                            legato=options.legato, pedal_ramps=options.pedal_ramps)
    info = {
        "source": Path(audio_path).name,
        "engine": engine.version,
        "device": device,
        "audio_duration_s": round(len(audio) / engine.sample_rate, 3),
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


def convert_apart(audio_path, out_dir, options: Options, *, output_name: str | None = None,
                  stopped: threading.Event | None = None) -> dict:
    """convert_file in a process of its own, which ends with the piece (the model's memory with it): its loading and
    its work then never hold this process's interpreter, where the thread that sends the piano its notes must run on
    time (in the service's own process, a transcription held it for seconds at a time and the piece playing
    stalled). What it says (the GPU failing over to the CPU, say) is passed on. InterruptedError once stopped is
    set: the process is ended at once; it also ends by itself when this one does. RuntimeError with its error when it
    fails."""
    child = subprocess.Popen([sys.executable, "-I", "-B", "-m", __spec__.name, str(audio_path), str(out_dir),
                              json.dumps(vars(options)), output_name or ""],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            out, err = child.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if stopped is not None and stopped.is_set():
                child.kill()
                child.communicate()
                raise InterruptedError("stopped") from None
    lines = out.splitlines()
    done = child.returncode == 0 and bool(lines)
    for line in lines[:-1] if done else lines:
        print(line, flush=True)
    if not done:
        raise RuntimeError(err.strip() or f"the transcription ended with {child.returncode}")
    return json.loads(lines[-1])


def convert_file(engine, audio_path, out_dir, options: Options, overwrite: bool = False,
                 *, output_name: str | None = None) -> dict:
    audio_path, out_dir = Path(audio_path), Path(out_dir)
    if output_name is not None and (not output_name or output_name in {".", ".."}
                                   or "/" in output_name or "\\" in output_name):
        raise ValueError("output_name must be a filename stem")
    raw, cleaned, info = transcribe_audio(engine, audio_path, options)
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


if __name__ == "__main__":  # convert_apart's process: one piece, then its info as the last line
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

        audio_path, out_dir, options, name = sys.argv[1:5]
        made = convert_file(TranskunEngine(), audio_path, out_dir, Options(**json.loads(options)),
                            output_name=name or None)
    except Exception as exc:
        sys.exit(str(exc))  # its error, whole, for convert_apart to say
    print(json.dumps(made), flush=True)
