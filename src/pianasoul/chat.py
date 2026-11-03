"""Terminal chat: ask for a piece, get a performance MIDI.

    ./run.sh chat

The assistant looks in the local library first, then searches online (YouTube, through yt-dlp), gets the chosen video
ready in the background through the same queue as the app's downloads (download, transcribe, file it in the library
folder's Transcribed/), and plays it on the piano over MIDI once it is there.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import Future
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
WAITS = {"search_online": "searching"}  # a long step, and her line for it
PREPARED_KEPT = 5  # the latest pieces she prepared in the background, remembered to be asked for
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
{finding}
If you are not sure which piece the user means (a misspelled or ambiguous name), ask a short question such as
"Do you mean John Legend's All of Me?". If the request is merely vague ("a Chopin nocturne"), pick a well-known
piece and say which.

Playback:
- Use play_recording(id) ONLY to select a recording returned by search_library, library_info or a transcription
  that is done, or listed in the application state (library_matches, prepared). Never invent, infer or reuse an
  unverified recording ID.
- To operate the current or pending piece, use control_playback(action). It takes NO recording ID:
  pause: stop playing but keep the position; resume: continue from the saved position;
  restart: play the same piece from zero; retry: repeat the pending playback, preserving its intended position;
  next / previous: the next or the previous piece in the list that plays (a playlist; a piece you chose plays alone);
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
general, briefly and only what you are sure of. You cannot look up anything live (the weather, the news, prices):
asked, say so kindly at once, without first asking for a place or other details. Do not play anything unless they
ask; you may offer to.
When the user asks for one of their own playlists (歌单; the application state lists their names), call play_playlist
with its name as listed, shuffle=true if they ask to shuffle; do not search the library or online for it. If there is
no such playlist, say which there are; if it has no pieces, say so.
Use update_recording to correct a piece's names, or to add aliases (names in other languages) or tags, when useful.
Never invent results: report only what the tools returned."""

# What she does with a piece the library has not got: find it online and transcribe it, or, with online search and
# transcription off (PIANASOUL_TRANSCRIBE=0, the Mac app's checkbox unticked), say so.
FINDING_ONLINE = """2. If it is not there either, write one short, friendly sentence that says you will find it AND explicitly asks the
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
   own, as the tool describes them) and a few English tags. The piece is prepared in the background and plays by
   itself once it is ready, unless something else is chosen to play meanwhile, in this request or later (the user
   is then told it is ready); of several sent at once, only the first ready plays by itself, and the others are
   said to be ready. Do not call play_recording for it. Say in a few words whose performance you are getting ready
   and that it will play once it is, without predicting a duration; the user may chat or ask for more meanwhile. If
   transcribe_url says it is done (it was prepared before), call play_recording with its id instead.
4. If there is no good piano version, say so kindly and do not play something poor.
If the user pastes a video link, call transcribe_url with it (its names empty unless the user named them) and say
briefly that you are getting it ready, as above. For a local audio file path use transcribe_local in the same way.
The application state lists the pieces being prepared (preparing), those prepared lately (prepared) and those that
could not be (could_not_prepare): when the user asks about one, say how it is. One prepared may be played with
play_recording; for one that could not be, you may offer to look for another performance."""
FINDING_LIBRARY = """2. If it is not there either, say kindly, in a sentence or two, that it is not in their library, and that you can
   find pieces online once Online Search & Transcription (在线搜索与转谱) is turned on in PianaSoul's settings on the Mac;
   name that setting as it is. You may offer something close from their library. A pasted video link or a local
   audio file can be prepared once it is on, too."""
ONLINE_TOOLS = {"search_online", "transcribe_url", "transcribe_local"}


def instructions(online: bool = True) -> str:
    """Her instructions, with online search and transcription or without."""
    return INSTRUCTIONS.replace("{finding}", FINDING_ONLINE if online else FINDING_LIBRARY)


def tools(online: bool = True) -> list[dict]:
    """Her tools: without online search and transcription, none of theirs, so that she cannot reach them at all."""
    return TOOLS if online else [tool for tool in TOOLS if tool["name"] not in ONLINE_TOOLS]


NAME_PROPERTIES = {name: {"type": "string", "description": meaning}
                   for name, meaning in {**NAMES, "aliases": ALIASES}.items()}
