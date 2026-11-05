"""Terminal chat: ask for a piece, get a performance MIDI.

    ./run.sh chat

The assistant looks in the local library, and online as it does (YouTube and Bilibili, those chosen): what the library
has plays from there; for anything else it gets the chosen video ready in the background through the same queue as
the app's downloads (download, transcribe, file it in the library folder's Transcribed/), and plays it on the piano
over MIDI once it can play: while the rest of it is transcribed.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field, replace
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
from .claims import asks_to_hear, asks_which, claims
from .conversations import QUIET_S as NEW_CONVERSATION_AFTER_S, Conversations, seconds_since
from .library import ALIASES, DB_PATH, NAMES, Library, artist_key, enrich, format_row, said_whole, title_parts
from .lines import every, line
from .play import clock
from .playback_commands import in_chinese, match_command, strip_fillers

log = logging.getLogger(__spec__.name)

MAX_TOOL_ROUNDS = 8
MATCH_SCORE = 2.0  # a library match on a whole word (two characters or more), not on one character by chance
VOLUME_STEP = 10  # percent, for "louder" and "softer"
LIST_MOST, OVERVIEW_MOST, MOST_PLAYED = 40, 15, 20  # library_info: pieces in one answer, artists in its overview, most played
ACTIONS = {"play_recording", "control_playback", "play_playlist", "replay_conversation", "set_volume",
           "save_conversation_as_playlist", "rename_playlist"}  # tools that do something she then confirms
PLAYS = {"play_recording", "control_playback", "play_playlist", "replay_conversation"}  # their result, said with her line (playback_reply)
WAITS = {"search_online": "searching"}  # going online, and her line for it when she has said nothing yet
PREPARES = {"transcribe_url", "transcribe_local"}  # her calls that send a piece to be prepared
ANSWERED_IN = {"search_library", "search_online", "library_info"}  # their results bring words in other languages
LOOKS = {"search_library", "search_online", "library_info"}  # tools that only look: they do nothing she may say is done
# What the model is told as it is asked again for a reply held back (Assistant.unfounded): words that say what no tool
# did, or a question which piece is meant before looking. Once a request: wrongly held back, the reply only comes a
# little later.
HELD_BACK = {
    "claims": "(From the application, not the user.) Your reply {reply} was held back, not said: it says something "
              "is done or being done, but no tool did it. If the user asked for that, call the tool that does it; if "
              "not, reply without saying it is done. Never write notes in brackets such as [played: …]. Answer in "
              "{language}.",
    "asks": "(From the application, not the user.) Your reply {reply} was held back, not said: it asks which piece "
            "the user means before you looked. If they asked to hear a piece, look first as your instructions say "
            "(library_matches, then search_library with the piece's other names) and play it if it is there; ask "
            "only if it is still unclear. If they did not, reply as you meant to. Answer in {language}.",
}
PREPARED_KEPT = 5  # the latest pieces she prepared in the background, remembered to be asked for
# Of a piece she prepares, the versions tried besides the one she chose while one cannot be made: each may take a
# download and a transcription before it fails, so the listener is not kept waiting long for a third failure.
OTHER_VERSIONS = 2
# Her calls about a piece: one response of hers that plays a piece and makes another of these is about several.
PIECE_CALLS = {"play_recording", "search_library", "search_online", "transcribe_url", "transcribe_local"}
# Typed to, she answers as a person texting would: her reply after a moment at least (the dots show meanwhile), and
# what she plays starts once it is there, a little after it.
TYPED_REPLY_S, TYPED_PLAY_S = 1.0, 0.8
# Spoken, her news of a piece she sent to be prepared is said once the recording of the version being made begins to
# come in (its download's progress) or is there, or NEWS_WAIT_S after she sent it at the latest; the piece starts once
# it can play and her news has been said (Assistant._announce). Her news (2.6 to 7.5 s, 3.5 s the median) is longer
# than the 1.4 s from a download done to its first part playing: the sooner it is said, the sooner the first note.
# Recorded runs of 2026-11-04 (42 on this Mac, from transcribe_url): her reply is there after 1.2 s (median; 2.7 s for
# nine in ten); the first of a recording comes after 2.1 to 3.1 s, its video's page read first (27 downloads of the
# same videos timed again, most done 0.3 to 1 s later), or 6.6 to 8.2 s when YouTube holds it back some 5 s (25 of the
# 42). Waiting for those would then leave 9.5 s of silence after her first sentence (median); 3 s leaves 5 s, about as
# when it is not held back (4.4 s), and the rest of the wait after her news (3.5 s, the lead-in's second included). Up
# to 4 s, the first note is no later for it: her news ends before the piece can play.
NEWS_WAIT_S = 3.0
ONLINE_TOO_SOON = json.dumps({"error": "library_not_searched", "instruction": "Search the library too: call "
                              "search_library with the piece's other names (another language or spelling, the "
                              "composer's other name) in the same response as search_online, and play it from the "
                              "library if it is there."})
ONLINE_FAILED = json.dumps({"error": "online_search_failed", "instruction": "Searching online failed this time, as its "
                            "result said: do not search again. Say you could not search online this time, and why, "
                            "as your instructions say."})
# Her search online in the response whose search_library found the piece clearly (Assistant.clear_match) is not run:
# what the library has plays without waiting for the web (a search there takes a second or so). She may still search
# online after it, for another version.
IN_LIBRARY = ("Not searched online: the library has this piece (in_library), found by its name. Play it from there "
              "with play_recording. Only if the user asked for another version or for one online, or named another "
              "pianist, call search_online again.")

INSTRUCTIONS = """You are the PianaSoul assistant. The user asks for music; you find a good piano performance and
play it on their piano. Talk like a friend who loves the piano: warm, cheerful and a little playful, never like a
program. Your names are 小茗同学 in Chinese and Jasmine in English, and you call yourself by the one that fits the
language of the conversation. In anything you say, speak of yourself as "I" ("我"), never in the third person (not
"小茗同学查到…" or "Jasmine is here").

Language: answer in the language of the user's latest request, whatever it is, every sentence of yours in it, also
those said while you look; your instructions, the examples in them and the library may be in another language, and a
name in the request does not change it ("play 晴天" is English). The application state says which (answer_in), as the
application's own lines take it, and so do the results that bring the library's or the web's words. Write Chinese in
simplified characters, always.

