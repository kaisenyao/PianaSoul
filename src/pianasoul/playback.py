"""The piano as a music app plays it: one piece at a time from a list, in one of three modes.

The list is where the piece was chosen: the library, 收藏, 最近播放, an artist, or a conversation (the pieces she
played in it). 顺序 and 随机 play the list once and stop; 单曲循环 plays the piece again and again. ⏮ starts the
piece over after its first 3 seconds, and otherwise goes to the one before; neither button wraps around. When the
list is over, its first piece waits at 0:00 (ended), as an album does.

Chat, voice and the phone all play through the one Playback, so they always agree on what is playing, and on its
volume: set from any of them, kept in the library, and told to the piano before each piece.

It plays on the output chosen in the app, kept in the library by its unique ID (and name). One that is offline is
waited for, never swapped for another, even of the same name; only when its ID is gone from the Mac altogether (an
app such as Pianoteq may get a new one each time it starts) is the one output with its name taken instead. Until one
is chosen, it is the first named like PIANASOUL_PORT, as for the player.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from pathlib import Path

from . import play
from .play import GAP_S, Output, Player

log = logging.getLogger(__spec__.name)
MODES = ("order", "one", "shuffle")
RESTART_AFTER_S = 3.0  # ⏮ past this point starts the piece over
DEFAULT_VOLUME = 80  # percent, until one is chosen: about where MIDI's own default (100 of 127) is


class Playback(Player):
    def __init__(self, lib, port=None, lead: float | None = None):
        super().__init__(port=port, lead=lead)
        self.lib = lib
        self.current_id: int | None = None
        self.duration = 0.0
        self.ids: list[int] = []  # the list, in the order it plays
        self.unshuffled: list[int] | None = None  # its own order, while 随机 is on
        self.scope: dict = {}  # where the list comes from, e.g. {"kind": "artist", "name": "周杰伦"}
        self.mode = "order"
        self.ended = False  # the list has played to its end
        self.listeners: list = []  # called after every change, on whichever thread made it
        self._lock = threading.RLock()
        self._run = 0  # counts the pieces started, so a finish that comes too late does nothing
        saved = lib.setting("volume", "")
        self.volume = min(100, int(saved)) if saved.isdigit() else DEFAULT_VOLUME  # kept from one run to the next

    @property
    def current(self) -> tuple[Path, str] | None:
        """The piece playing, paused or last played, as the library has it now: renamed meanwhile (its file takes
        the new name), it goes on from its file under that name."""
        rec = self.lib.get(self.current_id) if self.current_id is not None else None
        return (Path(rec["midi_path"]), rec["title"]) if rec else self._current

    @current.setter
    def current(self, item: tuple[Path, str] | None) -> None:
        self._current = item

    # ---- where it plays ----------------------------------------------------------------------------
    def chosen(self) -> dict | None:
        """The output chosen in the app: {"id", "name", "kind"}, or None until one is."""
        saved = self.lib.setting("output", "")
        return json.loads(saved) if saved else None

    def target(self) -> Output | None:
        chosen = self.chosen()
        if chosen is None:
            return super().target()
        found = play.outputs()
        out = next((o for o in found if o.id == chosen["id"]), None)
        if out is None:
            named = [o for o in found if o.name == chosen["name"]]
            if len(named) == 1 and play.output(chosen["id"]) is None:  # its ID is gone: the one of its name is it
                out = named[0]
                self.keep(out)
                log.debug("the output %s is back with a new ID", out.name)
        return out

    def missing(self) -> str:
        chosen = self.chosen()
        return f'"{chosen["name"]}" is not there' if chosen else super().missing()

    def test_tone(self) -> None:
        with self._lock:  # not while a piece is being started or the output changed
            super().test_tone()

    def choose(self, out: Output) -> None:
        """Play on out from now on: a piece playing pauses where it is, and goes on there when played again."""
        with self._lock:
            self.keep(out)
            self.switch()
        log.debug("plays on %s from now on", out.name)
        self.changed()

    def keep(self, out: Output) -> None:
        self.lib.set_setting("output", json.dumps({"id": out.id, "name": out.name, "kind": out.kind}))

    # ---- what the phone shows -------------------------------------------------------------------
    def snapshot(self) -> dict:
        """As it is heard: position moves on after starts_in seconds (the notes leave a lead early, and a next piece
        comes after a short silence), up to until (the end of the piece; after a pause, where the notes already
        sent stop sounding)."""
        with self._lock:
            now, position = time.perf_counter(), self.position
            if self.state == "playing":
                starts_in, until = max(0.0, self._t0 + position - now), self.duration or None
            else:
                tail = max(0.0, self._last_when - now) if self.state == "paused" else 0.0
                position, starts_in, until = max(0.0, position - tail), 0.0, position
            return {"id": self.current_id, "state": self.state, "position": round(position, 2),
                    "starts_in": round(starts_in, 2), "until": until if until is None else round(until, 2),
                    "duration": self.duration, "ended": self.ended,
                    "mode": self.mode, "ids": list(self.ids), "scope": dict(self.scope), "volume": self.volume}

    def changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    # ---- choosing ---------------------------------------------------------------------------------
    def item(self, rec_id: int) -> tuple[Path, str]:
        rec = self.lib.get(rec_id)
        if not rec or not rec["file_exists"]:
            raise FileNotFoundError(f"recording {rec_id} is not in the library")
        return Path(rec["midi_path"]), rec["title"]

    def select(self, rec_id: int, ids: list[int] | None = None, scope: dict | None = None, from_s: float = 0.0,
               mode: str | None = None) -> None:
        """Play rec_id, from the list ids (in their order) that scope names, in mode if given (a playlist's 播放
        is in order, its 随机播放 shuffled). LookupError when the piano is not there; then nothing changes, and the
        piece that was playing goes on."""
        item = self.item(rec_id)
        ids = list(dict.fromkeys(ids or [rec_id]))
        if rec_id not in ids:
            ids.append(rec_id)
        if mode is not None and mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")

        def ready():
            self._set_current(rec_id)
            self.mode = mode or self.mode
            self.ids, self.scope, self.unshuffled = ids, dict(scope or {}), None
            log.debug("the list: %d piece(s) from %s, %s", len(ids), self.scope.get("kind", "one choice"), self.mode)
            if self.mode == "shuffle":
                self._shuffle()

        with self._lock:
            self.start([item], from_s, ready)

    def show(self, rec_id: int, ids: list[int] | None = None, scope: dict | None = None,
             mode: str | None = None) -> None:
        """The list a piece belongs to, for the piece already playing: tapping it in another list changes what
        comes next, not what plays now."""
        if mode is not None and mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        with self._lock:
            if rec_id != self.current_id:
                return
            self.mode = mode or self.mode
            self.ids = list(dict.fromkeys(ids or [rec_id]))
            if rec_id not in self.ids:
                self.ids.append(rec_id)
            self.scope, self.unshuffled = dict(scope or {}), None
            if self.mode == "shuffle":
                self._shuffle()
        self.changed()

    def _set_current(self, rec_id: int) -> None:
        rec = self.lib.get(rec_id)
        self.current_id, self.duration = rec_id, (rec or {}).get("duration_s") or 0.0

    # ---- the transport ----------------------------------------------------------------------------
    def start(self, items, from_s: float = 0.0, on_ready=None) -> None:
        with self._lock:
            super().start(items, from_s, on_ready)

    def _launch(self, items, from_s: float) -> None:
        self._run += 1
        super()._launch(items, from_s)
        self.ended = False
        if self.current_id is not None:
            self.lib.played(self.current_id)
        self.changed()

    def pause(self) -> None:
        with self._lock:
            super().pause()
        self.changed()

    def stop(self) -> None:
        with self._lock:
            super().stop()
        self.changed()

    def toggle(self) -> None:
        """▶ or ⏸. After the end of the list, ▶ plays it again from its first piece."""
        with self._lock:
            if self.state == "playing":
                self.pause()
            elif self.current is not None:
                self.start([self.current], self._offset if self.state == "paused" else 0.0)

    def seek(self, seconds: float) -> None:
        with self._lock:
            if self.current is None:
                return
            seconds = max(0.0, min(seconds, self.duration or seconds))
            if self.state == "playing":
                self.start([self.current], seconds)
                return
            self.state, self._offset, self.ended = "paused", seconds, False
        self.changed()

    def step(self, direction: int) -> None:
        """⏮ (direction < 0) or ⏭; each plays what it lands on."""
        with self._lock:
            if self.current_id is None:
                return
            ids = self._live()
            i = ids.index(self.current_id) if self.current_id in ids else -1
            if direction < 0:
                target = self.current_id if self.position > RESTART_AFTER_S or i <= 0 else ids[i - 1]
            elif 0 <= i < len(ids) - 1:
                target = ids[i + 1]
            elif self.mode == "one":
                target = self.current_id
            else:
                self._end_list()
                return
            path_title = self.item(target)
            previous = self.current_id
            self._set_current(target)
            try:
                self.start([path_title], 0.0)
            except LookupError:
                self._set_current(previous)
                raise

    def set_volume(self, percent: float) -> None:
        with self._lock:
            super().set_volume(percent)
            self.lib.set_setting("volume", str(self.volume))
        self.changed()

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        with self._lock:
            if mode == "shuffle" and self.mode != "shuffle":
                self.mode = mode
                self.unshuffled = list(self.ids)
                self._shuffle()
            elif mode != "shuffle" and self.mode == "shuffle":
                self.mode = mode
                if self.unshuffled is not None:
                    self.ids = [i for i in self.unshuffled if i in self.ids]
                self.unshuffled = None
            else:
                self.mode = mode
        self.changed()

    def _shuffle(self) -> None:
        """The playing piece first, the rest in a random order."""
        if self.unshuffled is None:
            self.unshuffled = list(self.ids)
        rest = [i for i in self.ids if i != self.current_id]
        random.shuffle(rest)
        self.ids = ([self.current_id] if self.current_id in self.ids else []) + rest

    # ---- editing the list ---------------------------------------------------------------------------
    def remove(self, rec_id: int) -> None:
        """Take a piece out of the list; the playing one stays."""
        with self._lock:
            if rec_id == self.current_id:
                return
            self.ids = [i for i in self.ids if i != rec_id]
            if self.unshuffled is not None:
                self.unshuffled = [i for i in self.unshuffled if i != rec_id]
        self.changed()

    def move(self, rec_id: int, index: int) -> None:
        """Put a piece at index in the list."""
        with self._lock:
            if rec_id not in self.ids:
                return
            ids = [i for i in self.ids if i != rec_id]
            ids.insert(max(0, min(index, len(ids))), rec_id)
            self.ids = ids
        self.changed()

    def forget(self, rec_id: int) -> None:
        """A recording left the library: out of the list, and if it was the one playing, nothing plays."""
        with self._lock:
            self.ids = [i for i in self.ids if i != rec_id]
            if self.unshuffled is not None:
                self.unshuffled = [i for i in self.unshuffled if i != rec_id]
            if rec_id == self.current_id:
                super().stop()
                self.current, self.current_id, self.duration, self.ended = None, None, 0.0, False
        self.changed()

    def _live(self) -> list[int]:
        """The list as it can play: pieces still in the library, with their files."""
        return [i for i in self.ids if (rec := self.lib.get(i)) and rec["file_exists"]]

    def _end_list(self) -> None:
        """The list is over: nothing plays, and its first piece waits at 0:00."""
        log.debug("the list is over")
        super().stop()
        first = next(iter(self._live()), self.current_id)
        if first is not None and first != self.current_id:
            try:
                self.current = self.item(first)
                self._set_current(first)
            except FileNotFoundError:
                pass
        self.ended = True
        self.changed()

    # ---- the end of a piece -------------------------------------------------------------------------
    def after_run(self) -> None:
        """The playing thread is done. After a natural finish the next piece follows on a thread of its own,
        so that a pause or a new choice made meanwhile, which waits for this thread, wins."""
        run = self._run
        if self.completed is not None:
            threading.Thread(target=self._next, args=(run,), daemon=True).start()
        elif self.lost:
            self.changed()

    def _next(self, run: int) -> None:
        with self._lock:
            if run != self._run or self.completed is None or self.state != "stopped":
                return  # something else was started, or stopped, meanwhile
            ids = self._live()
            i = ids.index(self.current_id) if self.current_id in ids else -1
            target = self.current_id if self.mode == "one" else ids[i + 1] if 0 <= i < len(ids) - 1 else None
            if target is None:
                completed = self.completed
                self._end_list()
                self.completed = completed  # she may say a word about the end
                return
            try:
                item = self.item(target)
                self._set_current(target)
                self.start([item], -GAP_S)  # after a short silence
            except FileNotFoundError:
                self._end_list()
            except LookupError as exc:  # the piano went away between two pieces: the next one waits
                self.current, self.state, self._offset, self.lost = item, "paused", 0.0, str(exc)
                self.changed()
