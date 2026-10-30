"""The library folder and the library, kept in step: what is put in the folder (in Finder, by the phone, by
PianaSoul) comes into the library, and what moves is followed.

A MIDI file becomes a piece; a recording (mp3 and the like) becomes an item to transcribe (Library.audio). A file
that is gone stays in the library, out of sight, with everything about it: when it comes back to where it was, or
turns up elsewhere with the same content (moved or renamed, by its fingerprint), it is the same piece again. Only
deleting a piece on the phone takes it out of the library; a scan never does.

The folder is scanned at start and, while PianaSoul runs, a second after anything in it changes (FSEvents, through
watchdog). A new file is taken in once it has kept its size for that second, so that one still being copied is not
read halfway. Hidden files, downloads in progress, and files whose content is still in iCloud are passed over until
they are whole and there.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .audio import AUDIO_EXTENSIONS
from .library import MIDI_TYPES, Library, fingerprint, present, stamp

log = logging.getLogger(__spec__.name)

SETTLE_S = 1.0  # a change is looked at this long after the last one, and a new file must keep its size as long
PARTIAL = {".download", ".crdownload", ".part"}  # downloads in progress


def files(folder: Path):
    """Every music file in the folder and its subfolders, but hidden ones and downloads in progress."""
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            path = Path(root) / name
            if name.startswith(".") or path.suffix.lower() in PARTIAL:
                continue
            if path.suffix.lower() in MIDI_TYPES | AUDIO_EXTENSIONS:
                yield path


class Folder:
    def __init__(self, lib: Library, on_change=None):
        """on_change() is called after a scan that changed the library, on the scanning thread."""
        self.lib, self.on_change = lib, on_change
        self._seen: dict[str, str] = {}  # new files waiting to settle: their stamp when last seen
        self._unreadable: dict[str, str] = {}  # files that are not MIDI after all, by stamp, not read again
        self._lock = threading.Lock()  # one scan at a time
        self._timer: threading.Timer | None = None
        self._observer = None

    def scan(self) -> bool:
        """Bring the library up to date with the folder. True when some new file is still settling: scan again in
        SETTLE_S."""
        with self._lock:
            folder = self.lib.music_dir
            try:
                folder.mkdir(exist_ok=True)  # the default one is made; one on a disk that is not there is not
            except OSError as exc:
                log.debug("the library folder is not there: %s", exc)
                return False
            pieces = {r["midi_path"]: r for r in self.lib.all()}
            recordings = {a["path"]: a for a in self.lib.audio()}
            changed, settling, seen = False, False, {}
            for path in files(folder):
                key = str(path.resolve())
                if not present(path):  # in iCloud only, or gone since it was listed
                    continue
                now = stamp(path)
                known = pieces.get(key) or recordings.get(key)
                if known:
                    if known["stamp"] != now:  # changed in place
                        changed |= self._refresh(path, known)
                    continue
                if self._unreadable.get(key) == now:
                    continue
                if self._seen.get(key) != now:  # new: taken in once it has kept its size for SETTLE_S
                    seen[key], settling = now, True
                    continue
                changed |= self._take_in(path)
            self._seen = seen
        if changed and self.on_change:
            self.on_change()
        return settling

    def _take_in(self, path: Path) -> bool:
        """A new file: a piece or a recording moved or renamed (the same content), or a new one."""
        key = str(path.resolve())
        try:
            content = fingerprint(path)
            if path.suffix.lower() in MIDI_TYPES:
                moved = self.lib.moved(content)
                if moved:
                    self.lib.move(moved["id"], path)
                    log.debug("moved: %s -> %s", Path(moved["midi_path"]).name, path.name)
                else:
                    self.lib.add(path, path.stem, source_type="folder")
                    log.debug("new piece: %s", path.name)
            else:
                moved = self.lib.moved_audio(content)
                if moved:
                    self.lib.move_audio(moved["id"], path)
                    log.debug("moved: %s -> %s", Path(moved["path"]).name, path.name)
                else:
                    self.lib.add_audio(path)
                    log.debug("new recording to transcribe: %s", path.name)
        except Exception as exc:  # not a MIDI file after all, or gone meanwhile
            log.debug("passed over %s: %s", path.name, exc)
            if present(path):
                self._unreadable[key] = stamp(path)
            return False
        return True

    def _refresh(self, path: Path, known: dict) -> bool:
        try:
            if path.suffix.lower() in MIDI_TYPES:
                self.lib.refresh(known["id"])
            else:
                self.lib.refresh_audio(known["id"])
        except Exception as exc:
            log.debug("could not read %s again: %s", path.name, exc)
            return False
        return True

    # ---- while PianaSoul runs --------------------------------------------------------------------
    def watch(self) -> None:
        """Scan now, and a second after each change in the folder from now on."""
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        folder = self

        class Changes(FileSystemEventHandler):
            def on_any_event(self, event):
                folder.soon()

        if self.scan():
            self.soon()
        if not self.lib.music_dir.is_dir():
            return
        self._observer = Observer()
        self._observer.schedule(Changes(), str(self.lib.music_dir), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def soon(self) -> None:
        """Scan once things have been quiet for SETTLE_S (each change starts the wait again)."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(SETTLE_S, self._again)
            self._timer.daemon = True
            self._timer.start()

    def _again(self) -> None:
        if self.scan():
            self.soon()

    def close(self) -> None:
        if self._observer:
            self._observer.stop()
        with self._lock:
            if self._timer:
                self._timer.cancel()