Style: one or two short, natural sentences (a few more when the user wants to chat or asks about their music),
plain text without Markdown or emoji. Vary your wording; never repeat a sentence
you used earlier in the conversation, and say the same thing a different way each time (a small interjection or a
light, cute touch is welcome). Do not mention tools, video sites, downloads, transcription, MIDI, note counts, ids or
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
   them{quiet} (its name in English or Chinese, whichever the request did not use, other spellings,
   the composer's other name; for example 夜曲 -> Chopin Nocturne, 致爱丽丝 -> Für Elise), and play it if it is there.
{finding}
Several pieces in one request ("我想听青花瓷和千里之外"; "play Clair de Lune, then Gymnopédie No. 1"): take each as
above, in the order asked, except that those in the library play first. Call play_recording for each one in the
library, in that order: the first plays now and the others follow it (Playing Next on the phone shows them). One
sentence of yours says the whole plan ("好呀，先弹《青花瓷》，再接着弹《千里之外》～"; "Clair de Lune first, then
Gymnopédie No. 1.").
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
  next / previous: the next or the previous piece in the list that plays (a playlist, or the pieces asked for
  together; a piece you chose by itself plays alone);
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
The pieces of this conversation are those you played in it, in that order (not those only asked about, found
missing, or prepared and not played). When the user asks to hear them again ("把这段聊天里的歌从头放一遍"; "play this
chat's songs again from the start"), call replay_conversation. When they ask to keep them as a playlist
("把这段聊天里的歌整理成歌单"; "save these songs as a playlist"), choose a short name that fits them, in the language
you answer in, and call save_conversation_as_playlist; it does not change what plays. Then tell them its name and how
many pieces it has, and that they can rename it anytime, here or on their phone
("歌单建好啦，我暂时叫它《雨天的钢琴》，一共 8 首，想换名字随时告诉我～"; "Your playlist is ready: I've called it Rainy
Day Piano for now, 8 pieces. Rename it anytime."). To rename one of their playlists, call rename_playlist. If this
conversation has no pieces yet, say so.
Use update_recording to correct a piece's names, or to add aliases (names in other languages) or tags, when useful.
Never invent results: report only what the tools returned."""

# What she does with a piece the library has not got: find it online and transcribe it, or, with online search and
# transcription off (PIANASOUL_TRANSCRIBE=0, the Mac app's checkbox unticked), say so.
FINDING_ONLINE = """2. In the same response as that search_library call, search online for it too (search_online), in case it is not
   there: what is found online is for a piece the library has under none of its names, and one it has plays from
   there (1.), never prepared again. Begin that response with one short, friendly sentence that says what you will
   do: find a version of it ("好呀，我去找一版《搁浅》～"; "Sure, I'll find a version of it."). It neither asks the listener to
   wait nor says how long it takes, and it is your only sentence before your news (3.): if you said one already in
   this request, or another piece of this request plays meanwhile, write nothing more. Search with few words: the
   singer or composer, the piece (with its catalog number), the pianist if named, and "piano" once; more words
   ("cover", "solo", "piano" again) find less, often nothing.
   If it is not in the library, choose the best candidate yourself:
   - prefer recordings where the piano carries the music: solo piano first, piano with voice second, never a band
     mix or a remix; a real human performance by a named pianist, a good recording, and a duration that fits;
   - avoid falling-notes/Synthesia videos, MIDI renderings, tutorials, easy or slowed arrangements, compilations,
     and different pieces (see each result's flags);
   - for a song that is not a piano piece, a good piano cover is right. A singer or band named with a song
     ("Sodagreen's Xiao Qing Ge") only says which song it is, so any good pianist's cover is right;
   - if the user named a pianist ("Lang Lang's Chopin"), use only that pianist; if there is none, say so and ask.
   If nothing is found or nothing fits, search once more with fewer words (the piece and "piano"), never the same
   words again. If search_online fails, do not search again; say you could not search online this time (not that
   nothing was found), and by each site's reason (unavailable): unreachable, suggest turning on the other site
   too (YouTube or Bilibili, 哔哩哔哩) under the search field of Search Online (在线搜索) on the phone, unless
   both were searched; refused, suggest trying again shortly, on Bilibili with any proxy (VPN) turned off.
3. Call transcribe_url once for the piece: with the chosen URL, the piece's names (piece, artist, performer and
   aliases: each on its own, as the tool describes them), a few English tags, and in alternatives the next good
   candidates of the same search, best first, each with its pianist. One version is prepared at a time: if the
   chosen one cannot be, the next is tried, quietly. The piece is prepared in the background and plays by itself
   once it is ready (after what this request plays), unless something else is chosen to play meanwhile (the user is
   then told it is ready). Do not call play_recording for it. Asked for this piece alone, write no words with it:
   after its result, share the news in a few words: you found it, and whose performance it is
   ("找到啦，是 Jason Piano 弹的《黑色幽默》～"; "Found it: Jason Piano's Black Humor."), and nothing more: nothing of
   waiting, of getting it ready or of when it plays, and no remark about the music. Spoken, it is said before the
   piece can play, and the piece follows it with nothing more said. Of several pieces in one
   request, your sentence says what plays now and what follows once ready ("《千里之外》先弹给你，《青花瓷》我这就去准备～";
   "Gymnopédie No. 1 first; I'll get Clair de Lune ready to follow."). If transcribe_url says it is done (it was
   prepared before), call play_recording with its id instead.
4. If there is no good piano version, say so kindly and do not play something poor.
If the user pastes a video link, call transcribe_url with it (its names empty unless the user named them) and say
in a few words that you will play it ("好呀，弹给你听～"; "Sure, I'll play it for you."), which is said as your news is.
For a local audio file path use transcribe_local in the same way.
The application state lists the pieces being prepared (preparing), those prepared lately (prepared) and those that
could not be (could_not_prepare): when the user asks about one, say how it is. One prepared may be played with
play_recording; for one that could not be, you may offer to look for another performance."""
FINDING_LIBRARY = """2. If it is not there either, say kindly, in a sentence or two, that it is not in their library, and that you can
   find pieces online once Online Search & Transcription is turned on in PianaSoul's settings on the Mac; name that
   setting exactly, as "在线搜索与转谱" when you answer in Chinese and "Online Search & Transcription" otherwise. You
   may offer something close from their library. A pasted video link or a local audio file can be prepared once it
   is on, too."""
ONLINE_TOOLS = {"search_online", *PREPARES}


def instructions(online: bool = True) -> str:
    """Her instructions, with online search and transcription or without. With it, she searches online in the same
    response as the library under the piece's other names, and that response has her sentence for going online
    (FINDING_ONLINE); without it, she searches the library saying nothing yet."""
    return INSTRUCTIONS.replace("{quiet}", "" if online else ", saying nothing yet").replace(
        "{finding}", FINDING_ONLINE if online else FINDING_LIBRARY)


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
     "description": "Search videos online for a piece the library may not have, in the same response as "
                    "search_library with the piece's other names, or after it. Returns url, title, site, channel, "
                    "duration_s, views, description, warning flags, and in_library when that video was already "
                    "transcribed; and the sites that could not be searched this time, with why (unavailable). When "
                    "none could be, it fails, with the sites searched (searched) and those turned off (off), which "
                    "only the user can turn on.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"},
         "max_results": {"type": "integer", "description": "1-15, default 8"}}, "required": ["query"]}},
    {"name": "transcribe_url",
     "description": "Download the audio of one video (YouTube, bilibili or another site yt-dlp supports), transcribe "
                    "it to MIDI and add it to the library under its names (left empty, they are taken from the video). "
                    "In the background, after any pieces before it: once ready it plays by itself, after what this "
                    "request plays. One version of a piece at a time: should it not be made, its alternatives are "
                    "tried in turn.",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string"}, **NAME_PROPERTIES,
         "tags": {"type": "string", "description": "comma-separated English tags"},
         "alternatives": {"type": "array", "description": f"up to {OTHER_VERSIONS} other good videos of this piece "
                                                          "from the same search, best first",
                          "items": {"type": "object", "properties": {"url": {"type": "string"},
                                                                     "performer": NAME_PROPERTIES["performer"]},
                                    "required": ["url", "performer"]}}},
         "required": ["url"]}},
    {"name": "transcribe_local",
     "description": "Transcribe a local audio file (mp3, m4a, wav, flac) to MIDI and add it to the library under its "
                    "names. In the background, after any pieces before it: once ready it plays by itself, after what "
                    "this request plays.",
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
     "description": "Select a NEW recording from library_matches, search_library, library_info, a transcription that is done or the pieces prepared lately, and play it from zero in place of the current piece. "
                    "Called again in the same request, for another piece asked for, that one follows the first. "
                    "For continuing, retrying or restarting the existing piece, use control_playback instead.",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}},
                    "required": ["id"], "additionalProperties": False}},
    {"name": "play_playlist",
     "description": "Play one of the user's own playlists (歌单), named as the application state lists them: from its "
                    "first piece in its order, or shuffled when asked. Not for a piece: use play_recording.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "shuffle": {"type": "boolean"}},
                    "required": ["name"], "additionalProperties": False}},
    {"name": "replay_conversation",
     "description": "Play again the pieces played in this conversation, in the order they were played, from the first: "
                    "a list for now, not a playlist.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "save_conversation_as_playlist",
     "description": "Make a new playlist (歌单) of the pieces played in this conversation, in their order, named as "
                    "you choose. What plays does not change.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"], "additionalProperties": False}},
    {"name": "rename_playlist",
     "description": "Rename one of the user's playlists, named as the application state lists them.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "new_name": {"type": "string"}},
                    "required": ["name", "new_name"], "additionalProperties": False}},
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


def says_name(words: str, name: str) -> bool:
    """Whether her words say a name, or a part of it ("夜色钢琴曲 赵海洋" as 赵海洋), whatever the spaces and marks in
    it ("Czzy__" as Czzy)."""
    def bare(text: str) -> str:
        return "".join(c for c in text.casefold() if c.isalnum())
    said = bare(words)
    return any(len(part) > 1 and part in said for part in map(bare, [name, *name.split()]))


def _brief(rec: dict) -> dict:
    """A recording as she sees it: its names (as the tools take them) and what she may need to choose it (one being
    made is there to play, as it is made)."""
    keys = ("id", "aliases", "tags", "source_type", "source_url", "midi_path", "notes", "pedal_presses",
            "duration_s", "score")
    names = title_parts(rec["title"], rec["composer"], rec["performer"])
    return {"id": rec["id"], **names, **{k: rec[k] for k in keys if k in rec},
            "file_exists": rec["file_exists"] or rec["making"]}


def _named(job) -> str:
    """A piece being made, as she is told of it: its title, or its link or file until it is named."""
    return job.public()["title"] or job.source


def _choice(rec: dict) -> dict:
    """A recording the application state offers her to play: what she chooses by, not where its file is."""
    return {k: v for k, v in _brief(rec).items() if k not in ("source_url", "midi_path", "notes", "pedal_presses")}


REPLY_LINES = {  # a playback result, its error or status, and her line for it
    "playback_connection_unavailable": "piano_missing", "playback_connection_lost": "piano_lost",
    "recording_unavailable": "file_missing", "recording_not_found": "not_in_library", "nothing_to_play": "what_to_play",
    "playlist_not_found": "no_playlist", "playlist_empty": "playlist_empty",
    "paused": "pause", "stopped": "cancel", "nothing is playing": "nothing_playing", "not_started": "ready",
    "not_ready": "not_ready", "link_not_ready": "link_not_ready", "nothing_next": "nothing_next",
    "conversation_empty": "conversation_empty",
    "lined_up": "lined_up",  # a piece she prepared, joining the list that plays (_ready); not one a request lines up
    "coming": "coming", "changed_on_phone": "phone_stands",
}


@dataclass(frozen=True)
class PlaybackRequest:
    """A piece to play, from where. A request with a scope brings its own list (ids) and mode: a playlist, named
    for her line, or this conversation's pieces played again (replay); without one, the piece plays alone (the
    conversation is not a play queue), or, lined_up, after what its request chose before it. moved: a step in the
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
    replay: bool = False
    lined_up: bool = False


@dataclass
class Lineup:
    """What one request asked to hear, in the order it plays: the pieces it chose or went on with (ids), and after
    them, as each is ready, those it sent to be prepared (_ready). listed: the player's list they are in
    (Playback.listed), once one plays. moves and taps: what plays, as the request left it (_asked), and the phone's
    player, as it began; once a piece it prepared starts by itself, both as they are then (_place). announced: her
    news of the piece it sent to be prepared was said, spoken, as its line (_announce): as it starts, nothing more
    is. Of the first piece it sent to be prepared, the one that starts by itself (_to_start): sent, when
    (time.monotonic(): her news waits from then, _announce); and named, the version her words named by its performer
    (its job's id, link and performer): the one she chose, kept as her reply comes only if her words name its
    performer (ask), then the one the line that says it is another names (_other_version)."""
    taps: int = 0
    ids: list[int] = field(default_factory=list)
    listed: int | None = None
    moves: int = 0
    announced: bool = False
    sent: float | None = None
    named: tuple[int, str, str] | None = None


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
        self.paused_for_her = False  # answering a request the piece playing was paused for: it plays on after (ask)
        self.after_reply: list = []  # what she starts playing, typed to, once her reply is there
        self.taps = 0  # what was done on the phone's player: counted, so that what she would start gives way to it
        self.taps_asked = 0  # as it was when the request began: used since, nothing she gets ready is started (_start)
        self.taps_before = 0  # as it was when the first start of a typed request began to wait
        # What plays, changed by her (play, pause, stop): counted, so that a piece she prepared in the background
        # starts by itself, nothing of its request playing, only if nothing changed since (_ready).
        self.moves = 0
        self.preparing: dict[int, tuple] = {}  # being prepared: job id -> (job, the Lineup of the request that sent it)
        self.preparing_now: list[int] = []  # the jobs this request has sent to be prepared
        self.asked_now: Lineup | None = None  # what the request she answers asks to hear (_ready: that of a piece made)
        self.lineup: Lineup | None = None  # of the latest request that chose pieces or sent any to be prepared
        self.started_now = False  # this request started a piece or went on with one (_play)
        self.looked_for = ""  # her words said at once as this request searched the library or chose several (_named)
        # Her words that came with a piece she plays among others asked for: said as it begins (_start), spoken, when
        # they name it; else before her reply (_answer).
        self.held = ""
        self.line_said = False  # her line, not her own words, said what this request plays
        self.said_now = ""  # what she said (or showed) for this request before her reply (tell)
        self.prepared: deque[int] = deque(maxlen=PREPARED_KEPT)  # the ids of the latest she prepared, oldest first
        self.failed: deque[str] = deque(maxlen=PREPARED_KEPT)  # the latest that could not be made, by name or link
        # Paused while she listened, held for a piece of hers to start in its place (voice.finish_interaction): the
        # piece, where it was, and moves and taps then; it plays on should none come (_play_on).
        self.held_piece: tuple | None = None
        self.may_play = threading.Event()  # cleared unless voice waits for her name: a piece she prepared waits
        self.may_play.set()
        self.on_message = None  # called with each message kept in a conversation
        self.last_kept = None  # (conversation, role, text) of the latest message kept
        self.on_busy = None  # called with True when she starts on a request, False when she has answered
        self.on_playlists = None  # called when she made or renamed a playlist
        self._turn = threading.RLock()  # one request at a time, typed or spoken
        self._news = threading.Lock()  # one line about the piano at a time (piano_news): from Core, the phone or voice
        self._language = ("", True)  # a request, and whether it is in Chinese: worked out once (chinese)

    # ---- the conversation ------------------------------------------------------------------------
    def chinese(self) -> bool:
        """Whether she speaks Chinese now: as the last request in this conversation (typed or said) was, the names it
        gives aside ("play 晴天" is in English); and before there is one. Asked often (voice, for each moment she
        waits), it is worked out once for each request."""
        if self._language[0] != self.request:
            self._language = self.request, in_chinese(self.request, self._names()) if self.request.strip() else True
        return self._language[1]

    def _names(self) -> set[str]:
        """The names a request may give: the library's pieces (and their other names), artists and pianists, and its
        playlists."""
        rows = self.lib.execute("SELECT title, composer, performer, aliases FROM recordings")
        names = {name for title, composer, performer, aliases in rows
                 for name in (*title_parts(title, composer, performer).values(), *re.split(r"[;,；，]", aliases))}
        return names | {row[0] for row in self.lib.execute("SELECT name FROM playlists")}

    def answer_in(self) -> str:
        """The language of her answer, as her ready-made lines take it (chinese), for the model."""
        return "Chinese" if self.chinese() else "the language of the request, which is not Chinese"

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
        self.said_now = f"{self.said_now} {text}"

    def new_conversation(self, chat) -> None:
        """Start afresh: she forgets what was said. The piano is not touched."""
        with self._turn:
            chat.reset()
            self.conversation_id = None

    def pick_up(self, chat, conversation_id: int) -> bool:
        """Make an earlier conversation the current one: she knows it again as it was, and speaks as its last request
        did (chinese)."""
        with self._turn:
            if self.conversations.get(conversation_id) is None:
                return False
            if conversation_id != self.conversation_id:
                history = self.conversations.history(conversation_id)
                chat.reset()
                chat.load(history)
                self.conversation_id = conversation_id
                self.request = next((text for role, text in reversed(history) if role == "user"), self.request)
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
        """On the sites chosen for her and the phones (online.chosen_sources), all at once and merged, as the phone
        shows them: she does not choose the sites herself. Those that could not be searched are in unavailable, with
        why; when none could be, it is an error, which she says (FINDING_ONLINE), with the sites searched and those
        turned off, which only the user can turn on: she does not say she tries one of them herself."""
        sources = online.chosen_sources(self.lib)
        print(f"  · searching {', '.join(online.SOURCES[s] for s in sources)}: {query}", flush=True)
        results, failed = online.search_sites(query, max_results, sources)
        unavailable = [{"site": online.SOURCES[e.source], "reason": e.reason} for e in failed]
        if len(failed) == len(sources):
            return {"error": "; ".join(f"{online.SOURCES[e.source]}: {e}" for e in failed), "unavailable": unavailable,
                    "searched": [online.SOURCES[s] for s in sources],
                    "off": [name for s, name in online.SOURCES.items() if s not in sources]}
        print(f"    {len(results)} found", flush=True)
        for e in failed:
            print(f"    {online.SOURCES[e.source]} not searched ({e.reason}): {e}", flush=True)
        note = {"unavailable": unavailable} if unavailable else {}
        if not results:  # often too many words for the sites: the same ones again would find nothing again
            return {"results": [], "instruction": "Nothing found for these words: search once more with fewer, "
                                                  "such as the piece and \"piano\".", **note}
        for r in results:  # made from it, or from its copy on another site (online.merged)
            known = next((k for k in map(self.lib.by_source, r["urls"]) if k and k["file_exists"]), None)
            r["in_library"] = known["id"] if known else None
        keys = ("url", "channel", "duration_s", "views", "description", "in_library")
        return {"results": [{"title": r["video_title"], "site": online.SOURCES[r["site"]], **{k: r[k] for k in keys},
                             "flags": online.describe(r["flags"])} for r in results], **note}

    def transcribe_url(self, url: str, piece: str = "", artist: str = "", performer: str = "", aliases: str = "",
                       tags: str = "", alternatives: list | None = None) -> dict:
        if not re.match(r"https?://", url.strip()):
            return {"status": "failed", "error": "url must start with http:// or https://"}
        versions = [{"url": str(v["url"]).strip(), "performer": str(v.get("performer") or "").strip()}
                    for v in alternatives or []
                    if isinstance(v, dict) and re.match(r"https?://", str(v.get("url", "")))]
        return self._transcribe(url, piece=piece, artist=artist, performer=performer, aliases=aliases, tags=tags,
                                versions=versions[:OTHER_VERSIONS])

    def transcribe_local(self, path: str, piece: str, artist: str = "", performer: str = "", aliases: str = "",
                         tags: str = "") -> dict:
        if not Path(path).expanduser().is_file():
            return {"status": "failed", "error": f"file not found: {path}"}
        return self._transcribe(path, piece=piece, artist=artist, performer=performer, aliases=aliases, tags=tags)

    def _transcribe(self, source: str, **names) -> dict:
        """Through the same queue as the app's downloads, named the same way. A piece made before is there at once;
        any other is prepared in the background, and she is free meanwhile (_prepared). Sent, it belongs to what this
        request asks to hear, and an earlier request's pieces still being made no longer play by themselves."""
        if self.downloads is None:
            from .downloads import Downloads

            self.downloads = Downloads(self.lib)
        job = self.downloads.add(source, query=self.request, **names)
        if job.finished.is_set():
            if job.result is None:
                return {"status": "failed", "error": job.error or job.status}
            return {"status": "done", **_brief(job.result)}
        self.preparing_now.append(job.id)
        waited = job.id in self.preparing  # asked for again: one waits for it, as the latest request asks to hear it
        self.preparing[job.id] = (job, self.asked_now)
        self.lineup = self.asked_now
        if self.asked_now.sent is None:  # the first she sends: her news is of it (Lineup)
            self.asked_now.sent, self.asked_now.named = time.monotonic(), (job.id, job.source, job.performer)
        if not waited:
            threading.Thread(target=self._prepared, args=(job,), daemon=True).start()
        return {"status": "preparing", "title": job.public()["title"],
                "instruction": "It is being prepared in the background. Once ready it plays by itself, after what "
                               "this request plays, unless something else is chosen to play meanwhile; then the user "
                               "is told it is ready. Do not call play_recording for it, and your news of it says only "
                               "that you found it and whose performance it is, nothing of waiting or of when it "
                               "plays. Answer in "
                               f"{self.answer_in()}."}  # (found and prepared, she was slipping into Chinese)

    def _asked(self) -> None:
        """As a request ends: what it asks to hear, as it leaves what plays (_ready)."""
        self.asked_now.moves = self.moves

    def _prepared(self, job) -> None:
        """A piece prepared in the background, once it can play (as it is made, or made) or could not be made: when
        she is free (_ready). One that plays as it is made is followed until it is made: not made after all, its next
        version (Downloads._next_version) is ready in its turn, in its place (went); with none, she says it could not
        be made."""
        went = False  # a version of it that played as it was made went
        while True:
            job.playable.wait()
            following = self._when_free(lambda: self._ready(job, went))
            if following is None:
                return
            rec, lineup = following
            if rec is not None:  # until it is made, or its version goes (another is tried, or it ended)
                self.downloads.wait(lambda: job.finished.is_set() or job.source != rec["source_url"])
                if job.status == "done" and job.source == rec["source_url"]:
                    return
                went = True
            with self._turn:
                if rec is not None and rec["id"] in self.prepared:  # gone, its number free for another piece
                    self.prepared.remove(rec["id"])
                if job.id in self.preparing:  # asked for again meanwhile: that request waits for it
                    return
                self.preparing[job.id] = (job, lineup)

    def _when_free(self, then):
        """then(), when she is free: no request she is answering, and voice waiting for her name. Returns what it
        returns."""
        while True:
            self.may_play.wait()
            with self._turn:
                if self.may_play.is_set():
                    return then()

    def _ready(self, job, went: bool = False) -> tuple | None:
        """What she does with it once it can play (_place), remembered (prepared); or, once it ended, that it could not
        be made, which stops nothing (failed), and a piece held for it plays on (_play_on). Returns what she follows
        while it is made (_prepared): its record being made, or None when its version went just now, and the Lineup of
        the request that sent it; None once she is done with it."""
        _, lineup = self.preparing.pop(job.id)
        rec = self.lib.get(job.recording) if job.recording is not None else None
        if rec is None:
            if not job.finished.is_set():
                return None, lineup
            if job.status == "failed":  # not one stopped in the app
                self._not_made(job)
            self._play_on()
            return None
        self.prepared.append(rec["id"])
        self._place(job, rec, lineup, went)
        return (rec, lineup) if rec["making"] else None

    def _place(self, job, rec: dict, lineup: Lineup, went: bool) -> None:
        """Its request still the latest to ask for pieces, it joins the list where its pieces play, after them; or,
        nothing of it playing, it plays: at the end of their list once it is over, or once what played of it went
        with nothing changed since it began (went: a version of it that played as it was made was not made), or by
        itself if nothing has changed since the request (_start: nor on the phone), with nothing said when her news of
        it was its line (_announce), and in another version than her words named, the line that says so in place of
        any (_other_version). Else she says it is ready."""
        player = self._get_player()
        if player.current_id == rec["id"]:
            return  # chosen meanwhile, by her or on the phone: nothing to say
        theirs = lineup is self.lineup and lineup.listed == player.listed  # the list its request's pieces are in
        if theirs and player.line_up(rec["id"], lineup.ids, lineup.listed):
            lineup.ids.append(rec["id"])
            self.piano_news({"status": "lined_up", "title": rec["title"]})
            self.keep("piece", rec["title"], rec["id"])
            return
        unchanged = lineup is self.lineup and lineup.listed is None and self.moves == lineup.moves
        in_its_place = went and player.current_id is None and (self.moves, self.taps) == (lineup.moves, lineup.taps)
        if not (theirs and (player.ended or in_its_place) or unchanged):
            self.piano_news({"status": "not_started", "title": rec["title"]})
            return
        # After their list played to its end, what was done on the phone within it does not hold it back.
        self.taps_asked, self.played_now, self.asked_now = self.taps if theirs else lineup.taps, [], lineup
        self.held_piece = None  # it takes the place of one held for it
        try:
            result = self._start(PlaybackRequest(Path(rec["midi_path"]), rec["title"], recording_id=rec["id"]))
        except Exception as exc:  # said, as for a typed request's piece (_started)
            print(f"  (playing failed: {type(exc).__name__}: {exc})", flush=True)
            result = {"error": "failed"}
        finally:
            self.asked_now = None
        playing = result.get("status") == "playing"
        other = self._other_version(lineup, job.id, rec["source_url"], rec["performer"]) if playing else ""
        if other:
            print(f"assistant> {other}\n", flush=True)
            self.keep("assistant", other)
        elif not (lineup.announced and playing):
            self.piano_news(result)
        if playing:
            lineup.announced = False  # her news was its line; a version in its place later has its own
            lineup.moves, lineup.taps = self.moves, self.taps  # what a version in its place goes by (in_its_place)
        for rec_id, title in self.played_now:
            self.keep("piece", title, rec_id)

    def _to_start(self):
        """The piece she is preparing that starts by itself once it can play (_place): sent by the latest request that
        chose pieces or sent any to be prepared, none of whose pieces plays, with nothing changed since, by her or on
        the phone; or None."""
        lineup = self.lineup
        if lineup is None or lineup.listed is not None or (self.moves, self.taps) != (lineup.moves, lineup.taps):
            return None
        return next((job for job, of in list(self.preparing.values())
                     if of is lineup and (not job.finished.is_set() or job.status == "done")), None)

    def _play_on(self) -> None:
        """A piece held for one of hers to start in its place (held_piece) plays on once none is to start, if nothing
        changed since, by her or on the phone."""
        if self.held_piece is None or self._to_start() is not None:
            return
        (piece, at, moves, taps), self.held_piece = self.held_piece, None
        player = self.player
        if (self.moves, self.taps) == (moves, taps) and player.state == "paused" and player.current == piece:
            try:
                player.look_for_piano()
                player.go_on(at)
            except LookupError as exc:
                self.piano_news(self.piano_missing(exc))
            except FileNotFoundError:  # its file was deleted while it waited
                self.piano_news({"error": "recording_unavailable"})

    def piece_to_start(self) -> bool:
        """Whether a piece she is preparing starts by itself once it can play (_to_start). Voice then waits for her
        name after her reply, as while the piano plays, so that it starts as soon as it can, and a piece paused while
        she listened does not play on before it does."""
        return self._to_start() is not None

    def _not_made(self, job) -> None:
        """She says it could not be made, and remembers it (failed)."""
        title = job.public()["title"]
        self.failed.append(_named(job))
        self.piano_news({"error": "not_ready", "title": title} if title else {"error": "link_not_ready"})

    def _get_player(self):
        if self.player is None:
            from .playback import Playback

            self.player = Playback(self.lib)  # opens the output each time playback starts
        return self.player

    def play(self, id: int | None = None, from_start: bool = False) -> dict:
        """A new piece (id), the one waiting to be played again (pending), or the one in the player: from where it
        was, or from the start. A new piece after another this request chose follows it (lined_up)."""
        self._touch()
        if id is not None:
            rec = self.lib.get(int(id))
            if not rec:
                return {"error": "recording_not_found"}
            if not (rec["file_exists"] or rec["making"]):
                return {"error": "recording_unavailable"}
            lined_up = self.asked_now is not None and bool(self.asked_now.ids or self.after_reply)
            return self._start(PlaybackRequest(Path(rec["midi_path"]), rec["title"], recording_id=int(id),
                                               lined_up=lined_up))
        if self.pending:
            request = replace(self.pending, from_s=0.0) if from_start else self.pending
            rec = self.lib.get(request.recording_id) if request.recording_id is not None else None
            if not (rec and (rec["file_exists"] or rec["making"]) if request.recording_id is not None
                    else request.path.is_file()):
                self.pending = None
                return {"error": "recording_unavailable"}
            return self._start(request, from_start)
        if self.player and self.player.current:
            path, title = self.player.current
            if not self.player.playable(path):
                return {"error": "recording_unavailable"}
            position = self.player.position if self.player.state in {"paused", "playing"} and not from_start else 0.0
            return self._start(PlaybackRequest(path, title, position, self.player.current_id), from_start,
                               going_on=self.player.state == "playing" and not from_start)
        return {"error": "nothing_to_play", "instruction": "No piece is paused or waiting to go on. If the user named "
                "one, play it as a new piece (library_matches, or search_library first); if not, ask what they would "
                "like to hear."}

    def _start(self, request: PlaybackRequest, from_start: bool = False, going_on: bool = False) -> dict:
        """Play the request, saying so first: a piece from its list (a playlist's, or else alone), a step in the
        list that plays, or the piece in the player again; or line it up after what its request chose before it,
        saying nothing. going_on: it is playing already, only looked at. With the piano away nothing changes, and
        the request waits to be retried (pending). The phone's player used since the request began (while she found
        and transcribed a piece, say), the user's choice stands: nothing is started, and she says it is ready (the
        very piece chosen there, it plays). (Nor, voice, does a piece paused while she listened play on by itself:
        finish_interaction.)"""
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
                                   f"want it. Answer in {self.answer_in()}."}
        if request.lined_up:  # it follows the piece this request plays: as that one starts, it joins its list
            if self.typed:
                self._when_replied(request, from_start)
            else:
                self._play(request, from_start)
            return {"status": "lined_up", "title": request.title,
                    "instruction": "It plays after what this request chose before it (Playing Next on the phone shows "
                                   f"it), not now. Answer in {self.answer_in()}."}
        how = request.moved or ("replay" if request.replay else
                                ("playlist_shuffled" if request.mode == "shuffle" else "playlist") if request.playlist
                                else "restart" if from_start else "resume" if request.from_s > 0 else "playing")  # lines.py
        announced = False
        try:
            player = self._get_player()
            if going_on:
                player.check_connection()
                self._chose([player.current_id])
                return {"status": "playing", "how": "now_playing", "title": request.title,
                        "from": clock(player.position)}
            player.look_for_piano()  # LookupError when it is not there: nothing is said or changed
            if self.speak:  # aloud, what she plays, before it sounds (and while the phone can still ask what plays)
                if self.held and self._named(request, self.held):  # her sentence about the pieces asked for, at once
                    self.tell(self.held)  # in place of her line, heard to the end before it sounds
                    self.held = ""
                elif self._named(request, self.looked_for):  # her words as she looked for it named it: not twice
                    self.speak("")  # (SPEC M5.5), and they are heard to the end before it sounds
                else:
                    self.tell(line(how, self.chinese(), request.playlist or spoken_title(request.title)))
                    self.line_said = True
                announced = True
            if self.typed:  # it starts once her reply is there (ask)
                self._when_replied(request, from_start)
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
        return {"status": "playing", "how": how, "title": player.current[1], "from": clock(player.position),
                "introduction_spoken": announced, **({"playlist": request.playlist} if request.playlist else {})}

    def _play(self, request: PlaybackRequest, from_start: bool) -> None:
        player, lineup = self.player, self.asked_now
        if request.lined_up:  # after what its request chose before it, in that piece's list
            if lineup is not None and player.line_up(request.recording_id, lineup.ids, lineup.listed):
                lineup.ids.append(request.recording_id)
                self.played_now.append((request.recording_id, request.title))
                print(f"  ↳ {request.title}, lined up", flush=True)
            return
        self.started_now = True
        if request.moved:  # a step in the list that plays: the list stays as it is
            was = player.current_id
            player.go_to(request.recording_id)
            if request.recording_id != was:
                self.played_now.append((request.recording_id, request.title))
            self._chose([request.recording_id])
        elif request.recording_id is None or (request.recording_id == player.current_id and request.scope is None):
            # the piece already in the player, from its file as the library has it now: on from where it was (a
            # resume, a retry: the same listening), or again from its start
            if request.recording_id is not None and not from_start:
                player.go_on(request.from_s)
            else:
                player.start([player.current if request.recording_id is not None else (request.path, request.title)],
                             from_s=request.from_s)
            self._chose([request.recording_id])
        else:  # a playlist plays on in its order; a piece she chose plays alone (the chat is not a play queue), or
            # once the list of its request's pieces is over, at its end
            if request.scope is not None:
                ids, scope = list(request.ids), request.scope
            elif lineup is not None and lineup.listed == player.listed:
                ids, scope = [*player.ids, request.recording_id], player.scope
            else:
                ids, scope = [request.recording_id], {"kind": "conversation", "id": self.conversation_id}
            player.select(request.recording_id, ids, scope, request.from_s, mode=request.mode)
            self.played_now.append((request.recording_id, request.title))
            self._chose(ids if request.scope is not None else [request.recording_id])
            if lineup is not None:  # what this request chose: what it prepares follows it, an earlier one's no more
                self.lineup = lineup
        self.pending = None
        print(f"  ▶ {player.current[1]}  {clock(player.position)}", flush=True)

    def _chose(self, ids: list[int | None]) -> None:
        """The pieces this request plays or goes on with, in the list that plays: what it lines up follows them."""
        if self.asked_now is not None:
            self.asked_now.ids += [i for i in ids if i is not None and i not in self.asked_now.ids]
            self.asked_now.listed = self.player.listed

    def _named(self, request: PlaybackRequest, words: str) -> bool:
        """Whether these words of hers name this piece: its name, or one of its other names."""
        rec = self.lib.get(request.recording_id) if request.recording_id is not None and words else None
        names = [spoken_title(request.title), *((rec or {}).get("aliases") or "").split(",")]
        return any(name.strip() and name.strip().casefold() in words.casefold() for name in names)

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

    def _when_replied(self, request: PlaybackRequest, from_start: bool) -> None:
        """A typed request's piece starts, or is lined up, once her reply is there (ask)."""
        if not self.after_reply:
            self.taps_before = self.taps
        self.after_reply.append(lambda: self._started(request, from_start))

    def _started(self, request: PlaybackRequest, from_start: bool) -> None:
        """A typed request's piece, started after her reply: the piano gone meanwhile, the file, or anything else,
        she says so, as a tool's failure would be said."""
        try:
            self._play(request, from_start)
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

    def _conversation_pieces(self) -> list[dict]:
        """The pieces of this conversation (Conversations.played) that can play: in the library, with their files."""
        if self.conversation_id is None:
            return []
        recs = map(self.lib.get, self.conversations.played(self.conversation_id))
        return [rec for rec in recs if rec and rec["file_exists"]]

    def replay_conversation(self) -> dict:
        """This conversation's pieces again, in their order, from the first: the list that plays now, in order."""
        pieces = self._conversation_pieces()
        if not pieces:
            return {"error": "conversation_empty"}
        first = pieces[0]
        self._touch()
        result = self._start(PlaybackRequest(
            Path(first["midi_path"]), first["title"], recording_id=first["id"], ids=tuple(p["id"] for p in pieces),
            scope={"kind": "conversation", "id": self.conversation_id}, mode="order", replay=True))
        return result if result.get("error") else {**result, "pieces": len(pieces)}

    def save_conversation_as_playlist(self, name: str) -> dict:
        """A new playlist of this conversation's pieces, in their order, under the name she chose; what plays goes
        on. As the library's playlists do, it keeps a piece whose file is away for now."""
        ids = self.conversations.played(self.conversation_id) if self.conversation_id is not None else []
        if not ids:
            return {"error": "conversation_empty"}
        found, playlists = self._playlist(name)
        if found is not None:
            return {"error": "playlist_exists", "playlists": [p["name"] for p in playlists]}
        try:
            made = self.lib.new_playlist(name, ids)
        except ValueError as exc:  # no name, or too long
            return {"error": str(exc)}
        if self.on_playlists:
            self.on_playlists()
        return {"status": "created", "name": made["name"], "pieces": len(made["ids"])}

    def rename_playlist(self, name: str, new_name: str) -> dict:
        """One of the user's playlists under another name; its description stays."""
        found, playlists = self._playlist(name)
        if found is None:
            return {"error": "playlist_not_found", "playlists": [p["name"] for p in playlists]}
        other, _ = self._playlist(new_name)
        if other is not None and other["id"] != found["id"]:
            return {"error": "playlist_exists", "playlists": [p["name"] for p in playlists]}
        try:
            renamed = self.lib.edit_playlist(found["id"], new_name, found["description"])
        except ValueError as exc:
            return {"error": str(exc)}
        if self.on_playlists:
            self.on_playlists()
        return {"status": "renamed", "name": renamed["name"]}

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
        # Her pause or stop, the phone's player used since the request began: what was done there stands, as it does
        # over what she would start (_start).
        if action in {"pause", "cancel"} and self.phone_used():
            return {"status": "changed_on_phone",
                    "instruction": "While you were answering, the user changed what plays on their phone, so it was "
                                   "left as they set it: nothing was paused or stopped. Do not try again: say in a few "
                                   f"words that what they did on their phone stands. Answer in {self.answer_in()}."}
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
        she plays (_start). Nothing after it yet, but a piece of its request still being prepared: she says so."""
        target, how = self.player.landing(direction, around=True) if self.player else (None, "")
        if how == "":
            return {"status": "nothing is playing"}
        if direction > 0 and how in {"alone", "around"} and (coming := self._coming()):
            return {"status": "coming", "title": coming}
        if how == "alone":
            return {"status": "nothing_next"}
        self._touch()
        rec = self.lib.get(target)
        if not rec or not (rec["file_exists"] or rec["making"]):
            return {"error": "recording_unavailable"}
        moved = "from_first" if how == "around" else "restart" if target == self.player.current_id \
            else "next" if direction > 0 else "previous"
        return self._start(PlaybackRequest(Path(rec["midi_path"]), rec["title"], recording_id=target, moved=moved))

    def _coming(self) -> str:
        """The first piece still being prepared for the pieces that play (the latest request's, in their list), by
        name; or "" (none, or a link not named yet)."""
        lineup = self.lineup
        if lineup is None or lineup.listed != self.player.listed:
            return ""
        return next((job.public()["title"] for job, of in self.preparing.values()
                     if of is lineup and not job.finished.is_set() and job.public()["title"]), "")

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

    def clear_match(self, query: str, found: list[dict]) -> dict | None:
        """Of what search_library found for a query (found, as she sees it), the one recording that is clearly the piece
        searched for: the only one matched on a whole word (MATCH_SCORE) whose own name, the piece in its title or one
        of its other names (aliases), the query says whole (said_whole); not the piece in the player, which, asked for
        again, is wanted in another version. None when there are several, or none."""
        def names(r: dict) -> list[str]:
            return [r["piece"], *re.split(r"[;,；，]", r.get("aliases") or "")]
        current = getattr(self.player, "current_id", None)
        clear = [r for r in found if r.get("score", 0) >= MATCH_SCORE and r.get("file_exists") and r["id"] != current
                 and any(said_whole(name, query) for name in names(r))]
        return clear[0] if len(clear) == 1 else None

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
        (_ready). She sends it to the conversation, as she would text it, but not again while a line of hers for it is
        the last thing there (the piano gone, Core sends it and voice says it): that one is the line. Returns it, for
        where she speaks."""
        (key, piece), chinese = self._reply_line(result), self.chinese()
        with self._news:
            kept = self.last_kept
            if kept and kept[:2] == (self.conversation_id, "assistant") and kept[2] in every(key, chinese, piece):
                return kept[2]
            text = line(key, chinese, piece)
            print(f"assistant> {text}\n", flush=True)
            self.keep("assistant", text)
            return text

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
        except online.OnlineError as exc:  # why, for her to say (FINDING_ONLINE), and where
            result = {"error": str(exc), "reason": exc.reason, "site": online.SOURCES.get(exc.source, "")}
        except TypeError as exc:
            result = {"error": f"bad arguments: {exc}"}
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        # The model words a failure for the listener; the reason stays visible here (piano_missing gives its own).
        if result.get("error") and result["error"] != "playback_connection_unavailable":
            print(f"  · {name} failed: {result['error']}", flush=True)
        if name in ANSWERED_IN and not result.get("error"):  # words in other languages: hers stay in the request's
            result["answer_in"] = self.answer_in()
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
        key, piece = self._reply_line(result)
        return line(key, self.chinese(), piece)

    def _reply_line(self, result: dict) -> tuple[str, str]:
        """Which of her lines a playback result takes (REPLY_LINES), and the name it says: the playlist's, or the
        piece's."""
        if result.get("error"):
            key = REPLY_LINES.get(result["error"], "failed")
        else:
            key = REPLY_LINES.get(result.get("status"), result.get("how", "now_playing"))
        if key == "ready" and result.get("playlist"):  # a playlist is not prepared: it gave way to the phone
            key = "playlist_ready"
        return key, result.get("playlist") or spoken_title(result.get("title", ""))

    # ---- conversation ------------------------------------------------------------------------
    def ask(self, chat, text: str, speak=None, typed: bool = False, taps: int | None = None,
            paused_for_her: bool = False) -> str:
        """Answer one request and keep it, her lines and the pieces she played in the conversation. speak says her lines
        aloud as they come (voice), and her reply about a piece she sends to be prepared is its line, said once its
        recording comes in, after which it starts with nothing more said (_announce); typed marks a request typed in the
        app, answered on screen. taps: the phone's player as it was when the request was made, if it waited its turn
        (Core.type). paused_for_her: the piece playing was paused for her to listen (voice), and plays on after her
        reply unless she pauses or cancels it. Returns her reply, for voice to say: spoken, with the line after it that
        says the piece is in another version than her news named, when it is (_announce)."""
        with self._turn:
            # Each request resends the whole conversation: after a long quiet spell, start afresh.
            if self.replied_at is not None and self.clock() - self.replied_at > NEW_CONVERSATION_AFTER_S:
                self.new_conversation(chat)
            self.speak, self.played_now, self.typed, asked = speak, [], typed, time.monotonic()
            self.paused_for_her = paused_for_her
            self.preparing_now, self.started_now, self.looked_for, self.held, self.line_said = [], False, "", "", False
            self.said_now = ""
            self.taps_asked = self.taps if taps is None else taps
            self.asked_now = lineup = Lineup(self.taps_asked)
            self.keep("user", text)
            if self.on_busy:
                self.on_busy(True)
            try:
                try:
                    answer = self._answer(chat, text, typed)
                except Exception:
                    self._start_after_reply()  # what she was to play still plays, as it would have when spoken
                    if not self.started_now and self.lineup is lineup:  # she says she could not answer: what it
                        self.lineup = None  # sent to be prepared does not start by itself, and is said to be ready
                    raise
                if lineup.named and not says_name(f"{self.said_now} {answer}", lineup.named[2]):
                    lineup.named = None  # her words did not name the version she chose by its performer
                other = ""
                if typed:
                    time.sleep(max(0.0, asked + TYPED_REPLY_S - time.monotonic()))
                elif speak and answer:
                    other = self._announce()
                if answer:
                    self.keep("assistant", answer)
                if other:  # a line of its own, said right after her news
                    self.keep("assistant", other)
                if self.after_reply:
                    if self.on_busy:
                        self.on_busy(False)  # the dots go with her reply
                    time.sleep(TYPED_PLAY_S)
                    self._start_after_reply()
                for rec_id, title in self.played_now:
                    self.keep("piece", title, rec_id)
                return f"{answer} {other}".strip()
            finally:
                self._asked()
                self.speak, self.typed, self.paused_for_her = None, False, False
                self.after_reply, self.asked_now = [], None
                self.replied_at = self.clock()
                if self.on_busy:
                    self.on_busy(False)

    def _announce(self) -> str:
        """Spoken, her reply about a piece this request sent to be prepared, which then starts by itself (_to_start),
        is her news of it, said in place of her line: once the recording of the version being made begins to come in
        (the first of its download: its progress) or is there (a file, or downloaded before), or NEWS_WAIT_S after she
        sent it at the latest. The piece starts once it can play and her news has been said, whichever comes later,
        with nothing more said (Lineup.announced): voice waits for her name after it, as while the piano plays
        (piece_to_start), so that the piece follows it at once. Returns the line that says it is in another version
        than her news named, said after it (the one she chose could not be had: _other_version), or ""."""
        self._asked()  # its tools are done: what plays is as it leaves it
        lineup = self.asked_now
        job = self._to_start() if self.lineup is lineup else None
        if job is None:
            return ""
        self.downloads.wait(lambda: job.progress > 0 or job.status == "converting" or job.finished.is_set(),
                            lineup.sent + NEWS_WAIT_S - time.monotonic())
        if self._to_start() is not job:  # it does not start by itself after all: it could not be made, or a tap
            return ""
        lineup.announced = True
        return self._other_version(lineup, job.id, job.source, job.performer)

    def _other_version(self, lineup: Lineup, job_id: int, source: str, performer: str) -> str:
        """The line that says a piece is in another version than the one her words named by its performer
        (Lineup.named), naming the one it is in, which is the one named from then on; "" when it is in that one, or
        they named none."""
        named = lineup.named
        if named is None or named[0] != job_id or named[1] == source:
            return ""
        lineup.named = job_id, source, performer
        if not performer:
            return line("another_version", self.chinese())
        return line("other_version", self.chinese(), performer)

    def unfounded(self, words: str, called: set[str]) -> str:
        """What the model is told of its reply to the request (HELD_BACK), when the reply is not to be said: it says
        that something is done or being done, or that she goes to look, with no tool doing it (claims; that the piece
        is paused, it may say when it is, not only while she listens); or, a piece asked for and nothing looked at, it
        asks which piece is meant. "" for any other reply. called: the tools called for the request."""
        said = claims(words)
        if self.player and self.player.state == "paused" and not self.paused_for_her:
            said.discard("paused")
        if said and not called - LOOKS:
            held = "claims"
        elif not called and asks_which(words) and asks_to_hear(self.request):
            held = "asks"
        else:
            return ""
        return HELD_BACK[held].format(reply=json.dumps(words[:300], ensure_ascii=False), language=self.answer_in())

    def could_not_answer(self) -> str:
        """The model failed (ask raised; the request is forgotten): she says so, in the conversation too, unless what
        was asked for plays already. Returns her line, for where she speaks, or "" then."""
        if self.started_now:
            return ""
        text = line("no_answer", self.chinese())
        self.keep("assistant", text)
        return text

    def _answer(self, chat, text: str, typed: bool) -> str:
        self.request = text
        self.selectable_ids.clear()
        progress_spoken = False
        playback_announced = False
        # An action besides playing (a playlist made, the volume), or a pause or stop after a piece her line introduced
        # (else that piece would stand paused or stopped with nothing said of it): her words say it.
        confirms = False
        action = match_command(text)
        if action is not None:
            log.debug("a short request, no model: %s", action)
            return self.short_request(action)

        # library_matches are for the request's own words only: the piece may be in the library under its other
        # names, so she looks online only as she searches the library herself (in the same response) or after; and
        # once searching online failed (no site could be searched), not again in this request, as she is told.
        library_searched = online_failed = False

        def refused(call) -> str:
            """Why a call of hers is not run, as its result: looking online too soon, or again; or ""."""
            if call.name != "search_online":
                return ""
            return ONLINE_TOO_SOON if not library_searched else ONLINE_FAILED if online_failed else ""

        called: set[str] = set()  # the tools the model called for this request
        held = False  # a reply of the model's held back, and the model asked for it again: once a request (unfounded)

        def model_reply(fn, *args):
            nonlocal held
            started = time.monotonic()
            reply = fn(*args)
            log.debug("model, %.1f s: %s", time.monotonic() - started,
                      ", ".join(call.name for call in reply.tool_calls) or "the answer")
            if reply.tool_calls or held or not (note := self.unfounded(reply.text, called)):
                return reply
            held = True
            log.debug("held back: %s", reply.text[:300])
            return model_reply(chat.ask_again, note)

        try:
            model_text = text
            state = {"typed": True} if typed else {}
            state["answer_in"] = self.answer_in()
            state["library_matches"] = {"matched_on": "the request's own words only, not the piece's other names",
                                        "recordings": self.library_matches(text)}
            if self.pending:
                state["pending"] = {"title": self.pending.title, "from_s": self.pending.from_s,
                                    "status": "waiting to retry playback"}
            if self.player and self.player.current:
                state["current"] = {"title": self.player.current[1], "state": self.player.state,
                                    "position_s": self.player.position}
                if self.paused_for_her and self.player.state == "paused":  # then plays on (voice.finish_interaction)
                    state["current"]["state"] = ("paused only while you listen: it plays on after your reply unless "
                                                 "you pause or cancel it with control_playback")
                rec = self.lib.get(self.player.current_id) if getattr(self.player, "current_id", None) else None
                if rec:  # its names as the library keeps them: the title need not say who plays it
                    state["current"].update(id=rec["id"], **title_parts(rec["title"], rec["composer"], rec["performer"]))
            waiting = [job for job, _ in self.preparing.values()]  # one finished while she was busy is as it ended
            if making := [_named(job) for job in waiting if not job.finished.is_set()]:
                state["preparing"] = making
            made = [*self.prepared, *(job.result["id"] for job in waiting if job.finished.is_set() and job.result)]
            prepared = [rec for rec in map(self.lib.get, made) if rec and (rec["file_exists"] or rec["making"])]
            if prepared:
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
            said_ahead = ""
            news = ""  # her words as she sent a piece to be prepared: her news only if her reply after it has none
            plan = ""  # her sentence about several pieces, not said as the first began: said before her reply

            def ahead(words: str, tool: str) -> bool:
                """Words the model wrote before its first tool call, said at once while it goes on (True): not those
                that come with an action (her line follows it), with looking online before the library is searched
                (said once the response is whole if it searches the library too, below), or with looking online once
                she has said a sentence (one only, before her news); nor those that come as she sends a piece to be
                prepared, held as her news (below). Only that first call is known yet; she is told to make an action
                alone (VOICE_NOTE)."""
                nonlocal said_ahead, progress_spoken
                if tool in ACTIONS or tool == "search_online" and (not library_searched or progress_spoken) \
                        or tool in PREPARES and not self.started_now:
                    return False
                self.tell(words, meanwhile=True)
                said_ahead, progress_spoken = words, True
                return True
            chat.before_tool = ahead
            reply = model_reply(chat.send_user, model_text)
            playback_result = None
            for _ in range(MAX_TOOL_ROUNDS):
                if not reply.tool_calls:
                    break
                # Words that come with an action are not said before it is done: her reply follows it, or, with a piece
                # she plays among others asked for, they are her sentence about them all, said as it begins (_start).
                # Nor are those that come with looking online too soon, which she does not do, or once she has said a
                # sentence: one only before her news. Those that come as she sends a piece to be prepared, none of this
                # request's playing, are held: her reply after it is her news (spoken, said as its line: _announce), and
                # they are only when it has no words; with one made before, there at once, they are said once its tools
                # have run, before it plays. Nor again what she said already, as the model went on to its tools. What
                # she says as she looks in the library, as she finds one made before, or of several pieces, may name the
                # piece she then plays (_named).
                calls = reply.tool_calls
                library_searched = library_searched or any(c.name == "search_library" for c in calls)
                words = reply.text.strip()
                said = said_ahead if said_ahead and words.startswith(said_ahead) else ""
                words, said_ahead = words[len(said):].strip(), ""
                several = any(c.name == "play_recording" for c in calls) \
                    and sum(c.name in PIECE_CALLS for c in calls) > 1
                online_again = progress_spoken and any(c.name == "search_online" for c in calls)
                sent = len(self.preparing_now)
                hold = bool(words) and not several and not self.started_now \
                    and any(c.name in PREPARES for c in calls)  # her news, if a piece is sent (below)
                if not hold and words and not online_again \
                        and not any(c.name in ACTIONS or refused(c) for c in calls):
                    self.tell(words, meanwhile=True)
                    progress_spoken, said = True, said + words
                elif several:
                    self.held = words
                if several or any(c.name == "search_library" for c in calls):
                    self.looked_for += said
                results = []
                found = None  # what this response's search_library found clearly (clear_match): not looked for online
                called.update(call.name for call in reply.tool_calls)
                for call in reply.tool_calls:
                    if why := refused(call):
                        log.debug("search_online: refused, %s", "again" if online_failed else "the library not searched")
                        results.append((call, why))
                        continue
                    if call.name == "search_online" and found is not None:  # what the library has waits for no web
                        log.debug("search_online: not run, the library has it: %s", found["piece"])
                        results.append((call, json.dumps({"status": "not_searched", "in_library": {
                            k: found[k] for k in ("id", "piece", "artist", "performer")}, "instruction": IN_LIBRARY},
                            ensure_ascii=False)))
                        continue
                    if not progress_spoken and call.name in WAITS:  # going online is not started silently
                        self.tell(line(WAITS[call.name], self.chinese()), meanwhile=True)
                        progress_spoken = True
                    raw = self.run_tool(call.name, call.arguments)
                    confirms = confirms or call.name in ACTIONS - PLAYS or playback_announced \
                        and call.name == "control_playback" and call.arguments.get("action") in {"pause", "cancel"}
                    if call.name == "search_online" and "searched" in json.loads(raw):  # no site could be searched
                        online_failed = True
                    if call.name == "search_library" and found is None:
                        query = str(call.arguments.get("query", ""))
                        found = self.clear_match(query, json.loads(raw).get("results", []))
                    results.append((call, raw))
                    if call.name in PLAYS:
                        played = json.loads(raw)
                        if played.get("status") != "lined_up":  # her line, if any, is for the piece that plays
                            playback_result = played
                        if played.get("introduction_spoken"):
                            progress_spoken = playback_announced = True
                        if played.get("error") in {"playback_connection_unavailable", "recording_unavailable", "recording_not_found"}:
                            chat.discard_turn()  # the unfinished tool exchange; earlier turns and the pending piece stay
                            return self.playback_reply(played)
                if hold and len(self.preparing_now) > sent:
                    news = news or words  # of the first piece sent, the one that starts by itself (Lineup.sent)
                elif hold:  # nothing sent: made before (or not to be had), nothing waits for it
                    self.tell(words, meanwhile=True)
                    progress_spoken, self.looked_for = True, self.looked_for + words
                plan, self.held = plan or self.held, ""
                reply = model_reply(chat.send_tool_results, results)
            if reply.tool_calls:  # still calling tools after MAX_TOOL_ROUNDS: give this request up
                chat.discard_turn()
            # Her words said it all as it began to play, and so did her line about a piece asked for alone; after her
            # line about the first of several (lined up after it, or sent to be prepared), her reply says the rest.
            if playback_announced and not confirms and not (self.line_said and (len(self.played_now) > 1
                                                                                or self.preparing_now)):
                return news
            words = reply.text.strip()
            if said_ahead and words.startswith(said_ahead):  # given up with words said already, as it went on
                words = words[len(said_ahead):].strip()
            if not words:  # her words as she sent a piece to be prepared are her news; her line says what plays,
                words = news  # unless her sentence about several pieces did
                if playback_result and not plan:
                    words = f"{words} {self.playback_reply(playback_result)}"
            return f"{plan} {words}".strip()
        except BaseException:  # the model failed, or Control+C: this request is forgotten, the conversation goes on
            chat.discard_turn()
            raise
        finally:
            chat.before_tool = None


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
            answer = assistant.could_not_answer()
        if answer:
            print(f"\nassistant> {answer}\n")


if __name__ == "__main__":
    sys.exit(main())
