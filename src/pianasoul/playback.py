"""The piano as a music app plays it: one piece at a time from a list, in one of three modes.

The list is where the piece was chosen: the library, 收藏, 最近播放, an artist or a playlist; a piece she chose, or
one tapped in a conversation, plays alone (the conversation is not a play queue), and several asked of her at once
play in the order asked (line_up). 顺序 and 随机 play the list once and stop; 单曲循环 plays the piece again and again.
⏮ starts the piece over 3 seconds after it began (where it begins when played from its start: play.begin), and
otherwise goes to the one before; neither button wraps around (her 下一首 does: Assistant.step). When the list is
over, its first piece waits at 0:00 (ended), as an album does.

Chat, voice and the phone all play through the one Playback, so they always agree on what is playing, and on its
volume: set from any of them, kept in the library, and told to the piano before each piece. What plays is kept too
as the service stops (the Mac app quitting, the Mac restarting, a restart for a key): the next start has it paused
where it was heard, its list and mode with it, and ▶ goes on from there.

A piece she gets ready plays while it is made (Downloads): as any other, with how much of it is there so far (ready).

It plays on the output chosen in the app, kept in the library by its unique ID (and name). One that is offline is
waited for, never swapped for another, even of the same name; only when its ID is gone from the Mac altogether (an
app such as Pianoteq may get a new one each time it starts) is the one output with its name taken instead. Until one
is chosen, it is the first named like PIANASOUL_PORT, as for the player.
"""

from __future__ import annotations

import json
import logging
import math
import random
import sqlite3
import threading
import time
from pathlib import Path

from . import play
from .play import GAP_S, Output, Player

log = logging.getLogger(__spec__.name)
MODES = ("order", "one", "shuffle")
RESTART_AFTER_S = 3.0  # ⏮ this long after a piece began starts it over
KEPT = "kept_playback"  # the library setting what plays is kept in from one run to the next (close, resume)
DEFAULT_VOLUME = 80  # percent, until one is chosen: about where MIDI's own default (100 of 127) is
PLAY_S = 30.0  # heard this long, a piece of unknown length counts as played


def _check_mode(mode: str | None) -> None:
    """ValueError for a mode that is not one of MODES (None: as it is)."""
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")


