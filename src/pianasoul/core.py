"""PianaSoul while it runs on the Mac: one library, one piano, one assistant with her conversations, and one queue
of new pieces. The terminal chat, the Mac's voice and the phone app are ways in; each runs one Core, so whatever
one of them does, the others see.

Without her (no chat model: the advanced extra or a key is missing) the library, the piano and the queue work all
the same; what needs her is not there (assistant is None).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from . import youtube
from .chat import INSTRUCTIONS, TOOLS, Assistant, Worker
from .conversations import Conversations
from .downloads import Downloads
from .folder import Folder
from .library import ALIASES, NAMES, Library, title_parts
from .play import clock
from .playback import Playback

ROOT = Path(__file__).resolve().parents[2]
REPO = "https://github.com/kaisenyao/PianaSoul"


class Core:
    def __init__(self, note: str = "", lib: Library | None = None, chat=None, assistant: bool = True):
        """note: what the chat model is told besides INSTRUCTIONS (voice says that replies are spoken).
        chat: (chat, label) to use instead of the configured model. assistant=False: without her."""
        self.lib = lib or Library()
        self.playback = Playback(self.lib)
        self.conversations = Conversations(self.lib)  # kept without her too: a deleted piece leaves them
        self.downloads = Downloads(self.lib, name_piece=name_piece if assistant else None)
        self.listeners: list = []  # called with each event, on whichever thread it happens
        self.busy = None  # the conversation she is answering in, while she answers
        self.assistant = self.chat = None
        self.label = ""
        if assistant:
            from .llm import make_chat

            self.chat, self.label = chat or make_chat(INSTRUCTIONS + note, TOOLS)
            self.assistant = Assistant(self.lib, self.playback, self.conversations, self.downloads)
            self.assistant.restore(self.chat)
            self.assistant.on_message = lambda message: self.emit({"type": "message", "message": message})
            self.assistant.on_busy = self._busy_changed
            self.worker = Worker()  # typed requests, in order
        self.playback.listeners.append(self._playback_changed)
        self.downloads.listeners.append(self._downloads_changed)
        self._jobs = 0
        self._audio: list[dict] = []  # as the app was last told
        self.folder = Folder(self.lib, self._folder_changed)
        self.folder.watch()
        self._transcribe_waiting()
        self._playing = None
        self._told = False  # the piano's going away, while the piece waits for it
        threading.Thread(target=self._fill_in_videos, daemon=True).start()

    def emit(self, event: dict) -> None:
        for listener in list(self.listeners):
            listener(event)

    def _busy_changed(self, busy: bool) -> None:
        self.busy = self.assistant.conversation_id if busy else None
        self.emit({"type": "busy", "conversation": self.busy})

    def _playback_changed(self) -> None:
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
            self.emit({"type": "library"})

    def _fill_in_videos(self) -> None:
        """Pieces downloaded before the library kept their videos' details get them from YouTube, with the
        request a search makes (one for up to 50 videos)."""
        missing = [r for r in self.lib.all() if not r["video_title"] and youtube.video_id(r["source_url"])]
        if not missing or not youtube.api_key():
            return
        try:
            found = youtube.details([r["source_url"] for r in missing])
        except youtube.YouTubeError as exc:
            print(f"  (video details unavailable: {exc})", flush=True)
            return
        for r in missing:
            if r["source_url"] in found:
                self.lib.set_video(r["id"], found[r["source_url"]])
        self.emit({"type": "library"})

    def _downloads_changed(self) -> None:
        jobs = self.downloads.snapshot()
        self.emit({"type": "jobs", "jobs": jobs})
        if len(jobs) != self._jobs:  # a piece came in, or went: the library changed
            self._jobs = len(jobs)
            self.emit({"type": "library"})
        self._audio_changed()

    def _folder_changed(self) -> None:
        """Something came into the library folder, moved in it, or changed."""
        self.emit({"type": "library"})
        self._audio_changed()
        self._transcribe_waiting()

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
        """What this Mac can do beyond the library and the piano: transcribe (the advanced extra is installed), search
        for pieces by name (and a YouTube key is set), and her."""
        transcribe = installed("transkun", "yt_dlp")
        return {"transcribe": transcribe, "search": transcribe and bool(youtube.api_key()),
                "assistant": self.assistant is not None}

    def recordings(self) -> list[dict]:
        return [recording(r) for r in self.lib.all() if r["file_exists"]]

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
        another is chosen (light, or the phone's: system). output: the MIDI output's unique ID, or None until one is
        chosen."""
        return {"language": self.lib.setting("language", ""),
                "appearance": self.lib.setting("appearance", "dark"),
                "output": (self.playback.chosen() or {}).get("id")}

    def set_settings(self, **values) -> dict:
        """Settings that exist, with values they can have; an output by its unique ID, one this Mac has."""
        allowed = {"language": ("zh", "en"), "appearance": ("system", "dark", "light")}
        for key, value in values.items():
            if key == "output":
                out = next((o for o in self.outputs() if str(o.id) == str(value)), None)
                if out:
                    self.playback.choose(out)
            elif value in allowed.get(key, ()):
                self.lib.set_setting(key, value)
        self.emit({"type": "settings", **self.settings()})
        return self.settings()

    def outputs(self) -> list:
        from . import play

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
        earlier one is picked up again), or in the current conversation."""
        def answer():
            if conversation_id is not None:
                self.assistant.pick_up(self.chat, conversation_id)
            try:
                self.assistant.ask(self.chat, text, typed=True)
            except Exception as exc:  # that request is dropped; the conversation goes on
                print(f"  (model error: {exc}; that request is dropped)", flush=True)
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
        """Pieces leave the library, their files to the Trash: out of the list that plays, and gone where she played
        them. The app hears once."""
        for rec_id in ids:
            self.playback.forget(rec_id)
            self.lib.delete(rec_id)
            self.conversations.forget(rec_id)
        self.emit({"type": "library"})
        self._audio_changed()  # a transcribed piece gone: its recording is back

    def delete_audio(self, audio_id: int) -> bool:
        """A recording to transcribe leaves the library, its file to the Trash."""
        deleted = self.lib.delete_audio(audio_id)
        self._audio_changed()
        return deleted

    def close(self) -> None:
        """Stop the piano, letting the notes already sent finish."""
        self.folder.close()
        self.playback.close()


def recording(rec: dict) -> dict:
    """A library record for the app: its title in parts, and nothing about files."""
    keep = ("id", "title", "composer", "performer", "duration_s", "notes", "velocity_min", "velocity_max",
            "pedal_presses", "added_at", "favorited_at", "last_played_at", "source_url", "video_title", "channel",
            "views", "published", "thumbnail", "channel_avatar", "origin")
    parts = title_parts(rec["title"], rec["composer"], rec["performer"])
    return {**{k: rec.get(k) for k in keep}, "piece": parts["piece"], "artist": parts["artist"],
            "performer": parts["performer"]}


def installed(*modules: str) -> bool:
    """Whether these modules can be imported, without importing them."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in modules)
    except (ImportError, ValueError):
        return False


def name_piece(query: str, video: dict) -> dict | None:
    """The names of a new piece from a video (library.NAMES, and its aliases), asked of the chat model. query is
    what was searched for or asked; the user's language is its language."""
    from .llm import complete_json

    meanings = "; ".join(f"{name} is {meaning}" for name, meaning in {**NAMES, "aliases": ALIASES}.items())
    data = complete_json(
        "A piano recording was chosen from a video, to be kept in a music library. Name it. Return only a JSON "
        f'object {{"piece": str, "artist": str, "performer": str, "aliases": str}}: {meanings}. Use empty strings '
        "when unsure.\n\n" + json.dumps({"asked": query, "video_title": video.get("video_title", ""),
                                          "channel": video.get("channel", "")}, ensure_ascii=False))
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

