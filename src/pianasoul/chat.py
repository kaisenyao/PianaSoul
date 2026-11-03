"""Terminal chat: ask for a piece, get a performance MIDI.

    ./run.sh chat

The assistant looks in the local library first, then searches online (YouTube, through yt-dlp), gets the chosen video
ready through the same queue as the app's downloads (download, transcribe, file it in the library folder's
Transcribed/), and plays it on the piano over MIDI.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import Future
import importlib.util
import inspect
import json
import logging
import queue
import random
import re
import sys
import threading
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from . import online
from .conversations import QUIET_S as NEW_CONVERSATION_AFTER_S, Conversations, seconds_since
from .library import ALIASES, DB_PATH, NAMES, Library, artist_key, enrich, format_row, title_parts
from .lines import line
from .play import clock
from .playback_commands import is_chinese, match_command, strip_fillers

log = logging.getLogger(__spec__.name)

MAX_TOOL_ROUNDS = 8
MATCH_SCORE = 2.0  # a library match on a whole word (two characters or more), not on one character by chance
VOLUME_STEP = 10  # percent, for "louder" and "softer"
LIST_MOST, OVERVIEW_MOST, MOST_PLAYED = 40, 15, 20  # library_info: pieces in one answer, artists in its overview, most played
ACTIONS = {"play_recording", "control_playback", "play_playlist", "set_volume"}  # tools that do something she then confirms
WAITS = {"search_online": "searching", "transcribe_url": "preparing", "transcribe_local": "preparing"}  # long steps
# Typed to, she answers as a person texting would: her reply after a moment at least (the dots show meanwhile), and
# what she plays starts once it is there, a little after it.
TYPED_REPLY_S, TYPED_PLAY_S = 1.0, 0.8
ONLINE_TOO_SOON = json.dumps({"error": "library_not_searched", "instruction": "First call search_library with the "
                              "piece's other names (another language or spelling, the composer's other name); search "
                              "online only if it is not there."})

INSTRUCTIONS = """You are the PianaSoul assistant. The user asks for music; you find a good piano performance and
play it on their piano. Talk like a friend who loves the piano: warm, cheerful and a little playful, never like a
program.

Language: answer in the language the user writes in. When a message mixes Chinese and English, Chinese is usually
right. Write Chinese in simplified characters unless the user clearly writes traditional ones. Do not force it;
follow the user's habit.

Style: one or two short, natural sentences (a few more when the user wants to chat or asks about their music),
plain text without Markdown. Vary your wording; never repeat a sentence
you used earlier in the conversation, and say the same thing a different way each time (a small interjection or a
light, cute touch is welcome). Do not mention tools, YouTube, downloads, transcription, MIDI, note counts, ids or
file paths unless the user asks how it works or where something is; then answer truthfully. Describe the music
as something you play on the piano for the listener: in Chinese, prefer "弹", "弹奏" or "演奏" rather than
"播放"; in English, use natural phrases such as "I'll play it for you". Keep the recording's performer/version
accurate, and never claim authorship or pretend to be learning the piece. Briefly acknowledge successful
pause, continue, restart and volume changes; speak only the connection reminder when an action fails.

