"""Imported by every test before pianasoul: the library, data and cache folders are throwaway ones, so that no test
ever reads or writes the real ~/Music/PianaSoul or Application Support. Setting PIANASOUL_DATA also keeps the one-time
move from a checkout's old output/ folder from starting. What goes to the Trash goes to a throwaway folder (TRASH),
not the Mac's, no note ever goes to a real MIDI output (a test that plays gives its own), and the real chat model is
never asked to name pieces (a test that names them gives its own model). A typed reply does not wait."""
import atexit
import os
from pathlib import Path
import shutil
import tempfile

import send2trash

ROOT = Path(tempfile.mkdtemp(prefix='pianasoul-tests-'))
TRASH = ROOT / 'Trash'
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)
for name in ('MUSIC', 'DATA', 'CACHE'):
    os.environ[f'PIANASOUL_{name}'] = str(ROOT / name.lower())


def to_trash(path) -> None:
    TRASH.mkdir(exist_ok=True)
    shutil.move(os.fspath(path), tempfile.mkdtemp(dir=TRASH))


send2trash.send2trash = to_trash


from pianasoul import llm, play  # noqa: E402  (after the places above)


class NoMidi:
    def __init__(self, out):
        raise OSError(f'a test tried to open the real MIDI output "{out.name}"')


REAL_MIDI_OUT = play.CoreMidiOut  # for the one test that plays on an output of its own (test_outputs)
play.CoreMidiOut = NoMidi


def no_model(prompt):
    raise OSError('a test tried to ask the real chat model')


REAL_COMPLETE_JSON = llm.complete_json  # for the tests of what it sends, to a made-up server (test_llm)
llm.complete_json = no_model

from pianasoul import chat  # noqa: E402
chat.TYPED_REPLY_S = chat.TYPED_PLAY_S = 0  # a typed reply's pauses are not waited for (a test of them sets its own)
