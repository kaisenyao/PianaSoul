"""PianaSoul while it runs on the Mac: one library, one piano, one assistant with her conversations, and one queue
of new pieces. The terminal chat, the Mac's voice and the phone app are ways in; each runs one Core, so whatever
one of them does, the others see.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from .chat import INSTRUCTIONS, TOOLS, Assistant, Worker
from .conversations import Conversations
from .downloads import Downloads
from .library import ALIASES, NAMES, Library, title_parts
from .play import clock
from .playback import Playback

ROOT = Path(__file__).resolve().parents[2]


class Core:
    def __init__(self, note: str = "", lib: Library | None = None, chat=None):
        """note: what the chat model is told besides INSTRUCTIONS (voice says that replies are spoken).
        chat: (chat, label) to use instead of the configured model."""
        from .llm import make_chat

        self.lib = lib or Library()
        self.lib.index_midi_folder()
        self.playback = Playback(self.lib)
        self.conversations = Conversations(self.lib)
        self.downloads = Downloads(self.lib, name_piece=name_piece)
        self.assistant = Assistant(self.lib, self.playback, self.conversations, self.downloads)
        self.chat, self.label = chat or make_chat(INSTRUCTIONS + note, TOOLS)
        self.assistant.restore(self.chat)
        self.worker = Worker()  # typed requests, in order
        self.listeners: list = []  # called with each event, on whichever thread it happens
        self.playback.listeners.append(self._playback_changed)
        self.downloads.listeners.append(self._downloads_changed)
        self.assistant.on_message = lambda message: self.emit({"type": "message", "message": message})
        self.busy = None  # the conversation she is answering in, while she answers
        self.assistant.on_busy = self._busy_changed
        self._jobs = 0
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
            self._told = True  # and says it too if she is on (voice.Conversation.remark)
            print(f"  ⏸ {self.playback.current[1]}  {clock(self.playback.position)}  ({lost})", flush=True)
            self.assistant.piano_news({"error": "playback_connection_lost"})
        elif not lost:
            self._told = False
        if snapshot["state"] != "playing":
            self._playing = None
        elif snapshot["id"] != self._playing:  # it is in 最近播放 now, and a piece she meant to retry is old news
            self._playing = snapshot["id"]
            self.assistant.pending = None
            self.emit({"type": "library"})

    def _fill_in_videos(self) -> None:
        """Pieces downloaded before the library kept their videos' details get them from YouTube, with the
        request a search makes (one for up to 50 videos)."""
        from . import youtube

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

    # ---- what the app shows -----------------------------------------------------------------------
    def recordings(self) -> list[dict]:
        return [recording(r) for r in self.lib.all() if r["file_exists"]]

    def settings(self) -> dict:
        """language: zh or en, or empty until one is chosen (the app then uses the phone's). appearance: dark until
        another is chosen (light, or the phone's: system)."""
        return {"language": self.lib.setting("language", ""),
                "appearance": self.lib.setting("appearance", "dark")}

    def set_settings(self, **values: str) -> dict:
        allowed = {"language": ("zh", "en"), "appearance": ("system", "dark", "light")}
        for key, value in values.items():
            if value in allowed.get(key, ()):
                self.lib.set_setting(key, value)
        self.emit({"type": "settings", **self.settings()})
        return self.settings()

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
        """Pieces leave the library, with their files: out of the list that plays, and gone where she played them.
        The app hears once."""
        for rec_id in ids:
            self.playback.forget(rec_id)
            self.lib.delete(rec_id)
            self.conversations.forget(rec_id)
        self.emit({"type": "library"})

    def close(self) -> None:
        """Stop the piano, letting the notes already sent finish."""
        self.playback.close()


def recording(rec: dict) -> dict:
    """A library record for the app: its title in parts, and nothing about files."""
    keep = ("id", "title", "composer", "performer", "duration_s", "notes", "velocity_min", "velocity_max",
            "pedal_presses", "added_at", "favorited_at", "last_played_at", "source_url", "video_title", "channel",
            "views", "published", "thumbnail", "channel_avatar")
    parts = title_parts(rec["title"], rec["composer"], rec["performer"])
    return {**{k: rec.get(k) for k in keep}, "piece": parts["piece"], "artist": parts["artist"],
            "performer": parts["performer"]}


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


def devices() -> list[dict]:
    """What plays the notes: the piano itself, or the devices in the network MIDI session, and whether each is
    there now."""
    from .play import DEFAULT_PORT, find_port, session_peers

    wanted = os.environ.get("PIANASOUL_PORT") or DEFAULT_PORT
    try:
        name = find_port(wanted)
    except Exception:
        return [{"name": wanted, "kind": "piano", "connected": False}]
    peers = session_peers(name)
    if peers is None:
        return [{"name": name, "kind": "piano", "connected": True}]
    if not peers:
        return [{"name": name, "kind": "network", "connected": False}]
    return [{"name": peer or name, "kind": "network", "connected": True} for peer in peers]


def version() -> dict:
    """The code running here: its commit, and the repository it came from (as a web address)."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    repo = git("remote", "get-url", "origin")
    repo = re.sub(r"^git@([^:]+):", r"https://\1/", repo)
    repo = re.sub(r"\.git$", "", re.sub(r"^https://[^@/]+@", "https://", repo))
    return {"commit": git("rev-parse", "HEAD"), "repo": repo}