TOOLS = [
    {"name": "search_library",
     "description": "Search the local MIDI library by piece, artist, performer, alias or tag (any language); "
                    "the best match first.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_online",
     "description": "Search videos online (YouTube), only once search_library, with the piece's other names, has "
                    "not found it. Returns url, title, channel, duration_s, views, description, warning flags, and "
                    "in_library when that video was already transcribed.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"},
         "max_results": {"type": "integer", "description": "1-15, default 8"}}, "required": ["query"]}},
    {"name": "transcribe_url",
     "description": "Download the audio of one video (YouTube, bilibili or another site yt-dlp supports), transcribe "
                    "it to MIDI and add it to the library under its names (left empty, they are taken from the video). "
                    "In the background, after any pieces before it: it plays by itself once ready.",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string"}, **NAME_PROPERTIES,
         "tags": {"type": "string", "description": "comma-separated English tags"}},
         "required": ["url"]}},
    {"name": "transcribe_local",
     "description": "Transcribe a local audio file (mp3, m4a, wav, flac) to MIDI and add it to the library under its "
                    "names. In the background, after any pieces before it: it plays by itself once ready.",
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
     "description": "Select a NEW recording from library_matches, search_library, library_info, a transcription that is done or the pieces prepared lately, and play it from zero. "
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
                    "next and previous move in the list that plays, as the player's buttons (past the last piece, its first again); "
                    "cancel explicitly abandons the request and resets position, so never use it merely to wait for reconnection.",
     "parameters": {"type": "object", "properties": {"action": {"type": "string",
                    "enum": ["pause", "resume", "restart", "retry", "next", "previous", "cancel"]}},
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
    """Online search, which comes with transcription (the advanced extra, yt-dlp), needing no key (SPEC 9.4)."""
    from .core import transcription_state  # core is made of this module

    state = transcription_state()
    return "online (yt-dlp)" if state == "on" else state


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


def _named(job) -> str:
    """A piece being made, as she is told of it: its title, or its link or file until it is named."""
    return job.public()["title"] or job.source


def _choice(rec: dict) -> dict:
    """A recording the application state offers her to play: what she chooses by, not where its file is."""
    return {k: v for k, v in _brief(rec).items() if k not in ("source_url", "midi_path", "notes", "pedal_presses")}


REPLY_LINES = {  # a playback result, its error or status, and her line for it
    "playback_connection_unavailable": "piano_missing", "playback_connection_lost": "piano_lost",
    "recording_unavailable": "file_missing", "recording_not_found": "which_piece", "nothing_to_play": "what_to_play",
    "paused": "pause", "stopped": "cancel", "nothing is playing": "nothing_playing", "not_started": "ready",
    "not_ready": "not_ready", "link_not_ready": "link_not_ready", "nothing_next": "nothing_next",
}


@dataclass(frozen=True)
class PlaybackRequest:
    """A piece to play, from where. A request with a scope brings its own list (ids) and mode: a playlist, named
    for her line; without one, the piece plays alone (the conversation is not a play queue). moved: a step in the
    list that plays (next, previous, from_first, restart; her line), which stays as it is."""
    path: Path
    title: str
    from_s: float = 0.0
    recording_id: int | None = None
    playlist: str = ""
    ids: tuple[int, ...] = ()
    scope: dict | None = None
    mode: str | None = None
    moved: str = ""


class Assistant:
    """She: understands a request, searches, gets new pieces and plays them, and keeps the conversation.

    Chat, voice and the phone app share one Assistant (see core.Core), so each sees what the others did."""

    def __init__(self, lib: Library, player=None, conversations: Conversations | None = None, downloads=None,
                 online: bool = True):
        self.lib = lib
        self.tools = tools(online)  # what she may call: the chat model is given these, and nothing else is run
        self.player = player  # the Playback; made on first use when not given
        self.conversations = conversations or Conversations(lib)
        self.downloads = downloads  # the Downloads; made on first use when not given
        self.request = ""
        self.speak = None  # during a spoken request: says her lines aloud as they come
        self.pending: PlaybackRequest | None = None
        self.selectable_ids: set[int] = set()  # only IDs offered by tools in the current request
        self.touched_playback = False  # set whenever play, pause or stop runs (_touch)
        self.clock = time.time
        self.replied_at: float | None = None
        self.conversation_id: int | None = None  # the conversation she remembers; None until something is said
        self.played_now: list[tuple[int, str]] = []  # the pieces chosen in this request, kept after its reply
        self.typed = False  # answering a typed request: what she plays waits for her reply (after_reply)
        self.after_reply: list = []  # what she starts playing, typed to, once her reply is there
        self.taps = 0  # what was done on the phone's player: counted, so that what she would start gives way to it
        self.taps_asked = 0  # as it was when the request began: used since, nothing she gets ready is started (_start)
        self.taps_before = 0  # as it was when the first start of a typed request began to wait
        # What plays, changed by her (play, pause, stop), by a request for pieces to be prepared or by one of them
        # starting: counted, so that a piece she prepared in the background starts only if nothing changed since.
        self.moves = 0
        self.preparing: dict[int, tuple] = {}  # being prepared: job id -> (job, moves, taps) as the request left them
        self.preparing_now: list[int] = []  # the jobs this request has sent to be prepared
        self.started_now = False  # this request started a piece or went on with one (_play)
        self.prepared: deque[int] = deque(maxlen=PREPARED_KEPT)  # the ids of the latest she prepared, oldest first
        self.failed: deque[str] = deque(maxlen=PREPARED_KEPT)  # the latest that could not be made, by name or link
        self.may_play = threading.Event()  # cleared unless voice waits for her name: a piece she prepared waits
        self.may_play.set()
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
        """Through the same queue as the app's downloads, named the same way. A piece made before is there at once;
        any other is prepared in the background, and she is free meanwhile (_prepared)."""
        if self.downloads is None:
            from .downloads import Downloads

            self.downloads = Downloads(self.lib)
        job = self.downloads.add(source, query=self.request, **names)
        if job.finished.is_set():
            if job.result is None:
                return {"status": "failed", "error": job.error or job.status}
            return {"status": "done", **_brief(job.result)}
        if not self.preparing_now:  # what this request asked for, not what an earlier one did, starts once ready
            self.moves += 1
        self.preparing_now.append(job.id)
        waited = job.id in self.preparing  # asked for again: one waits for it, as the latest request found things
        self.preparing[job.id] = (job, self.moves, self.taps_asked)  # moves as the request ends (_asked)
        if not waited:
            threading.Thread(target=self._prepared, args=(job,), daemon=True).start()
        return {"status": "preparing", "title": job.public()["title"],
                "instruction": "It is being prepared in the background. It plays by itself once ready (of several sent "
                               "at once, the first ready), unless something else is chosen to play meanwhile; then "
                               "the user is told it is ready. Do not call play_recording for it."}

    def _asked(self) -> None:
        """As a request ends: what it sent to be prepared starts once ready if nothing changes from now on, and not if
        the request played a piece itself, new or going on (they are said to be ready)."""
        moves = None if self.started_now else self.moves
        for job_id in self.preparing_now:
            job, _, taps = self.preparing[job_id]
            self.preparing[job_id] = (job, moves, taps)

    def _prepared(self, job) -> None:
        """A piece prepared in the background, once it is there (or could not be): when she is free (no request she
        is answering, and voice waiting for her name)."""
        job.finished.wait()
        while True:
            self.may_play.wait()
            with self._turn:
                if self.may_play.is_set():
                    return self._ready(job)

    def _ready(self, job) -> None:
        """It plays if nothing has changed since the request that asked for it (_start: nor on the phone), the first of
        a request's pieces only; else she says it is ready, or that it could not be made. Remembered either way
        (prepared, failed)."""
        _, moves, taps = self.preparing.pop(job.id)
        rec = self.lib.get(job.result["id"]) if job.result else None
        if rec is None:
            if job.status == "failed":  # not one stopped in the app
                title = job.public()["title"]
                self.failed.append(_named(job))
                self.piano_news({"error": "not_ready", "title": title} if title else {"error": "link_not_ready"})
            return
        self.prepared.append(rec["id"])
        if getattr(self.player, "current_id", None) == rec["id"]:
            return  # chosen meanwhile, by her or on the phone: nothing to say
        if self.moves != moves:
            self.piano_news({"status": "not_started", "title": rec["title"]})
            return
        self.moves += 1
        self.taps_asked, self.played_now = taps, []
        try:
            result = self._start(PlaybackRequest(Path(rec["midi_path"]), rec["title"], recording_id=rec["id"]))
        except Exception as exc:  # said, as for a typed request's piece (_started)
            print(f"  (playing failed: {type(exc).__name__}: {exc})", flush=True)
            result = {"error": "failed"}
        self.piano_news(result)
        for rec_id, title in self.played_now:
            self.keep("piece", title, rec_id)

    def _get_player(self):
        if self.player is None:
            from .playback import Playback

            self.player = Playback(self.lib)  # opens the output each time playback starts
        return self.player

    def play(self, id: int | None = None, from_start: bool = False) -> dict:
        """A new piece (id), the one waiting to be played again (pending), or the one in the player: from where it
        was, or from the start."""
        self._touch()
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
        """Play the request, saying so first: a piece from its list (a playlist's, or else alone), a step in the
        list that plays, or the piece in the player again. going_on: it is playing already, only looked at.
        With the piano away nothing changes, and the request waits to be retried (pending). The phone's player used
        since the request began (while she found and transcribed a piece, say), the user's choice stands: nothing
        is started, and she says it is ready (the very piece chosen there, it plays). (Nor, voice, does a piece
        paused while she listened play on by itself: finish_interaction.)"""
        player = self.player
        if not going_on and self.phone_used():
            if player is not None and request.recording_id is not None \
                    and player.current_id == request.recording_id and player.state == "playing":
                return {"status": "playing", "how": "now_playing", "title": request.title,
                        "from": clock(player.position)}
            return {"status": "not_started", "title": request.title,
                    **({"playlist": request.playlist} if request.playlist else {}),
                    "instruction": "While you were getting it, the user changed what plays on their phone, so it was "
                                   "not started. Do not start it: say in a few words that it is ready whenever they "
                                   "want it."}
        how = request.moved or (("playlist_shuffled" if request.mode == "shuffle" else "playlist") if request.playlist
                                else "restart" if from_start else "resume" if request.from_s > 0 else "playing")  # lines.py
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
                if not self.after_reply:
                    self.taps_before = self.taps
                self.after_reply.append(lambda: self._started(request, from_start))
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
        self.started_now = True
        if request.moved:  # a step in the list that plays: the list stays as it is
            was = player.current_id
            player.go_to(request.recording_id)
            if request.recording_id != was:
                self.played_now.append((request.recording_id, request.title))
        elif request.recording_id is None or (request.recording_id == player.current_id and request.scope is None):
            # the piece already in the player, from its file as the library has it now: on from where it was (a
            # resume, a retry: the same listening), or again from its start
            if request.recording_id is not None and not from_start:
                player.go_on(request.from_s)
            else:
                player.start([player.current if request.recording_id is not None else (request.path, request.title)],
                             from_s=request.from_s)
        else:  # a playlist plays on in its order; a piece she chose plays alone (the chat is not a play queue)
            ids, scope = (list(request.ids), request.scope) if request.scope is not None else (
                [request.recording_id], {"kind": "conversation", "id": self.conversation_id})
            player.select(request.recording_id, ids, scope, request.from_s, mode=request.mode)
            self.played_now.append((request.recording_id, request.title))

    def phone_used(self) -> bool:
        """Whether the phone's player was used since the request began: what it did stands."""
        return self.taps != self.taps_asked

    def _touch(self) -> None:
        """She changes what plays (play, pause, stop): a piece paused while she listened stays as she left it
        (voice.finish_interaction), and a piece she is preparing does not start by itself (_ready)."""
        self.touched_playback = True
        self.moves += 1

    def _start_after_reply(self) -> None:
        """What a typed request plays, once her reply is there; not if the phone's player was used meanwhile (a tap
        wins, as it does before she starts anything: _start)."""
        starts, self.after_reply = self.after_reply, []
        if self.taps == self.taps_before:
            for start in starts:
                start()

    def _started(self, request: PlaybackRequest, from_start: bool) -> None:
        """A typed request's piece, started after her reply: the piano gone meanwhile, the file, or anything else,
        she says so, as a tool's failure would be said."""
        try:
            self._play(request, from_start)
            self.pending = None
            print(f"  ▶ {self.player.current[1]}  {clock(self.player.position)}", flush=True)
        except LookupError as exc:
            self.pending = request
            self.piano_news(self.piano_missing(exc))
        except FileNotFoundError:
            self.piano_news({"error": "recording_unavailable"})
        except Exception as exc:
            print(f"  (playing failed: {type(exc).__name__}: {exc})", flush=True)
            self.piano_news({"error": "failed"})

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
        self._touch()
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
        if action in {"next", "previous"}:
            return self.step(1 if action == "next" else -1)
        return {"error": "invalid_control_action"}

    def step(self, direction: int) -> dict:
        """下一首 / 上一首, as ⏭ and ⏮ on the phone, in the list that plays: past its last piece, its first again; with
        this piece alone, nothing changes, and she says there is nothing lined up. Said and started as any piece
        she plays (_start)."""
        target, how = self.player.landing(direction, around=True) if self.player else (None, "")
        if how == "":
            return {"status": "nothing is playing"}
        if how == "alone":
            return {"status": "nothing_next"}
        self._touch()
        rec = self.lib.get(target)
        if not rec or not rec["file_exists"]:
            return {"error": "recording_unavailable"}
        moved = "from_first" if how == "around" else "restart" if target == self.player.current_id \
            else "next" if direction > 0 else "previous"
        return self._start(PlaybackRequest(Path(rec["midi_path"]), rec["title"], recording_id=target, moved=moved))

    def pause(self) -> dict:
        self._touch()
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
        self._touch()
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
        return [_choice(r) for r in found]

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
        going away while playing, a piece paused for her that could not go on, a piece she prepared in the background
        (_ready). She sends it to the conversation, as she would text it, but not again while it is the last thing
        there. Returns it, for where she speaks."""
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
        fn = getattr(self, name, None) if name in {t["name"] for t in self.tools} else None
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
    def ask(self, chat, text: str, speak=None, typed: bool = False, taps: int | None = None) -> str:
        """Answer one request and keep it, her lines and the pieces she played in the conversation. speak says
        her lines aloud as they come (voice); typed marks a request typed in the app, answered on screen. taps: the
        phone's player as it was when the request was made, if it waited its turn (Core.type)."""
        with self._turn:
            # Each request resends the whole conversation: after a long quiet spell, start afresh.
            if self.replied_at is not None and self.clock() - self.replied_at > NEW_CONVERSATION_AFTER_S:
                self.new_conversation(chat)
            self.speak, self.played_now, self.typed, asked = speak, [], typed, time.monotonic()
            self.preparing_now, self.started_now = [], False
            self.taps_asked = self.taps if taps is None else taps
            self.keep("user", text)
            if self.on_busy:
                self.on_busy(True)
            try:
                try:
                    answer = self._answer(chat, text, typed)
                except Exception:
                    self._start_after_reply()  # what she was to play still plays, as it would have when spoken
                    raise
                if typed:
                    time.sleep(max(0.0, asked + TYPED_REPLY_S - time.monotonic()))
                if answer:
                    self.keep("assistant", answer)
                if self.after_reply:
                    if self.on_busy:
                        self.on_busy(False)  # the dots go with her reply
                    time.sleep(TYPED_PLAY_S)
                    self._start_after_reply()
                for rec_id, title in self.played_now:
                    self.keep("piece", title, rec_id)
                return answer
            finally:
                self._asked()
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
            waiting = [job for job, _, _ in self.preparing.values()]  # one finished while she was busy is as it ended
            if making := [_named(job) for job in waiting if not job.finished.is_set()]:
                state["preparing"] = making
            made = [*self.prepared, *(job.result["id"] for job in waiting if job.finished.is_set() and job.result)]
            if prepared := [rec for rec in map(self.lib.get, made) if rec and rec["file_exists"]]:
                state["prepared"] = [_choice(rec) for rec in prepared]
                self.selectable_ids.update(rec["id"] for rec in prepared)
            if failed := [*self.failed, *(_named(job) for job in waiting if job.status == "failed")]:
                state["could_not_prepare"] = failed
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