class Playback(Player):
    def __init__(self, lib, port=None, lead: float | None = None):
        super().__init__(port=port, lead=lead)
        self.lib = lib
        self.current_id: int | None = None
        self.ids: list[int] = []  # the list, in the order it plays
        self.unshuffled: list[int] | None = None  # its own order, while 随机 is on
        self.scope: dict = {}  # where the list comes from, e.g. {"kind": "artist", "name": "周杰伦"}
        self.listed = 0  # which list it is: counts the lists chosen (select, show), so that her pieces join only theirs
        self.mode = "order"
        self.ended = False  # the list has played to its end
        self.listeners: list = []  # called after every change, on whichever thread made it
        self._lock = threading.RLock()
        self._run = 0  # counts the pieces started, so a finish that comes too late does nothing
        self._listening: dict | None = None  # the piece being heard: how long, so far (hear)
        self._hearing = threading.Lock()
        self._from_start = False  # the last start was from its beginning (not a seek): another listening of the piece
        self._going_on = False  # a start that goes on with the listening (a seek, ▶ after a pause), from wherever
        self._sounds_at = 0.0  # when the last start is first heard: after the gap before a new piece, and the lead
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

    @property
    def duration(self) -> float:
        """How long the piece playing, paused or last played is, as the library has it now: one being made, as long
        as its recording until it is made."""
        rec = self.lib.get(self.current_id) if self.current_id is not None else None
        return (rec or {}).get("duration_s") or 0.0

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
        comes after a short silence), up to until (the end of the piece, or of what is there of one being made; after
        a pause, where the notes already sent stop sounding). ready: how much of a piece being made is there."""
        with self._lock:
            now, position, duration = time.perf_counter(), self.position, self.duration
            making = self.growing.get(self.current[0]) if self.current else None
            ready = min(making.until, duration or math.inf) if making and not making.whole else None
            if self.state == "playing":
                starts_in = max(0.0, self._t0 + position - now)
                until = max(ready, position) if ready is not None else duration or None
            else:
                tail = max(0.0, self._last_when - now) if self.state == "paused" else 0.0
                position, starts_in, until = max(0.0, position - tail), 0.0, position
            return {"id": self.current_id, "state": self.state, "position": round(position, 2),
                    "starts_in": round(starts_in, 2), "until": until if until is None else round(until, 2),
                    "duration": duration, "ready": ready if ready is None else round(ready, 2), "ended": self.ended,
                    "mode": self.mode, "ids": list(self.ids), "scope": dict(self.scope), "volume": self.volume}

    def changed(self) -> None:
        self._hear()
        for listener in list(self.listeners):
            listener()

    def _hear(self) -> None:
        """With every change: how long the piece has been heard while it played. Heard for half its length (30 s
        when its length is not known) it is a play, counted once in the library (heard). Another piece, or this one
        again from its start, is another listening; a pause, a seek or a lost connection is not."""
        with self._hearing:
            now, h = time.monotonic(), self._listening
            if h and h["since"] is not None:
                h["heard"] += max(0.0, now - h["since"])
            if h and not h["counted"] and h["heard"] >= (h["length"] / 2 if h["length"] else PLAY_S):
                h["counted"] = True
                try:
                    self.lib.heard(h["id"])
                except sqlite3.Error as exc:  # a play not counted is less than the piano stopping for it
                    log.warning("a play of %s not counted: %s", h["id"], exc)
            if self.current_id is None:
                self._listening = None
                return
            if not h or h["id"] != self.current_id or self._from_start:
                h = self._listening = {"id": self.current_id, "length": self.duration, "heard": 0.0, "counted": False}
                self._from_start = False
            h["since"] = max(now, self._sounds_at) if self.state == "playing" else None

    # ---- choosing ---------------------------------------------------------------------------------
    def item(self, rec_id: int) -> tuple[Path, str]:
        rec = self.lib.get(rec_id)
        if not self._there(rec):
            raise FileNotFoundError(f"recording {rec_id} is not in the library")
        return Path(rec["midi_path"]), rec["title"]

    def _there(self, rec: dict | None) -> bool:
        """Whether a record's piece can play: its file is in the library, or it is being made and can play (growing)."""
        return bool(rec) and (rec["file_exists"] or Path(rec["midi_path"]) in self.growing)

    def select(self, rec_id: int, ids: list[int] | None = None, scope: dict | None = None, from_s: float = 0.0,
               mode: str | None = None) -> None:
        """Play rec_id, from the list ids (in their order) that scope names, in mode if given (a playlist's 播放
        is in order, its 随机播放 shuffled). LookupError when the piano is not there; then nothing changes, and the
        piece that was playing goes on."""
        item = self.item(rec_id)
        ids = list(dict.fromkeys(ids or [rec_id]))
        if rec_id not in ids:
            ids.append(rec_id)
        _check_mode(mode)

        def ready():
            self._set_current(rec_id)
            self.mode = mode or self.mode
            self.ids, self.scope, self.unshuffled = ids, dict(scope or {}), None
            self.listed += 1
            log.debug("the list: %d piece(s) from %s, %s", len(ids), self.scope.get("kind", "one choice"), self.mode)
            if self.mode == "shuffle":
                self._shuffle()

        with self._lock:
            self.start([item], from_s, ready)

    def show(self, rec_id: int, ids: list[int] | None = None, scope: dict | None = None) -> None:
        """The list a piece belongs to, for the piece already playing: tapping it in another list changes what
        comes next, not what plays now."""
        with self._lock:
            if rec_id != self.current_id:
                return
            self.ids = list(dict.fromkeys(ids or [rec_id]))
            if rec_id not in self.ids:
                self.ids.append(rec_id)
            self.scope, self.unshuffled = dict(scope or {}), None
            self.listed += 1
            if self.mode == "shuffle":
                self._shuffle()
        self.changed()

    def _set_current(self, rec_id: int) -> None:
        self.current_id = rec_id

    # ---- the transport ----------------------------------------------------------------------------
    def start(self, items, from_s: float = 0.0, on_ready=None) -> None:
        with self._lock:
            super().start(items, from_s, on_ready)

    def _launch(self, items, from_s: float) -> None:
        self._run += 1
        making = self.growing.get(Path(items[0][0]))
        if making and self.changed not in making.listeners:  # the phone sees it come
            making.listeners.append(self.changed)
        super()._launch(items, from_s)
        # From its beginning, or from the gap before it (the next piece, a loop of one), unless it is a seek there.
        self._from_start, self._going_on = from_s <= 0 and not self._going_on, False
        self._sounds_at = time.monotonic() + max(0.0, -from_s) + self.lead
        self.ended = False
        if self.current_id is not None:
            self.lib.played(self.current_id)
        self.changed()

    def waited(self) -> None:
        # On a thread of its own, as after_run's next piece: what is told takes the lock (snapshot), which a start or
        # a pause holds while it waits for the playing thread to end.
        threading.Thread(target=self.changed, daemon=True).start()

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
            elif self.state == "paused":
                self.go_on(self._offset)
            elif self.current is not None:
                self.start([self.current], 0.0)

    def seek(self, seconds: float) -> None:
        with self._lock:
            if self.current is None:
                return
            seconds = max(0.0, min(seconds, self.duration or seconds))
            if self.state == "playing":
                self.go_on(seconds)
                return
            # Stopped (✕, or the list over), what plays next is heard anew: the listening before it ended there.
            self._from_start = self.state == "stopped"
            self.state, self._offset, self.ended = "paused", seconds, False
        self.changed()

    def go_on(self, from_s: float) -> None:
        """The piece on from from_s (0:00 too) as the same listening: a seek, ▶ after a pause, her resume or retry, the
        voice going on after her answer."""
        with self._lock:
            self._going_on = True
            try:
                self.start([self.current], from_s)
            finally:
                self._going_on = False

    def landing(self, direction: int, around: bool = False) -> tuple[int | None, str]:
        """Where ⏮ (direction < 0) or ⏭ lands, without going there: (the piece, "moved"). Past the last piece,
        (None, "ended"); around (her 下一首), its first piece instead, (that piece, "around"), or for a list of this
        piece alone (None, "alone"). Nothing in the player: (None, "")."""
        with self._lock:
            if self.current_id is None:
                return None, ""
            ids = self._live()
            i = ids.index(self.current_id) if self.current_id in ids else -1
            if direction < 0:
                begun = self.position - self.begin(self.current[0], 0.0)  # heard, from where it began
                return (self.current_id if begun > RESTART_AFTER_S or i <= 0 else ids[i - 1]), "moved"
            if 0 <= i < len(ids) - 1:
                return ids[i + 1], "moved"
            if self.mode == "one":
                return self.current_id, "moved"
            if not around:
                return None, "ended"
            return (ids[0], "around") if len(ids) > 1 else (None, "alone")

    def step(self, direction: int) -> None:
        """⏮ (direction < 0) or ⏭ on the phone: each plays what it lands on; past the last piece, the list ends."""
        with self._lock:
            target, how = self.landing(direction)
            if how == "ended":
                self._end_list()
            elif target is not None:
                self.go_to(target)

    def go_to(self, rec_id: int) -> None:
        """Play rec_id, a piece of the list that plays, from its start; the list stays as it is (⏮, ⏭, her 下一首).
        LookupError when the piano is not there: then nothing changes."""
        with self._lock:
            path_title = self.item(rec_id)
            previous = self.current_id
            self._set_current(rec_id)
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
        _check_mode(mode)
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

    def line_up(self, rec_id: int, after: list[int], listed: int | None) -> bool:
        """rec_id joins the list that plays, after the last of the pieces after (those of her request) still to come,
        or else next: if it is still the list listed numbers, and goes on (playing, paused, or from one piece to the
        next). True when it joined; otherwise nothing changes. One in the list already moves there."""
        with self._lock:
            going_on = self.state != "stopped" or self.completed is not None
            if listed != self.listed or self.ended or not going_on or rec_id == self.current_id:
                return False
            ids = [i for i in self.ids if i != rec_id]
            now = ids.index(self.current_id) if self.current_id in ids else len(ids) - 1
            at = 1 + max([now, *(i for i in range(now + 1, len(ids)) if ids[i] in after)])
            self.ids = [*ids[:at], rec_id, *ids[at:]]
            if self.unshuffled is not None:  # its own order too: after the same piece
                rest = [i for i in self.unshuffled if i != rec_id]
                place = rest.index(ids[at - 1]) + 1 if at and ids[at - 1] in rest else len(rest)
                self.unshuffled = [*rest[:place], rec_id, *rest[place:]]
        self.changed()
        return True

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
                self.current, self.current_id, self.ended = None, None, False
        self.changed()

    def _live(self) -> list[int]:
        """The list as it can play: pieces still in the library, with their files or being made (_there)."""
        return [i for i in self.ids if self._there(self.lib.get(i))]

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

    # ---- from one run to the next ---------------------------------------------------------------------
    def close(self) -> None:
        """Stop, the notes already sent finishing, and close the output; what plays is kept for the next start. A
        piece playing is paused first, as for ▶ after any pause: kept where its sound stops, the notes sent ahead
        (a lead) heard once."""
        try:
            with self._lock:
                if self.state == "playing":
                    super().pause()
                self._keep()
        finally:
            super().close()

    def _keep(self) -> None:
        """The piece paused, where it goes on, its list and mode, kept in the library; nothing paused (stopped, the
        list over), nothing is kept."""
        kept = ""
        if self.current_id is not None and self.state == "paused":
            kept = json.dumps({"id": self.current_id, "position": self._offset, "ids": self.ids,
                               "unshuffled": self.unshuffled, "scope": self.scope, "mode": self.mode})
        try:
            self.lib.set_setting(KEPT, kept)
        except sqlite3.Error as exc:  # what plays not kept is less than the piano not stopping
            log.warning("what plays was not kept for the next start: %s", exc)

    def resume(self) -> None:
        """What played when the service last stopped, paused where it was, without a sound (close): once the service
        is up (Core), so that a start that fails takes nothing. Taken once: a service that ends without stopping (a
        crash) brings back nothing from before. A piece no longer in the library brings back nothing; one whose file
        is not there yet (moved, renamed, on a disk not mounted) waits paused, as it would at any time, until the
        folder finds it."""
        saved = self.lib.setting(KEPT, "")
        if not saved:
            return
        self.lib.set_setting(KEPT, "")
        try:
            kept = json.loads(saved)
            rec = self.lib.get(kept["id"])
            _check_mode(kept["mode"])
            position = max(0.0, float(kept["position"]))
        except (ValueError, KeyError, TypeError):
            return
        if rec is None:  # deleted meanwhile
            return
        self._set_current(rec["id"])
        self.current = Path(rec["midi_path"]), rec["title"]
        self.mode, self.scope = kept["mode"], dict(kept["scope"] or {})
        self.ids, self.unshuffled = list(dict.fromkeys(kept["ids"] or [kept["id"]])), kept["unshuffled"]
        self.state, self._offset = "paused", min(position, self.duration or position)
        log.debug("paused where it was when the service stopped: %s at %s", rec["title"], play.clock(self._offset))

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
                self._from_start = True  # waiting to be heard anew (the next piece, or this one once more)
                self.changed()
