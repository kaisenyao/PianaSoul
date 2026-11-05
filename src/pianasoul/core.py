"""PianaSoul while it runs on the Mac: one library, one piano, one assistant with her conversations, and one queue
of new pieces. The terminal chat, the Mac's voice and the phone app are ways in; each runs one Core, so whatever
one of them does, the others see.

Without her (no chat model: the advanced extra or a key is missing) the library, the piano and the queue work all
the same; what needs her is not there (assistant is None).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from . import online, play
from .chat import Assistant, Worker, instructions
from .conversations import Conversations
from .downloads import Downloads
from .folder import Folder
from .library import ALIASES, NAMES, Library, enrich, known_artists, title_parts
from .play import clock
from .playback import Playback

log = logging.getLogger(__spec__.name)
ROOT = Path(__file__).resolve().parents[2]
PIANO_LOOK_S = 3.0  # how often a network session's participants are looked at: macOS does not say when one leaves
PIANO_SAFETY_S = 15.0  # and any other output's, besides what CoreMIDI tells
REPO = "https://github.com/kaisenyao/PianaSoul"


class Core:
    def __init__(self, note: str = "", lib: Library | None = None, chat=None, assistant: bool = True):
        """note: what the chat model is told besides her instructions (voice says that replies are spoken).
        chat: (chat, label) to use instead of the configured model. assistant=False: without her."""
        self.lib = lib or Library()
        self.playback = Playback(self.lib)
        self.conversations = Conversations(self.lib)  # kept without her too: a deleted piece leaves them
        for rec in self.lib.all():  # being made when the service last stopped, and so not made: nothing of it stays
            if rec["making"]:
                self.lib.delete(rec["id"])
                self.conversations.forget(rec["id"])
        self.downloads = Downloads(self.lib, name_piece=name_piece if assistant else None)
        self.playback.growing = self.downloads.growing  # a piece plays as it is made
        self.downloads.unmade.append(self._unmade)
        self.listeners: list = []  # called with each event, on whichever thread it happens
        self.busy = None  # the conversation she is answering in, while she answers
        self.with_you = False  # voice: from her name heard until she waits for it again (Assistant.with_you)
        self.assistant = self.chat = None
        self.label = ""
        if assistant:
            from .llm import make_chat

            online = transcription_on()
            self.assistant = Assistant(self.lib, self.playback, self.conversations, self.downloads, online=online)
            self.chat, self.label = chat or make_chat(instructions(online) + note, self.assistant.tools)
            self.assistant.restore(self.chat)
            self.assistant.on_message = lambda message: self.emit({"type": "message", "message": message})
            self.assistant.on_busy = self._busy_changed
            self.assistant.on_with_you = self._with_you_changed
            self.assistant.on_playlists = self.playlists_changed
            self.worker = Worker()  # typed requests, in order
        self.playback.listeners.append(self._playback_changed)
        self.downloads.listeners.append(self._downloads_changed)
        self._jobs: list = []  # each job, and its piece's record once it plays as it is made: as the app last read it
        self._audio: list[dict] = []  # as the app was last told
        self._name = threading.Event()  # pieces may need their other names (with her: at the start, and as they come)
        self.folder = Folder(self.lib, self._folder_changed)
        self.folder.watch()
        self._transcribe_waiting()
        # The piano, watched all along: the page shows when it is not connected, without waiting for a piece to fail.
        self.piano = self._piano_now()
        self._piano_lock, self._look, self._closed = threading.Lock(), threading.Event(), False
        play.on_change(self._look.set)
        threading.Thread(target=self._watch_piano, daemon=True).start()
        self._playing = None
        self._told = False  # the piano's going away, while the piece waits for it
        threading.Thread(target=self._fill_in_videos, daemon=True).start()
        if self.assistant:
            self._name.set()
            threading.Thread(target=self._name_pieces, daemon=True).start()
        self.playback.resume()  # last: a Core that failed to start has taken nothing

    def emit(self, event: dict) -> None:
        for listener in list(self.listeners):
            listener(event)

    def _busy_changed(self, busy: bool) -> None:
        self.busy = self.assistant.conversation_id if busy else None
        self.emit({"type": "busy", "conversation": self.busy})

    def _with_you_changed(self, on: bool) -> None:
        self.with_you = on
        self.emit({"type": "with_you", "with_you": on})

    def _playback_changed(self) -> None:
        self.look_at_piano()  # it may have gone away, or a piece just started on it
        snapshot = self.playback.snapshot()
        self.emit({"type": "playback", **snapshot})
        lost = self.playback.lost
        if lost and not self._told:  # the piano went away while playing: she tells you in the conversation, once,
            self._told = True  # and says it too if she is on (voice.Conversation.remark); the phone shows it
            print(f"  ⏸ {self.playback.current[1]}  {clock(self.playback.position)}  ({lost})", flush=True)
            if self.assistant:
                self.assistant.piano_news({"error": "playback_connection_lost"})
        elif not lost:
            self._told = False
        if snapshot["state"] != "playing":
            self._playing = None
        elif snapshot["id"] != self._playing:  # it is in 最近播放 now, and a piece she meant to retry is old news
            self._playing = snapshot["id"]
            if self.assistant:
                self.assistant.pending = None
            self.library_changed()

    def _fill_in_videos(self) -> None:
        """Pieces downloaded before the library kept their videos' details get them, in the background, one at a
        time (a second or two each, as online search reads a video's page); with online search only."""
        if not transcription_on():
            return
        for r in [r for r in self.lib.all() if not r["video_title"] and online.video_id(r["source_url"])]:
            if self._closed:
                return
            try:
                found = online.details(r["source_url"])
            except online.OnlineError:  # removed, private, or out of reach: left as it is
                continue
            self.lib.set_video(r["id"], found)
            self.library_changed()

    # ---- the piano -----------------------------------------------------------------------------------
    def _piano_now(self) -> dict:
        """The output chosen (or found by name), as the page shows it: its name, its kind and whether it is
        connected; the one chosen, not connected, when it is not there at all."""
        out = self.playback.target()
        if out is None:
            chosen = self.playback.chosen() or {}
            return {"name": chosen.get("name") or self.playback.wanted, "kind": chosen.get("kind", "other"),
                    "connected": False}
        return {"name": out.name, "kind": out.kind, "connected": out.connected}

    def look_at_piano(self) -> None:
        """Look at the piano now (CoreMIDI said something changed, a piece could not play, another was chosen)."""
        self._look.set()

    def _watch_piano(self) -> None:
        while not self._closed:
            self._look.wait(PIANO_LOOK_S if self.piano["kind"] == "network" else PIANO_SAFETY_S)
            self._look.clear()
            if self._closed:
                return
            with self._piano_lock:
                now = self._piano_now()
                if now == self.piano:
                    continue
                self.piano = now
            log.debug("the piano: %s %s", now["name"], "connected" if now["connected"] else "not connected")
            self.emit({"type": "piano", "piano": now})

    def _downloads_changed(self) -> None:
        jobs = self.downloads.snapshot()
        self.emit({"type": "jobs", "jobs": jobs})
        pieces = [(j["id"], j["recording"]) for j in jobs]
        if pieces != self._jobs:  # a piece came in, went, or can play as it is made: the library changed
            self._jobs = pieces
            self.library_changed()
            self._name.set()
        self._audio_changed()

    def _unmade(self, rec_id: int) -> None:
        """A piece that played as it was made was not made after all (Downloads took its record out of the library):
        it is gone from the player, and from where she played it."""
        self.playback.forget(rec_id)
        self.conversations.forget(rec_id)
        self.library_changed()
        self.playlists_changed()

    def _name_pieces(self) -> None:
        """With her: each piece without its names in other languages gets them from the chat model, once (enrich), so
        that a request in one language finds a piece titled in another."""
        from .llm import complete_json

        while not self._closed:
            self._name.wait()
            self._name.clear()
            if self._closed:
                return
            try:
                named = enrich(self.lib, complete_json)
            except Exception as exc:  # the model out of reach: tried again when pieces next come in
                print(f"  (pieces not named: {exc})", flush=True)
                continue
            if named:
                log.debug("named %d piece(s) in other languages", named)
                self.library_changed()

    def _folder_changed(self) -> None:
        """Something came into the library folder, moved in it, or changed."""
        self.library_changed()
        self._audio_changed()
        self._transcribe_waiting()
        self._name.set()

    def _audio_changed(self) -> None:
        audio = self.audio()
        if audio != self._audio:
            self._audio = audio
            self.emit({"type": "audio", "audio": audio})

    def _transcribe_waiting(self) -> None:
        """Each recording in the library folder is transcribed once on its own: when it is first seen, or when this
        Mac can first transcribe. After that, only when asked (transcribe)."""
        if not self.features()["transcribe"]:
            return
        for item in self.lib.audio():
            if item["status"] == "waiting" and self.lib.here(Path(item["path"])):
                self.downloads.add(item["path"], auto=True)

    # ---- what the app shows -----------------------------------------------------------------------
    def features(self) -> dict:
        """What this Mac can do beyond the library and the piano: transcribe, which brings online search by name or
        link (transcription_on), and her."""
        return {"transcribe": transcription_on(), "assistant": self.assistant is not None}

    def recordings(self) -> list[dict]:
        """The pieces in the library, and those being made that play as they are made (making: not in its lists)."""
        return [recording(r) for r in self.lib.all() if r["file_exists"] or r["making"]]

    def artists(self, recordings: list[dict] | None = None) -> dict:
        """What the user gave the artists (原作者 and 演奏者 alike), by name: their pictures (versions) and their
        descriptions (简介)."""
        names = {n for r in recordings or self.recordings() for n in (r["artist"], r["performer"]) if n}
        return {"pictures": self.lib.pictures(names), "descriptions": self.lib.descriptions(names)}

    def library_changed(self) -> None:
        """The app hears the library changed (and reads it again), with the artists' pictures and descriptions as
        they are now: a new piece may be by an artist who has them."""
        self.emit({"type": "library", **self.artists()})

    def audio(self) -> list[dict]:
        """The recordings in the library folder that are not pieces (yet, or any more): waiting for this Mac to
        transcribe, queued, being transcribed, failed, or done once and their piece since deleted."""
        pieces = {r["id"] for r in self.lib.all() if r["file_exists"]}
        return [{"id": a["id"], "name": Path(a["path"]).stem, "status": a["status"], "error": a["error"],
                 "added_at": a["added_at"]}
                for a in self.lib.audio() if a["recording_id"] not in pieces and self.lib.here(Path(a["path"]))]

    def transcribe(self, audio_id: int) -> bool:
        """A recording in the library folder made into a piece, as asked (before those transcribed on their own)."""
        item = self.lib.audio_item(audio_id)
        if item is None or not self.lib.here(Path(item["path"])):
            return False
        self.downloads.add(item["path"])
        return True

    def settings(self) -> dict:
        """language: zh or en, or empty until one is chosen (the app then uses the phone's). appearance: dark until
        another is chosen (light, or the phone's: system). search_sources: the sites online search searches, for her
        and every phone (online.SOURCES): all of them until some are chosen. output: the MIDI output's unique ID, or
        None until one is chosen."""
        return {"language": self.lib.setting("language", ""),
                "appearance": self.lib.setting("appearance", "dark"),
                "search_sources": online.chosen_sources(self.lib),
                "output": (self.playback.chosen() or {}).get("id")}

    def set_settings(self, **values) -> dict:
        """Settings that exist, with values they can have: the sites to search, at least one of online.SOURCES; an
        output by its unique ID, one this Mac has."""
        allowed = {"language": ("zh", "en"), "appearance": ("system", "dark", "light")}
        for key, value in values.items():
            if key == "output":
                out = next((o for o in self.outputs() if str(o.id) == str(value)), None)
                if out:
                    self.playback.choose(out)
                    self.look_at_piano()
            elif key == "search_sources":
                if sources := online.sources_of(value):
                    self.lib.set_setting(key, ",".join(sources))
            elif isinstance(value, str) and value in allowed.get(key, ()):
                self.lib.set_setting(key, value)
        self.emit({"type": "settings", **self.settings()})
        return self.settings()

    def outputs(self) -> list:
        return play.outputs()

    def devices(self) -> list[dict]:
        """What plays the notes: the output chosen (or found by name), or each device in its network session, and
        whether each is there now."""
        out = self.playback.target()
        if out is None:
            chosen = self.playback.chosen()
            return [{"name": chosen["name"] if chosen else self.playback.wanted, "kind": "other", "connected": False}]
        if out.peers:
            return [{"name": peer or out.name, "kind": "network", "connected": out.online} for peer in out.peers]
        return [{"name": out.name, "kind": out.kind, "connected": out.connected}]

    # ---- what the app does --------------------------------------------------------------------------
    def type(self, text: str, conversation_id: int | None = None) -> None:
        """A request typed in the app, answered on screen: in conversation_id, however long ago it was (an
        earlier one is picked up again), or in the current conversation. The phone's player used after it was
        sent wins over what it plays, though it waits its turn."""
        taps = self.assistant.taps

        def answer():
            if conversation_id is not None:
                self.assistant.pick_up(self.chat, conversation_id)
            self.assistant.ask(self.chat, text, typed=True, taps=taps)
            self.emit({"type": "conversations"})
        self.worker.submit(answer)

    def new_conversation(self) -> None:
        self.worker.submit(lambda: (self.assistant.new_conversation(self.chat), self.emit({"type": "conversations"})))

    def pick_up(self, conversation_id: int) -> None:
        self.worker.submit(lambda: (self.assistant.pick_up(self.chat, conversation_id),
                                    self.emit({"type": "conversations"})))

    def delete_conversation(self, conversation_id: int) -> None:
        def delete():
            if conversation_id == self.assistant.conversation_id:
                self.assistant.new_conversation(self.chat)  # she forgets it too
            self.conversations.delete(conversation_id)
            self.emit({"type": "conversations"})
        self.worker.submit(delete)

    def delete_recordings(self, ids: list[int]) -> None:
        """Pieces leave the library, their files to the Trash: out of the list that plays and of the playlists, and
        gone where she played them; one being made stops being made. The app hears, once, what changed."""
        for rec_id in ids:
            if job := self.downloads.making(rec_id):
                self.downloads.stop(job.id)
            self.playback.forget(rec_id)
            self.lib.delete(rec_id)
            self.conversations.forget(rec_id)
        self.library_changed()
        self.playlists_changed()
        self._audio_changed()  # a transcribed piece gone: its recording is back

    def playlists_changed(self) -> None:
        """The app hears the playlists as they are now."""
        self.emit({"type": "playlists", "playlists": self.lib.playlists()})

    def delete_audio(self, audio_id: int) -> bool:
        """A recording to transcribe leaves the library, its file to the Trash."""
        deleted = self.lib.delete_audio(audio_id)
        self._audio_changed()
        return deleted

    def close(self) -> None:
        """Stop the piano, letting the notes already sent finish."""
        self._closed = True
        self._look.set()
        self._name.set()
        self.folder.close()
        self.playback.close()


def recording(rec: dict) -> dict:
    """A library record for the app: its title in parts, and nothing about files."""
    keep = ("id", "title", "composer", "performer", "duration_s", "notes", "velocity_min", "velocity_max",
            "pedal_presses", "added_at", "favorited_at", "last_played_at", "source_url", "video_title", "channel",
            "views", "published", "thumbnail", "channel_avatar", "origin", "artwork", "making")
    parts = title_parts(rec["title"], rec["composer"], rec["performer"])
    return {**{k: rec.get(k) for k in keep}, "piece": parts["piece"], "artist": parts["artist"],
            "performer": parts["performer"]}


def transcription_on() -> bool:
    """Transcription and online search: with the advanced extra installed, unless turned off (PIANASOUL_TRANSCRIBE=0:
    the Mac app's Online Search & Transcription unticked, her alone on that extra)."""
    return os.environ.get("PIANASOUL_TRANSCRIBE") != "0" and installed("transkun", "yt_dlp")


def transcription_state() -> str:
    """As the start says it: on, or off and why."""
    if transcription_on():
        return "on"
    return "off (turned off)" if installed("transkun", "yt_dlp") else "off (the advanced extra is not installed)"


def installed(*modules: str) -> bool:
    """Whether these modules can be imported, without importing them."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in modules)
    except (ImportError, ValueError):
        return False


def name_piece(query: str, video: dict, artists: list[str] = ()) -> dict | None:
    """The names of a new piece (library.NAMES, and its aliases), asked of the chat model, from what its video says
    or, for a recording of yours, its file's name. query is what was searched for or asked, if anything; the user's
    language is its language. artists: those in the library already, whose names are kept for the same people."""
    from .llm import complete_json

    known = known_artists(list(artists))
    meanings = "; ".join(f"{name} is {meaning}" for name, meaning in {**NAMES, "aliases": ALIASES}.items())
    meanings += f"; artist and performer, {known}" if known else ""
    data = complete_json(
        "A piano recording, from a video or a file, is to be kept in a music library. Name it. Return only a JSON "
        f'object {{"piece": str, "artist": str, "performer": str, "aliases": str}}: {meanings}. Use empty strings '
        "when unsure.\n\n" + json.dumps({k: v for k, v in {"asked": query, "video_title": video.get("video_title"),
                                                             "channel": video.get("channel"),
                                                             "file_name": video.get("file_name")}.items() if v},
                                          ensure_ascii=False))
    names = {k: str(data.get(k) or "").strip() for k in ("piece", "artist", "performer", "aliases")}
    return names if names["piece"] else None


def version() -> dict:
    """The code running here: its commit, and the repository it came from (as a web address); and the Mac app's
    version when the app runs it. The app says its version and the commit it was built from (PIANASOUL_APP_VERSION,
    PIANASOUL_COMMIT): it has no git."""
    if os.environ.get("PIANASOUL_COMMIT"):
        return {"commit": os.environ["PIANASOUL_COMMIT"], "repo": REPO,
                "app": os.environ.get("PIANASOUL_APP_VERSION", "")}

    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    repo = git("remote", "get-url", "origin")
    repo = re.sub(r"^git@([^:]+):", r"https://\1/", repo)
    repo = re.sub(r"\.git$", "", re.sub(r"^https://[^@/]+@", "https://", repo))
    return {"commit": git("rev-parse", "HEAD"), "repo": repo, "app": ""}

