"""Imported by every test before pianasoul: the library, data and cache folders are throwaway ones, so that no test
ever reads or writes the real ~/Music/PianaSoul or Application Support. Setting PIANASOUL_DATA also keeps the one-time
move from a checkout's old output/ folder from starting. What goes to the Trash goes to a throwaway folder (TRASH),
not the Mac's."""
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