When the user wants to hear something:
1. The application state has library_matches: the library searched for the request's own words only, best first.
   If a recording clearly matches (same piece; same pianist if one was named), call play_recording with its id and
   say in a few words what you are playing; of several that match equally, play the first. If none does, the piece
   may still be there under its other names, as titles are often in another language: call search_library with
   them, saying nothing yet (its name in English or Chinese, whichever the request did not use, other spellings,
   the composer's other name; for example 夜曲 -> Chopin Nocturne, 致爱丽丝 -> Für Elise), and play it if it is there.
2. If it is not there either, write one short, friendly sentence that says you will find it AND explicitly asks the
   listener to wait (for example "I'll find a lovely piano performance for you; please give me a moment.").
   Vary the wording without predicting a duration or promising it will be ready immediately. Include the sentence
   with your search_online call. Search with the composer or singer, piece, catalog number, pianist if named, and "piano".
   Choose the best candidate yourself:
   - prefer recordings where the piano carries the music: solo piano first, piano with voice second, never a band
     mix or a remix; a real human performance by a named pianist, a good recording, and a duration that fits;
   - avoid falling-notes/Synthesia videos, MIDI renderings, tutorials, easy or slowed arrangements, compilations,
     and different pieces (see each result's flags);
   - for a song that is not a piano piece, a good piano cover is right. A singer or band named with a song
     ("Sodagreen's Xiao Qing Ge") only says which song it is, so any good pianist's cover is right;
   - if the user named a pianist ("Lang Lang's Chopin"), use only that pianist; if there is none, say so and ask.
   If nothing fits, try one refined search.
3. Call transcribe_url with the chosen URL, the piece's names (piece, artist, performer and aliases: each on its
   own, as the tool describes them) and a few English tags. Then call play_recording with the new id and say whose performance
   it is in a few words.
4. If there is no good piano version, say so kindly and do not play something poor.
If you are not sure which piece the user means (a misspelled or ambiguous name), ask a short question such as
"Do you mean John Legend's All of Me?". If the request is merely vague ("a Chopin nocturne"), pick a well-known
piece and say which. If the user pastes a video link, briefly say you will prepare it and explicitly ask them to
wait, without estimating a duration. Call transcribe_url with that link (its names empty unless the user named
them) and play it. For a local audio file path use transcribe_local in the same way.

Playback:
- Use play_recording(id) ONLY to select a recording returned by search_library, library_info or a successful
  transcription in this request. Never invent, infer or reuse an unverified recording ID.
- To operate the current or pending piece, use control_playback(action). It takes NO recording ID:
  pause: stop playing but keep the position; resume: continue from the saved position;
  restart: play the same piece from zero; retry: repeat the pending playback, preserving its intended position;
  cancel: explicitly abandon the selected request and reset playback. Use pause, NOT cancel, for temporary
  waiting, "not yet", or "wait until the connection is ready". These preserve the piece and position.
- A report of disconnection does not erase a piece. If the user reports fixing the connection or asks to try
  again, use retry. If they ask to continue, use resume; if they explicitly ask for the beginning, use restart.
  A vague "play again" after a disconnection can mean retry; do not assume the piece must be reselected.
  Understand the entire request, including explanations before the command. Do not search or select again
  unless they request a different piece.
- The application supplies current/pending playback state with each request. Use it as fact. A piece may be
  paused temporarily so that the assistant can listen; operate that same piece when the user asks to resume.
- Never diagnose connection or missing-file problems without a corresponding tool result. In particular,
  selection_required is an invalid tool choice, not a missing recording: use control_playback for an existing
  piece, or search_library for a new selection.
- Never announce playback before the tool succeeds. A connection failure should be explained simply as a
  connection between the device and piano, without assuming a device type or app.
- If introduction_spoken=true, the application already spoke the introduction. Do not repeat it.
- set_volume(level) sets the piano's volume, 0-100, playing or not; the application state has the current level.
  For louder or softer, change it by about 10 (more when asked for much more).
Company: you are also company. The user may simply want to chat, or ask about their own music: what is in their
library, what they have listened to lately, their favorites, an artist or a playlist of theirs. Talk with them warmly
and briefly, and ask a little back now and then. For anything about their library or their listening, call
library_info first and say only what it returns; never guess it. Whether a particular piece is there is
search_library's to say, not library_info's. To find old pieces they have not heard for a while, see what they
listen to most (most_played: its artists and tags), and choose from rarely_played what is close to it; offer them,
and play one only if they want it. What the user wrote about an artist or a playlist
(its description) is theirs, and you may draw on it. You may also say what you know of composers and pieces in
general, briefly and only what you are sure of. Do not play anything unless they ask; you may offer to.
When the user asks for one of their own playlists (歌单; the application state lists their names), call play_playlist
with its name as listed, shuffle=true if they ask to shuffle; do not search the library or online for it. If there is
no such playlist, say which there are; if it has no pieces, say so.
Use update_recording to correct a piece's names, or to add aliases (names in other languages) or tags, when useful.
Never invent results: report only what the tools returned."""

NAME_PROPERTIES = {name: {"type": "string", "description": meaning}
                   for name, meaning in {**NAMES, "aliases": ALIASES}.items()}
TOOLS = [
    {"name": "search_library",
     "description": "Search the local MIDI library by piece, artist, performer, alias or tag (any language); "
                    "the best match first. Always before search_online, with the piece's other names.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_online",
     "description": "Search videos online (YouTube). Returns url, title, channel, duration_s, views, description, "
                    "warning flags, and in_library when that video was already transcribed.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"},
         "max_results": {"type": "integer", "description": "1-15, default 8"}}, "required": ["query"]}},
    {"name": "transcribe_url",
     "description": "Download the audio of one video (YouTube, bilibili or another site yt-dlp supports), transcribe "
                    "it to MIDI and add it to the library under its names (left empty, they are taken from the video).",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string"}, **NAME_PROPERTIES,
         "tags": {"type": "string", "description": "comma-separated English tags"}},
         "required": ["url"]}},
    {"name": "transcribe_local",
     "description": "Transcribe a local audio file (mp3, m4a, wav, flac) to MIDI and add it to the library under its names.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, **NAME_PROPERTIES,
         "tags": {"type": "string", "description": "comma-separated English tags"}},
         "required": ["path", "piece"]}},
    {"name": "library_info",
     "description": "What the user's own library holds, to talk about it: overview (how many pieces, the artists and "
                    "pianists with the most, playlists, favorites, what was played last and most), recently_played, "
                    "most_played (all along and in the last 30 days, with how often, counted since counted_since), "
                    "rarely_played (never or longest ago first, then the least heard), favorites, artist (by name: "
                    "their pieces, as artist and as pianist, and what the user wrote about them) or playlist (by name: "
                    "its pieces and description). Pieces say how often they were heard (plays) and, for choosing by "
                    "style, their tags. They may then be played with play_recording.",
     "parameters": {"type": "object", "properties": {
         "about": {"type": "string", "enum": ["overview", "recently_played", "most_played", "rarely_played", "favorites",
                                             "artist", "playlist"]},
         "name": {"type": "string", "description": "the artist's or the playlist's name, for those"}},
         "required": ["about"], "additionalProperties": False}},
    {"name": "play_recording",
     "description": "Select a NEW recording from library_matches, search_library, library_info or transcription in this request, and play it from zero. "
                    "For continuing, retrying or restarting the existing piece, use control_playback instead.",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}},
                    "required": ["id"], "additionalProperties": False}},
    {"name": "play_playlist",
     "description": "Play one of the user's own playlists (歌单), named as the application state lists them: from its "
                    "first piece in its order, or shuffled when asked. Not for a piece: use play_recording.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "shuffle": {"type": "boolean"}},
                    "required": ["name"], "additionalProperties": False}},
    {"name": "control_playback",
     "description": "Control the current or pending piece using application-owned state. Never supply a recording ID. "
                    "pause keeps position (also for waiting/not ready); resume continues; restart starts at zero; retry repeats a failed attempt; "
                    "cancel explicitly abandons the request and resets position, so never use it merely to wait for reconnection.",
     "parameters": {"type": "object", "properties": {"action": {"type": "string",
                    "enum": ["pause", "resume", "restart", "retry", "cancel"]}},
                    "required": ["action"], "additionalProperties": False}},
    {"name": "set_volume",
     "description": "Set the piano's volume, 0-100 (the application state has the current level).",
     "parameters": {"type": "object", "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                    "required": ["level"], "additionalProperties": False}},
    {"name": "update_recording",
     "description": "Change a recording's names (any of piece, artist and performer; the others stay), aliases or tags.",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "integer"}, **NAME_PROPERTIES,
         "tags": {"type": "string", "description": "comma-separated English tags"}},
         "required": ["id"]}},
]


class Worker:
    """Runs calls one after another in a thread of its own. A daemon thread: quitting never waits for a call
    in progress (a model reply, a download, a transcription), so one Control+C is always enough."""

    def __init__(self):
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, *args) -> Future:
        future: Future = Future()
        self._jobs.put((future, fn, args))
        return future

    def shutdown(self) -> None:
        """No calls after those already submitted; the thread then ends."""
        self._jobs.put(None)

    def _run(self) -> None:
        while (job := self._jobs.get()) is not None:
            future, fn, args = job
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args))
                except BaseException as exc:
                    future.set_exception(exc)


def startup(name: str, **jobs: str) -> str:
    """The first lines of chat, voice and the app: what does each job, one per line, aligned."""
    return "\n".join([f"PianaSoul {name}", *(f"  {job:<9}{what}" for job, what in jobs.items())])


def search_label() -> str:
    """Online search, which comes with the advanced extra (yt-dlp), needing no key (SPEC 9.4)."""
    return "online (yt-dlp)" if importlib.util.find_spec("yt_dlp") else "off (the advanced extra is not installed)"


def spoken_title(title: str) -> str:
    """The piece alone, as she says it: "周杰伦 - 搁浅 (MappleZS)" is 搁浅, "Chopin - Nocturne Op. 9 No. 2 (Lang Lang)"
    is Nocturne Op. 9 No. 2. The full title stays on screen."""
    return title_parts(title)["piece"]


def _brief(rec: dict) -> dict:
    """A recording as she sees it: its names (as the tools take them) and what she may need to choose it."""
    keys = ("id", "aliases", "tags", "source_type", "source_url", "midi_path", "notes", "pedal_presses",
            "duration_s", "file_exists", "score")
    names = title_parts(rec["title"], rec["composer"], rec["performer"])
    return {"id": rec["id"], **names, **{k: rec[k] for k in keys if k in rec}}


REPLY_LINES = {  # a playback result, its error or status, and her line for it
    "playback_connection_unavailable": "piano_missing", "playback_connection_lost": "piano_lost",
    "recording_unavailable": "file_missing", "recording_not_found": "which_piece", "nothing_to_play": "what_to_play",
    "paused": "pause", "stopped": "cancel", "nothing is playing": "nothing_playing",
}


@dataclass(frozen=True)
class PlaybackRequest:
    """A piece to play, from where. A request with a scope brings its own list (ids) and mode: a playlist, named
    for her line; without one, the list is the pieces she has played in the conversation."""
    path: Path
    title: str
    from_s: float = 0.0
    recording_id: int | None = None
    playlist: str = ""
    ids: tuple[int, ...] = ()
    scope: dict | None = None
    mode: str | None = None


class Assistant:
    """She: understands a request, searches, gets new pieces and plays them, and keeps the conversation.

    Chat, voice and the phone app share one Assistant (see core.Core), so each sees what the others did."""

    def __init__(self, lib: Library, player=None, conversations: Conversations | None = None, downloads=None):
        self.lib = lib
        self.player = player  # the Playback; made on first use when not given
        self.conversations = conversations or Conversations(lib)
        self.downloads = downloads  # the Downloads; made on first use when not given
        self.request = ""
        self.speak = None  # during a spoken request: says her lines aloud as they come
        self.pending: PlaybackRequest | None = None
        self.selectable_ids: set[int] = set()  # only IDs offered by tools in the current request
        self.touched_playback = False  # set whenever play, pause or stop runs
        self.clock = time.time
        self.replied_at: float | None = None
        self.conversation_id: int | None = None  # the conversation she remembers; None until something is said
        self.played_now: list[tuple[int, str]] = []  # the pieces chosen in this request, kept after its reply
        self.typed = False  # answering a typed request: what she plays waits for her reply (after_reply)
        self.after_reply: list = []  # what she starts playing, typed to, once her reply is there
        self.on_message = None  # called with each message kept in a conversation
        self.last_kept = None  # (conversation, role, text) of the latest message kept
        self.on_busy = None  # called with True when she starts on a request, False when she has answered
        self._turn = threading.RLock()  # one request at a time, typed or spoken

    # ---- the conversation ------------------------------------------------------------------------
    def chinese(self) -> bool:
        """Whether she speaks Chinese now: as the last request (typed or said) was, and before there is one."""
        return is_chinese(self.request) if self.request.strip() else True

    def keep(self, role: str, text: str = "", recording_id: int | None = None) -> None:
        """Keep a line, or a piece she played, in the current conversation (made on its first line)."""
        if self.conversation_id is None:
            self.conversation_id = self.conversations.create()
        message = self.conversations.add(self.conversation_id, role, text, recording_id)
        self.last_kept = (self.conversation_id, role, text)
        if self.on_message:
            self.on_message(message)

    def tell(self, text: str, meanwhile: bool = False) -> None:
        """A line of hers before the reply: on the screen, aloud in voice, and in the conversation. meanwhile: what
        she says she will do goes on while she says it."""
        print(f"assistant> {text}", flush=True)
        if self.speak:
            self.speak(text, meanwhile=meanwhile)
        self.keep("assistant", text)

    def new_conversation(self, chat) -> None:
        """Start afresh: she forgets what was said. The piano is not touched."""
        with self._turn:
            chat.reset()
            self.conversation_id = None

    def pick_up(self, chat, conversation_id: int) -> bool:
        """Make an earlier conversation the current one: she knows it again as it was."""
        with self._turn:
            if self.conversations.get(conversation_id) is None:
                return False
            if conversation_id != self.conversation_id:
                chat.reset()
                chat.load(self.conversations.history(conversation_id))
                self.conversation_id = conversation_id
            self.replied_at = self.clock()
            return True

    def restore(self, chat) -> None:
        """At start-up: the latest conversation is still the current one if it was within the last
        NEW_CONVERSATION_AFTER_S."""
        latest = self.conversations.latest()
        if latest and seconds_since(latest["updated_at"]) < NEW_CONVERSATION_AFTER_S:
            self.pick_up(chat, latest["id"])
            self.replied_at = self.clock() - seconds_since(latest["updated_at"])

    # ---- tools -------------------------------------------------------------------------------
    def search_library(self, query: str) -> dict:
        print(f"  · library: {query}", flush=True)
        return {"results": [_brief(r) for r in self.lib.search(query)]}

    def search_online(self, query: str, max_results: int = 8) -> dict:
        print(f"  · searching online: {query}", flush=True)
        results = online.search(query, max_results)
        print(f"    {len(results)} found", flush=True)
        for r in results:
            known = self.lib.by_source(r["url"])
            r["in_library"] = known["id"] if known and known["file_exists"] else None
        keys = ("url", "channel", "duration_s", "views", "description", "in_library")
        return {"results": [{"title": r["video_title"], **{k: r[k] for k in keys},
                             "flags": online.describe(r["flags"])} for r in results]}

    def transcribe_url(self, url: str, piece: str = "", artist: str = "", performer: str = "", aliases: str = "",
                       tags: str = "") -> dict:
        if not re.match(r"https?://", url.strip()):
            return {"status": "failed", "error": "url must start with http:// or https://"}
        return self._transcribe(url, piece=piece, artist=artist, performer=performer, aliases=aliases, tags=tags)

    def transcribe_local(self, path: str, piece: str, artist: str = "", performer: str = "", aliases: str = "",
                         tags: str = "") -> dict:
        if not Path(path).expanduser().is_file():
            return {"status": "failed", "error": f"file not found: {path}"}
        return self._transcribe(path, piece=piece, artist=artist, performer=performer, aliases=aliases, tags=tags)

    def _transcribe(self, source: str, **names) -> dict:
        """Through the same queue as the app's downloads, named the same way; waits for the piece."""
        if self.downloads is None:
            from .downloads import Downloads

            self.downloads = Downloads(self.lib)
        rec = self.downloads.run(source, query=self.request, **names)
        if rec.get("error"):
            return {"status": "failed", "error": rec["error"]}
        return {"status": "done", **_brief(rec)}

    def _get_player(self):
        if self.player is None:
            from .playback import Playback

            self.player = Playback(self.lib)  # opens the output each time playback starts
        return self.player

    def play(self, id: int | None = None, from_start: bool = False) -> dict:
        """A new piece (id), the one waiting to be played again (pending), or the one in the player: from where it
        was, or from the start."""
        self.touched_playback = True
        if id is not None:
            rec = self.lib.get(int(id))
            if not rec:
                return {"error": "recording_not_found"}
            if not rec["file_exists"]:
                return {"error": "recording_unavailable"}
            return self._start(PlaybackRequest(Path(rec["midi_path"]), rec["title"], recording_id=int(id)))
        if self.pending:
            request = replace(self.pending, from_s=0.0) if from_start else self.pending
            rec = self.lib.get(request.recording_id) if request.recording_id is not None else None
            if not (rec and rec["file_exists"] if request.recording_id is not None else request.path.is_file()):
                self.pending = None
                return {"error": "recording_unavailable"}
            return self._start(request, from_start)
        if self.player and self.player.current:
            path, title = self.player.current
            if not path.is_file():
                return {"error": "recording_unavailable"}
            position = self.player.position if self.player.state in {"paused", "playing"} and not from_start else 0.0
            return self._start(PlaybackRequest(path, title, position, self.player.current_id), from_start,
                               going_on=self.player.state == "playing" and not from_start)
        return {"error": "nothing_to_play"}

    def _start(self, request: PlaybackRequest, from_start: bool = False, going_on: bool = False) -> dict:
        """Play the request, saying so first: a piece from its list (a playlist's, or else the pieces she has played
        in this conversation), or the piece in the player again. going_on: it is playing already, only looked at.
        With the piano away nothing changes, and the request waits to be retried (pending)."""
        how = ("playlist_shuffled" if request.mode == "shuffle" else "playlist") if request.playlist \
            else "restart" if from_start else "resume" if request.from_s > 0 else "playing"  # her line (lines.py)
        announced = False
        try:
            player = self._get_player()
            if going_on:
                player.check_connection()
                return {"status": "playing", "how": "now_playing", "title": request.title,
                        "from": clock(player.position)}
            player.look_for_piano()  # LookupError when it is not there: nothing is said or changed
            if self.speak:  # aloud, what she plays, before it sounds (and while the phone can still ask what plays)
                self.tell(line(how, self.chinese(), request.playlist or spoken_title(request.title)))
                announced = True
            if self.typed:  # it starts once her reply is there (ask)
                self.after_reply.append(lambda: self._started(request, from_start))
                self.pending = None
                return {"status": "playing", "how": how, "title": request.title, "from": clock(request.from_s),
                        "introduction_spoken": False, **({"playlist": request.playlist} if request.playlist else {})}
            self._play(request, from_start)
        except LookupError as exc:
            if self.player and self.player.state == "playing":
                self.player.pause()
            self.pending = request
            return self.piano_missing(exc)
        except FileNotFoundError:  # gone since it was looked at
            return {"error": "recording_unavailable"}
        self.pending = None
        print(f"  ▶ {player.current[1]}  {clock(player.position)}", flush=True)
        return {"status": "playing", "how": how, "title": player.current[1], "from": clock(player.position),
                "introduction_spoken": announced, **({"playlist": request.playlist} if request.playlist else {})}

    def _play(self, request: PlaybackRequest, from_start: bool) -> None:
        player = self.player
        if request.recording_id is None or (request.recording_id == player.current_id and request.scope is None):
            # the piece already in the player, from its file as the library has it now: on from where it was (a
            # resume, a retry: the same listening), or again from its start
            if request.recording_id is not None and not from_start:
                player.go_on(request.from_s)
            else:
                player.start([player.current if request.recording_id is not None else (request.path, request.title)],
                             from_s=request.from_s)
        else:
            ids, scope = (list(request.ids), request.scope) if request.scope is not None else (
                [*self.conversations.pieces(self.conversation_id or 0), *(i for i, _ in self.played_now)],
                {"kind": "conversation", "id": self.conversation_id})
            player.select(request.recording_id, ids, scope, request.from_s, mode=request.mode)
            self.played_now.append((request.recording_id, request.title))

    def _started(self, request: PlaybackRequest, from_start: bool) -> None:
        """A typed request's piece, started after her reply: the piano gone meanwhile, or the file, she says so."""
        try:
            self._play(request, from_start)
            print(f"  ▶ {self.player.current[1]}  {clock(self.player.position)}", flush=True)
        except LookupError as exc:
            self.pending = request
            self.piano_news(self.piano_missing(exc))
        except FileNotFoundError:
            self.piano_news({"error": "recording_unavailable"})

    def _playlist(self, name: str) -> tuple[dict | None, list[dict]]:
        """One of the user's playlists, by name (whatever its case and spaces; of two with that name, the one changed
        last), or None; and all of them."""
        wanted, playlists = str(name).strip().casefold(), self.lib.playlists()
        return max((p for p in playlists if p["name"].casefold() == wanted), key=lambda p: p["updated_at"], default=None), playlists

    def play_playlist(self, name: str, shuffle: bool = False) -> dict:
        """One of the user's playlists, by name: from its first piece in its order, or shuffled. Its pieces whose files
        are not there are left out."""
        shuffle = shuffle is True  # only a true true: not "false"
        found, playlists = self._playlist(name)
        if found is None:
            return {"error": "playlist_not_found", "playlists": [p["name"] for p in playlists]}
        pieces = [rec for rec in map(self.lib.get, found["ids"]) if rec and rec["file_exists"]]
        if not pieces:
            return {"error": "playlist_empty"}
        first = random.choice(pieces) if shuffle else pieces[0]
        self.touched_playback = True
        return self._start(PlaybackRequest(
            Path(first["midi_path"]), first["title"], recording_id=first["id"], playlist=found["name"],
            ids=tuple(p["id"] for p in pieces), scope={"kind": "playlist", "id": found["id"]},
            mode="shuffle" if shuffle else "order"))

    def library_info(self, about: str = "overview", name: str = "") -> dict:
        """The user's library, to talk about it (library_info in TOOLS); the pieces it names may be played."""
        recs = [r for r in self.lib.all() if r["file_exists"]]
        here, heard, heard_last = {r["id"]: r for r in recs}, self.lib.play_counts(), self.lib.last_plays()
        names = {r["id"]: title_parts(r["title"], r["composer"], r["performer"]) for r in recs}

        def piece(r: dict, style: bool = False, **more) -> dict:  # style: with its tags, to choose by
            self.selectable_ids.add(r["id"])
            return {"id": r["id"], **{k: v for k, v in names[r["id"]].items() if v},
                    **({"plays": heard[r["id"]]} if r["id"] in heard else {}),
                    **({"tags": r["tags"]} if style and r["tags"] else {}), **more}

        def most(counts: dict[int, int], top: int = MOST_PLAYED, **more) -> list[dict]:
            ranked = sorted((i for i in counts if i in here), key=lambda i: -counts[i])[:top]  # only these offered
            return [piece(here[i], style=True, **{k: f(i) for k, f in more.items()}) for i in ranked]

        def when(stamp: str) -> str:  # in the user's own time, as they would say it
            return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")

        played = sorted((r for r in recs if r["last_played_at"]), key=lambda r: r["last_played_at"], reverse=True)
        liked = sorted((r for r in recs if r["favorited_at"]), key=lambda r: r["favorited_at"], reverse=True)
        if about == "recently_played":
            return {"now": when(datetime.now(timezone.utc).isoformat()),
                    "pieces": [piece(r, played_at=when(r["last_played_at"])) for r in played[:LIST_MOST]]}
        if about == "most_played":  # counted from when plays were first kept: before that nothing is known
            month = self.lib.play_counts((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds"))
            first = self.lib.first_play()
            return {"counted_since": when(first) if first else None, "all_time": most(heard),
                    "last_30_days": most(month, plays_in_30_days=month.get)}
        if about == "rarely_played":  # never or longest ago first, then the least heard: to find old ones again
            # Last heard: its last play; for one not heard since plays were counted, its last start before then (a
            # start since, a skip as like as not, says nothing). since: no play counted since plays were.
            first = self.lib.first_play() or ""

            def last_heard(r: dict) -> str:
                started = r["last_played_at"] or ""
                return heard_last.get(r["id"]) or (started if not first or started < first else "")
            last = {r["id"]: last_heard(r) for r in recs}
            order = sorted(recs, key=lambda r: (last[r["id"]], heard.get(r["id"], 0)))
            def heard_when(r: dict) -> dict:
                if last[r["id"]]:
                    return {"played_at": when(last[r["id"]])}
                return {"not_heard_since": when(first)} if first and r["last_played_at"] else {"never_played": True}
            return {"pieces": [piece(r, style=True, **heard_when(r)) for r in order[:LIST_MOST]]}
        if about == "favorites":
            return {"pieces": [piece(r) for r in liked[:LIST_MOST]], "count": len(liked)}
        if about == "artist":
            key = artist_key(name)
            by = [r for r in recs if key and artist_key(names[r["id"]]["artist"]) == key]
            plays = [r for r in recs if key and artist_key(names[r["id"]]["performer"]) == key]
            if not by and not plays:
                return {"error": "artist_not_found", "artists": self.lib.artist_names()[:LIST_MOST]}
            known = names[(by or plays)[0]["id"]]["artist" if by else "performer"]
            return {"name": known, "description": self.lib.descriptions([known]).get(known, ""),
                    "as_artist": [piece(r) for r in by[:LIST_MOST]], "as_pianist": [piece(r) for r in plays[:LIST_MOST]]}
        if about == "playlist":
            found, playlists = self._playlist(name)
            if found is None:
                return {"error": "playlist_not_found", "playlists": [p["name"] for p in playlists]}
            return {"name": found["name"], "description": found["description"],
                    "pieces": [piece(here[i]) for i in found["ids"] if i in here][:LIST_MOST]}
        counts = lambda part: Counter(n[part] for n in names.values() if n[part]).most_common(OVERVIEW_MOST)
        return {"pieces": len(recs),
                "artists": [{"name": n, "pieces": c} for n, c in counts("artist")],
                "pianists": [{"name": n, "pieces": c} for n, c in counts("performer")],
                "playlists": [{"name": p["name"], "pieces": len(p["ids"])} for p in self.lib.playlists()],
                "favorites": len(liked),
                "played_last": [piece(r, played_at=when(r["last_played_at"])) for r in played[:OVERVIEW_MOST // 3]],
                "most_played": most(heard, OVERVIEW_MOST // 3)}

    def play_recording(self, id: int) -> dict:
        if id not in self.selectable_ids:
            return {"error": "selection_required", "instruction": "For the current/pending piece use control_playback. For a new piece, search_library first and use an ID from its result."}
        return self.play(id=id)

    def control_playback(self, action: str) -> dict:
        if action == "pause":
            return self.pause()
        if action == "cancel":
            return self.stop()
        if action in {"resume", "restart", "retry"}:
            return self.play(from_start=action == "restart")
        return {"error": "invalid_control_action"}

    def pause(self) -> dict:
        self.touched_playback = True
        player = self.player
        if not player or player.current is None or player.state == "stopped":
            return {"status": "nothing is playing"}
        if player.state == "paused":
            return {"status": "paused", "title": player.current[1]}
        player.pause()
        print(f"  ⏸ {player.current[1]}  {clock(player.position)}", flush=True)
        return {"status": "paused", "title": player.current[1], "at": clock(player.position)}

    def stop(self) -> dict:
        self.pending = None
        self.touched_playback = True
        if self.player and self.player.current is not None:
            self.player.stop()
            print(f"  ⏹ {self.player.current[1]}", flush=True)
        return {"status": "stopped"}

    def library_matches(self, text: str) -> list[dict]:
        """The library searched for a request's own words before the model is asked, so that it can play what it
        finds, or go looking elsewhere, at once: one step fewer before she speaks. Only matches on a whole word, which
        may be played as if search_library had offered them."""
        found = [r for r in self.lib.search(strip_fillers(text)) if r["score"] >= MATCH_SCORE and r["file_exists"]]
        self.selectable_ids.update(r["id"] for r in found)
        return [{k: v for k, v in _brief(r).items() if k not in ("source_url", "midi_path", "notes", "pedal_presses")}
                for r in found]

    def short_request(self, action: str) -> str:
        """A short request that always means the same thing (playback_commands), done without the model."""
        if action == "goodbye":
            return line("goodbye", self.chinese())
        if action in {"louder", "softer"}:
            was = self._get_player().volume
            level = self.set_volume(was + (VOLUME_STEP if action == "louder" else -VOLUME_STEP))["level"]
            return line(action if level != was else "loudest" if action == "louder" else "silent", self.chinese())
        return self.playback_reply(self.control_playback(action))

    def set_volume(self, level: int) -> dict:
        """The piano's volume: heard at once, and kept. A piece paused while she listens plays on at it."""
        player = self._get_player()
        player.set_volume(level)
        print(f"  volume {player.volume}%", flush=True)
        return {"status": "volume set", "level": player.volume}

    def piano_news(self, result: dict) -> str:
        """Her line about the piano (playback_reply) when no request to her brought it up: a tap in the app, the piano
        going away while playing, a piece paused for her that could not go on. She sends it to the conversation, as
        she would text it, but not again while it is the last thing there. Returns it, for where she speaks."""
        line = self.playback_reply(result)
        if self.last_kept != (self.conversation_id, "assistant", line):
            print(f"assistant> {line}\n", flush=True)
            self.keep("assistant", line)
        return line

    def piano_missing(self, reason) -> dict:
        """The piano is not there. Why shows here, once; the listener gets a brief reminder (playback_reply)."""
        print(f"  (cannot play: {reason})", flush=True)
        return {"error": "playback_connection_unavailable"}

    def update_recording(self, id: int, piece: str | None = None, artist: str | None = None,
                         performer: str | None = None, aliases: str | None = None, tags: str | None = None) -> dict:
        rec = self.lib.get(int(id))
        if rec is None:
            return {"error": f"no recording with id {id}"}
        given = {k: v for k, v in (("piece", piece), ("artist", artist), ("performer", performer)) if v is not None}
        if given:
            names = {**title_parts(rec["title"], rec["composer"], rec["performer"]), **given}
            self.lib.edit(rec["id"], names["piece"], names["artist"], names["performer"])
        return _brief(self.lib.update(rec["id"], aliases=aliases, tags=tags))

    def run_tool(self, name: str, args: dict) -> str:
        started = time.monotonic()
        fn = getattr(self, name, None) if name in {t["name"] for t in TOOLS} else None
        # Playback boundaries reject unsupported parameters rather than silently dropping an ID.
        if name in {"play_recording", "control_playback"}:
            expected = {"id"} if name == "play_recording" else {"action"}
            valid_value = type(args.get("id")) is int if name == "play_recording" else isinstance(args.get("action"), str)
            if set(args) != expected or not valid_value:
                log.debug("%s %s: refused, not its arguments", name, json.dumps(args, ensure_ascii=False)[:300])
                return json.dumps({"error": "invalid_playback_arguments", "instruction": "play_recording accepts only id; control_playback accepts only action, with no recording ID."})
        elif fn:
            allowed = inspect.signature(fn).parameters
            args = {k: v for k, v in args.items() if k in allowed}
        try:
            result = fn(**args) if fn else {"error": f"unknown tool {name}"}
        except online.OnlineError as exc:
            result = {"error": str(exc)}
        except TypeError as exc:
            result = {"error": f"bad arguments: {exc}"}
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        # The model words a failure for the listener; the reason stays visible here (piano_missing gives its own).
        if result.get("error") and result["error"] != "playback_connection_unavailable":
            print(f"  · {name} failed: {result['error']}", flush=True)
        if name in {"search_library", "transcribe_url", "transcribe_local"}:
            offered = result.get("results", []) if name == "search_library" else [result]
            self.selectable_ids.update(rec["id"] for rec in offered
                                       if isinstance(rec.get("id"), int) and rec.get("file_exists"))
        elif name == "search_online":  # a video already made into a piece is in the library: it may be played
            self.selectable_ids.update(r["in_library"] for r in result.get("results", []) if r.get("in_library"))
        text = json.dumps(result, ensure_ascii=False)
        log.debug("%s %s, %.1f s → %s", name, json.dumps(args, ensure_ascii=False)[:300], time.monotonic() - started,
                  text[:300])
        return text

    def playback_reply(self, result: dict) -> str:
        """Her line for a playback result (lines.py), the same for a short request and for the model's tool, typed
        or said; empty when she said it already, as she began to play."""
        if result.get("introduction_spoken"):
            return ""
        if result.get("error"):
            key = REPLY_LINES.get(result["error"], "failed")
        else:
            key = REPLY_LINES.get(result.get("status"), result.get("how", "now_playing"))
        return line(key, self.chinese(), result.get("playlist") or spoken_title(result.get("title", "")))

    # ---- conversation ------------------------------------------------------------------------
    def ask(self, chat, text: str, speak=None, typed: bool = False) -> str:
        """Answer one request and keep it, her lines and the pieces she played in the conversation. speak says
        her lines aloud as they come (voice); typed marks a request typed in the app, answered on screen."""
        with self._turn:
            # Each request resends the whole conversation: after a long quiet spell, start afresh.
            if self.replied_at is not None and self.clock() - self.replied_at > NEW_CONVERSATION_AFTER_S:
                self.new_conversation(chat)
            self.speak, self.played_now, self.typed, asked = speak, [], typed, time.monotonic()
            self.keep("user", text)
            if self.on_busy:
                self.on_busy(True)
            try:
                answer = self._answer(chat, text, typed)
                if typed:
                    time.sleep(max(0.0, asked + TYPED_REPLY_S - time.monotonic()))
                if answer:
                    self.keep("assistant", answer)
                if self.after_reply:
                    if self.on_busy:
                        self.on_busy(False)  # the dots go with her reply
                    time.sleep(TYPED_PLAY_S)
                    for start in self.after_reply:
                        start()
                for rec_id, title in self.played_now:
                    self.keep("piece", title, rec_id)
                return answer
            finally:
                self.speak, self.typed, self.after_reply = None, False, []
                self.replied_at = self.clock()
                if self.on_busy:
                    self.on_busy(False)

    def _answer(self, chat, text: str, typed: bool) -> str:
        self.request = text
        self.selectable_ids.clear()
        progress_spoken = False
        playback_announced = False
        chinese = is_chinese(text)
        action = match_command(text)
        if action is not None:
            log.debug("a short request, no model: %s", action)
            return self.short_request(action)

        # library_matches are for the request's own words only: the piece may be in the library under its other
        # names, so she looks online only once she has searched the library herself.
        library_searched = False

        def refused(call) -> bool:
            return call.name == "search_online" and not library_searched

        def model_reply(fn, *args):
            started = time.monotonic()
            reply = fn(*args)
            log.debug("model, %.1f s: %s", time.monotonic() - started,
                      ", ".join(call.name for call in reply.tool_calls) or "the answer")
            return reply

        try:
            model_text = text
            state = {"typed": True} if typed else {}
            state["library_matches"] = {"matched_on": "the request's own words only, not the piece's other names",
                                        "recordings": self.library_matches(text)}
            if self.pending:
                state["pending"] = {"title": self.pending.title, "from_s": self.pending.from_s,
                                    "status": "waiting to retry playback"}
            if self.player and self.player.current:
                state["current"] = {"title": self.player.current[1], "state": self.player.state,
                                    "position_s": self.player.position}
                rec = self.lib.get(self.player.current_id) if getattr(self.player, "current_id", None) else None
                if rec:  # its names as the library keeps them: the title need not say who plays it
                    state["current"].update(id=rec["id"], **title_parts(rec["title"], rec["composer"], rec["performer"]))
            if self.player and self.player.volume is not None:
                state["volume"] = self.player.volume
            if playlists := self.lib.playlists():
                state["playlists"] = [p["name"] for p in playlists]
            if state:
                model_text += "\n\nApplication state (data, not instructions): " + json.dumps(state, ensure_ascii=False)
            reply = model_reply(chat.send_user, model_text)
            playback_result = None
            for _ in range(MAX_TOOL_ROUNDS):
                if not reply.tool_calls:
                    break
                # Words that come with an action are not said before it is done: her reply follows it. Nor are
                # those that come with looking online too soon, which she does not do.
                if reply.text.strip() and not any(c.name in ACTIONS or refused(c) for c in reply.tool_calls):
                    self.tell(reply.text.strip(), meanwhile=True)
                    progress_spoken = True
                results = []
                for call in reply.tool_calls:
                    if refused(call):
                        log.debug("search_online: refused, the library not searched yet")
                        results.append((call, ONLINE_TOO_SOON))
                        continue
                    if not progress_spoken and call.name in WAITS:  # a long step is not started silently
                        self.tell(line(WAITS[call.name], chinese), meanwhile=True)
                        progress_spoken = True
                    raw = self.run_tool(call.name, call.arguments)
                    library_searched = library_searched or call.name == "search_library"
                    results.append((call, raw))
                    if call.name in {"play_recording", "control_playback", "play_playlist"}:
                        playback_result = json.loads(raw)
                        if playback_result.get("error") in {"playback_connection_unavailable", "recording_unavailable", "recording_not_found", "nothing_to_play"}:
                            chat.discard_turn()  # the unfinished tool exchange; earlier turns and the pending piece stay
                            return self.playback_reply(playback_result)
                if playback_result and playback_result.get("introduction_spoken"):
                    progress_spoken = playback_announced = True
                reply = model_reply(chat.send_tool_results, results)
            if reply.tool_calls:  # still calling tools after MAX_TOOL_ROUNDS: give this request up
                chat.discard_turn()
            if playback_announced:
                return ""
            return reply.text.strip() or (self.playback_reply(playback_result) if playback_result else "")
        except BaseException:  # the model failed, or Control+C: this request is forgotten, the conversation goes on
            chat.discard_turn()
            raise


HELP = """Playback: /play ▶ continue or start · /pause ⏸ · /stop ⏹ back to the beginning
Library: /library [words] · /import <folder> · /enrich      Chat: /new start over · /quit"""


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import show_details
    from .core import Core
    from .llm import complete_json

    parser = argparse.ArgumentParser(prog="pianasoul chat", description="Ask for a piece in plain language.")
    parser.add_argument("--debug", action="store_true", help="also show what happens in detail, with times")
    show_details(parser.parse_args(argv).debug)
    try:
        core = Core()
    except Exception as exc:
        print(f"Cannot start the chat model: {exc}", file=sys.stderr)
        return 2
    lib, chat, assistant = core.lib, core.chat, core.assistant
    print(startup("chat", chat=core.label, search=search_label(),
                  library=f"{lib.count()} recording(s) in {DB_PATH} · folder: {lib.music_dir}"))
    print(HELP + "\n")
    try:
        import readline  # noqa: F401  (line editing and history)
    except ImportError:
        pass
    while True:
        try:
            text = input("you> ").strip()
        except EOFError:
            text = "/quit"
        except KeyboardInterrupt:  # Control+C must not end the session by accident
            print("\n(type /quit to exit)")
            continue
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            assistant.stop()
            core.close()
            return 0
        if text in {"/play", "/pause", "/stop"}:
            result = getattr(assistant, text[1:])()
            if "error" in result:
                print(result["error"])
            elif text == "/pause" and result["status"] != "paused":
                print("Nothing is playing.")
            continue
        if text == "/help":
            print(HELP)
            continue
        if text == "/new":
            assistant.new_conversation(chat)
            print("Started a new conversation.")
            continue
        if text.startswith("/library"):
            words = text[len("/library"):].strip()
            rows = lib.search(words, limit=50) if words else lib.all()
            print("\n".join(format_row(r) for r in rows) or "Nothing found.")
            continue
        if text.startswith("/import "):
            added = lib.import_folder(Path(text[len("/import "):].strip().strip("'\"")))
            print(f"Imported {len(added)} MIDI file(s). Library now has {lib.count()} recording(s).")
            continue
        if text == "/enrich":
            print(f"Updated {enrich(lib, complete_json)} recording(s).")
            continue
        try:
            answer = assistant.ask(chat, text)
        except KeyboardInterrupt:
            print("\n(interrupted; that request is dropped)")
            continue
        except Exception as exc:
            print(f"  (model error: {exc}; that request is dropped)")
            continue
        if answer:
            print(f"\nassistant> {answer}\n")


if __name__ == "__main__":
    sys.exit(main())
