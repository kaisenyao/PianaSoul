"""Imported by every test before pianasoul: the library, data and cache folders are throwaway ones, so that no test
ever reads or writes the real ~/Music/PianaSoul or Application Support. Setting PIANASOUL_DATA also keeps the one-time
move from a checkout's old output/ folder from starting."""
import atexit
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(tempfile.mkdtemp(prefix='pianasoul-tests-'))
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)
for name in ('MUSIC', 'DATA', 'CACHE'):
    os.environ[f'PIANASOUL_{name}'] = str(ROOT / name.lower())
