"""New pieces: download a recording (or take a local audio file), transcribe it, and file it in the library.

One piece at a time, in the order asked, whether she asked for it (transcribe_url) or you did (the app's download
button): the same work on the same Mac. A recording put in the library folder is transcribed on its own (auto),
after everything asked for. Each piece has a status (queued, downloading, converting) and a progress that goes once
round: the download is the first part of it, the transcription the rest, estimated from how fast this Mac
transcribed before. A recording in the library folder (Library.audio) shows the status of its piece's making.

She may give a piece other versions besides the one she chose (the next good ones of her search): one that cannot
be made (its download, its transcription, a preview only, too long) gives way to the next, in its place in the
queue, and the piece fails only when none could be made.

The transcription's process starts with the piece, so its model loads while the recording downloads. A new piece
can play while the rest of it is transcribed: once enough of it is there (growing.Growing.plays), it has its record,
marked as being made (Library.making), and it is in the library once it is made; if it is not made after all (it
fails, or is stopped), its record goes, and its next version, if it has one, can play in its turn.

A video may be downloaded before it is asked for (fetch_ahead: her first YouTube result, while she chooses, since
YouTube makes a download wait some 5 s): asked for, its piece takes that download as it goes, and its outcome is the
piece's; not asked for, it is let go, and nothing of it stays.
"""

from __future__ import annotations

import contextlib
import itertools
import logging
import re
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import online
from .growing import Growing
from .library import CACHE_DIR, file_result, make_title

log = logging.getLogger(__spec__.name)

AUDIO_DIR = CACHE_DIR / "downloads"  # audio waits here until its piece is in the library; a retry reuses it
DOWNLOAD_SHARE = 0.3  # of the way round, for the download
SECONDS_PER_SECOND = 0.6  # transcription time per second of audio, until this Mac has been timed
TRACKING_PARAMS = {"si", "feature", "pp", "spm_id_from", "vd_source", "share_source", "share_medium",
                   "share_plat", "share_from", "from_spmid", "unique_k", "bbid"}


# Bilibili's own conversion of a video's number (av…) into its id (BV…), which its links and its search now give.
BV_LETTERS = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
BV_PLACES = (8, 7, 0, 5, 1, 3, 2, 4, 6)


def clean_url(url: str) -> str:
    """Canonical form of a pasted link, so the same video is recognized when it comes back: a YouTube video as
    www.youtube.com/watch?v=…, a Bilibili one as www.bilibili.com/video/BV… (?p=… for a part after the first)."""
    parts = urllib.parse.urlsplit(url.strip())
    host = re.sub(r"^(www|m|music)\.", "", parts.netloc.lower())
    path = parts.path.split("/")
    query = urllib.parse.parse_qs(parts.query)
    vid = ""
    if host == "youtu.be":
        vid = path[1] if len(path) > 1 else ""
    elif host == "youtube.com":
        vid = query.get("v", [""])[0] if parts.path == "/watch" else (
            path[2] if len(path) > 2 and path[1] in {"shorts", "live", "embed"} else "")
    if re.fullmatch(r"[\w-]{11}", vid):
        return f"https://www.youtube.com/watch?v={vid}"
    video = re.fullmatch(r"/video/(?:bv([0-9a-z]{10})|av(\d+))/?", parts.path, re.IGNORECASE)
    if host == "bilibili.com" and video:
        bvid = f"BV{video[1]}" if video[1] else bv_of(int(video[2]))
        part = int(query["p"][0]) if query.get("p", [""])[0].isdigit() else 1
        return f"https://www.bilibili.com/video/{bvid}" + (f"?p={part}" if part > 1 else "")
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if k not in TRACKING_PARAMS and not k.startswith("utm_")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), ""))


def bv_of(av: int) -> str:
    """A Bilibili video's id (BV…) from its number (av…)."""
    code, letters = (1 << 51 | av) ^ 23442827791579, [""] * len(BV_PLACES)
    for place in BV_PLACES:
        code, letter = divmod(code, len(BV_LETTERS))
        letters[place] = BV_LETTERS[letter]
    return "BV1" + "".join(letters)


def _video_page(url: str) -> dict:
    """For a YouTube video, what its page says, with the channel's picture (online search); for a Bilibili one a
    search found, its UP主's picture, which no page yt-dlp reads gives; else nothing."""
    if online.video_id(url):
        return online.details([url]).get(url, {})
    avatar = online.avatar_found(url)
    return {"channel_avatar": avatar} if avatar else {}


class Stopped(Exception):
    """The piece was stopped from the app."""


@dataclass
class Ahead:
    """A video's audio downloaded before it is asked for (Downloads.fetch_ahead)."""
    url: str
    audio: tuple[Path, dict] | None = None  # once downloaded: the audio, and the video's details (download_audio)
    error: Exception | None = None  # or why it could not be
    done: threading.Event = field(default_factory=threading.Event)
    let_go: threading.Event = field(default_factory=threading.Event)  # not wanted: it stops, nothing of it stays


def _drop_audio(url: str) -> None:
    """Nothing of a video's download stays."""
    from .download import task_id

    for leftover in AUDIO_DIR.glob(f"{task_id(url)}.*"):
        leftover.unlink(missing_ok=True)


@dataclass
class Job:
    id: int
    source: str  # a video's address, or a local audio file
    piece: str = ""  # its names (library.NAMES); named while it is transcribed (_file) when not given
    artist: str = ""
    performer: str = ""
    aliases: str = ""
    tags: str = ""
    query: str = ""  # what was asked for or searched, to name the piece by
    video: dict = field(default_factory=dict)  # what the search said about the video
    auto: bool = False  # not asked for: a recording put in the library folder, after everything asked for
    # Other versions of the piece, best first ({"url", "performer"}): the next is made when this one cannot be
    versions: list[dict] = field(default_factory=list)
    audio: int | None = None  # the recording in the library folder it is made from (Library.audio)
    status: str = "queued"  # downloading, converting, then done, failed or stopped
    progress: float = 0.0
    result: dict | None = None
    error: str = ""
    recording: int | None = None  # its piece's record: from when it can play as it is made, and once it is made
    stopped: threading.Event = field(default_factory=threading.Event)  # set by stop() only
    finished: threading.Event = field(default_factory=threading.Event)
    # It can play (as it is made, or made), or it ended; a version that could play and went clears it (_transcribe)
    playable: threading.Event = field(default_factory=threading.Event)
    ahead: Ahead | None = None  # its video's download, begun before it was asked for (fetch_ahead), until taken

    @property
    def local(self) -> bool:
        return not re.match(r"https?://", self.source)

    @property
    def title(self) -> str:
        return make_title(self.piece, self.artist, self.performer) if self.piece else ""

    def public(self) -> dict:
        return {"id": self.id, "source": self.source, "title": self.title or self.video.get("video_title", ""),
                "status": self.status, "progress": round(self.progress, 3),
                "duration_s": self.video.get("duration_s"), "audio": self.audio, "recording": self.recording}


class Downloads:
    def __init__(self, lib, *, name_piece=None):
        self.lib = lib
        self.jobs: list[Job] = []  # queued and running, in order
        self.listeners: list = []  # called after every change, on the downloads' thread or the caller's
        self.unmade: list = []  # called with a record's id when its piece played as it was made, and was not made
        self.growing: dict[Path, Growing] = {}  # the pieces being made that can play, by the file each will have
        self.name_piece = name_piece  # (query, video, artists) -> {piece, artist, performer, aliases}, or None: the video's
        self._ahead: Ahead | None = None  # a video downloaded ahead, not asked for yet (fetch_ahead)
        self._speed = SECONDS_PER_SECOND
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        threading.Thread(target=self._work, daemon=True).start()

    def snapshot(self) -> list[dict]:
        with self._cond:
            return [j.public() for j in self.jobs]

    def changed(self) -> None:
        with self._cond:
            self._cond.notify_all()  # those waiting for a piece to change (wait)
        for listener in list(self.listeners):
            listener()

    def wait(self, until) -> None:
        """Until until() is true, looked at again each time a piece changes."""
        with self._cond:
            self._cond.wait_for(until)

    # ---- asking for a piece -------------------------------------------------------------------------
    def add(self, source: str, *, piece: str = "", artist: str = "", performer: str = "", aliases: str = "",
            tags: str = "", query: str = "", video: dict | None = None, auto: bool = False,
            versions: list[dict] = ()) -> Job:
        """Queue a piece, by its names when they are known; a piece already in the library, or already queued,
        is not done twice (one queued on its own and now asked for moves up). A short link is followed first, to the
        video it stands for (OnlineError when it cannot be). versions: other videos of the piece, best first, each
        {"url", "performer"}, made in turn while one cannot be (_next_version)."""
        local = not re.match(r"https?://", source.strip())
        source = str(Path(source).expanduser().resolve()) if local else clean_url(online.resolved(source))
        with self._cond:
            for job in self.jobs:
                if job.source == source:
                    job.auto = job.auto and auto
                    return job
            job = Job(next(self._ids), source, piece.strip(), artist.strip(), performer.strip(), aliases.strip(), tags,
                      query.strip(), dict(video or {}), auto, [dict(v) for v in versions])
            known = self.lib.by_source(source)
            if known and known["file_exists"]:
                job.status, job.result, job.recording = "done", known, known["id"]
                job.finished.set()
                job.playable.set()
                return job
            recording = self.lib.audio_at(Path(source)) if local else None
            if recording:
                job.audio = recording["id"]
                self.lib.set_audio(job.audio, status="queued", error="")
            if self._ahead is not None and self._ahead.url == source:  # downloading already: the piece's own now
                job.ahead, self._ahead = self._ahead, None
            self.jobs.append(job)
            self._cond.notify_all()
        self.changed()
        return job

    def making(self, rec_id: int) -> Job | None:
        """The job making the piece of this record, while it plays as it is made."""
        with self._cond:
            return next((j for j in self.jobs if j.recording == rec_id), None)

    def stop(self, job_id: int) -> None:
        """Stop a piece: it leaves the queue at once, and nothing it made stays (a transcription under way ends at
        once, with its process)."""
        with self._cond:
            job = next((j for j in self.jobs if j.id == job_id), None)
            if job is None:
                return
            self.jobs.remove(job)
            job.stopped.set()
            if job.ahead is not None:  # not taken yet (_audio)
                self._let_go(job.ahead)
                job.ahead = None
            if job.status == "queued":
                job.status, job.error = "stopped", "stopped"
                self._ended(job)
                job.finished.set()
                job.playable.set()  # (or not: it ended)
        self.changed()

    # ---- downloading ahead ----------------------------------------------------------------------------
    def fetch_ahead(self, url: str) -> None:
        """Start downloading a video's audio before it is asked for: her first YouTube result, while she chooses.
        Asked for (add), its piece takes this download as it goes (_audio). One at a time: another before it, not
        asked for, is let go. Not for a video being made, or whose audio is there already."""
        from .download import task_id

        source = clean_url(url)
        with self._cond:
            if self._ahead is not None and self._ahead.url == source:
                return  # on its way already
            self.drop_ahead()
            if any(j.source == source for j in self.jobs) or any(AUDIO_DIR.glob(f"{task_id(source)}.*")):
                return
            ahead = self._ahead = Ahead(source)
        log.debug("downloading ahead: %s", source)
        threading.Thread(target=self._fetch, args=(ahead,), name="ahead", daemon=True).start()

    def drop_ahead(self) -> None:
        """What was downloaded ahead and not asked for (fetch_ahead) is let go: it stops, and nothing of it stays."""
        with self._cond:
            ahead, self._ahead = self._ahead, None
            if ahead is not None:
                self._let_go(ahead)

    def _let_go(self, ahead: Ahead) -> None:
        """(_cond held.) Its download stops where it is, and what it got goes, now or once it ends (_fetch)."""
        ahead.let_go.set()
        if ahead.done.is_set():
            _drop_audio(ahead.url)

    def _fetch(self, ahead: Ahead) -> None:
        from .download import download_audio

        def progress(fraction: float) -> None:
            if ahead.let_go.is_set():
                raise Stopped()

        started = time.monotonic()
        try:
            ahead.audio = download_audio(ahead.url, AUDIO_DIR, progress)
            log.debug("downloaded ahead in %.1f s: %s", time.monotonic() - started, ahead.audio[0].name)
        except Exception as exc:  # the piece's, should it be asked for
            ahead.error = exc
            log.debug("not downloaded ahead (%s): %s", type(exc).__name__, ahead.url)
        with self._cond:
            ahead.done.set()
            if ahead.let_go.is_set():
                _drop_audio(ahead.url)
            self._cond.notify_all()

    def _audio(self, job: Job, progress) -> tuple[Path, dict]:
        """A piece's audio and its video's details: from its download ahead (fetch_ahead), once that is done, as it
        went, failure included; or downloaded now."""
        from .download import download_audio

        with self._cond:
            ahead, job.ahead = job.ahead, None
            if ahead is not None:
                self._cond.wait_for(lambda: ahead.done.is_set() or job.stopped.is_set())
                if not ahead.done.is_set():
                    self._let_go(ahead)
                    raise Stopped()
        if ahead is None:
            return download_audio(job.source, AUDIO_DIR, progress)
        log.debug("its download ahead taken: %s", job.source)
        if ahead.error is not None:
            raise ahead.error
        return ahead.audio

    # ---- the work -------------------------------------------------------------------------------------
    def _work(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: any(j.status == "queued" for j in self.jobs))
                queued = [j for j in self.jobs if j.status == "queued"]
                job = next((j for j in queued if not j.auto), queued[0])  # what was asked for first
                job.status = "converting" if job.local else "downloading"
                if job.audio:
                    self.lib.set_audio(job.audio, status="converting")
            self.changed()
            try:
                job.result = self._make(job)
                job.status, job.recording = "done", job.result["id"]
            except Stopped:
                log.debug("stopped: %s", job.title or job.source)
                job.status, job.error = "stopped", "stopped"
            except Exception as exc:  # this piece fails, unless another version of it can be made; the queue goes on
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
                print(f"  · could not get {job.title or job.source}: {job.error}", flush=True)
            with self._cond:
                if job.status == "failed" and self._next_version(job):
                    continue  # first in the queue still: made next
                if job in self.jobs:
                    self.jobs.remove(job)
            self._ended(job)
            job.finished.set()
            job.playable.set()  # (or not: it ended)
            self.changed()

    def _next_version(self, job: Job) -> bool:
        """A piece that could not be made, from its next version instead (one being made for another piece is left
        out): True when it waits to be made again, from where it was in the queue. Not once stopped; a version in
        the library already makes it done at once (False)."""
        while job.versions and job in self.jobs and not job.stopped.is_set():
            version = job.versions.pop(0)
            source = clean_url(version["url"])
            if any(j.source == source for j in self.jobs):
                continue
            job.source, job.performer, job.video, job.progress, job.error = source, version["performer"], {}, 0.0, ""
            known = self.lib.by_source(source)
            if known and known["file_exists"]:
                job.status, job.result, job.recording = "done", known, known["id"]
                return False
            job.status = "queued"
            print(f"  · trying another version: {job.title or source}", flush=True)
            return True
        return False

    def _ended(self, job: Job) -> None:
        """The recording in the library folder it was made from shows how it went."""
        if job.audio is None:
            return
        if job.status == "done":
            self.lib.set_audio(job.audio, status="done", recording_id=job.result["id"], error="")
        else:
            self.lib.set_audio(job.audio, status="failed", error=job.error)

    def _make(self, job: Job) -> dict:
        print(f"  · getting {job.title or job.source} ready…", flush=True)
        from .pipeline import Options, Transcription

        def progress(fraction: float) -> None:
            self._check(job)
            job.progress = DOWNLOAD_SHARE * fraction
            self.changed()

        with tempfile.TemporaryDirectory() as made, \
                Transcription(Path(made), Options(), output_name="piece", stopped=job.stopped) as apart:
            if job.local:
                return self._file(job, Path(job.source), {}, apart)
            try:
                started = time.monotonic()
                audio, details = self._audio(job, progress)
                log.debug("downloaded in %.1f s: %s", time.monotonic() - started, audio.name)
                rec = self._file(job, audio, details, apart)
            except Exception:
                if job.stopped.is_set():  # whatever the downloader made of the stop, nothing it got stays
                    _drop_audio(job.source)
                    raise Stopped() from None
                raise  # the audio stays for a retry
        for leftover in AUDIO_DIR.glob(f"{audio.stem}.*"):  # no longer needed once the MIDI is in the library
            leftover.unlink(missing_ok=True)
        return rec

    def _file(self, job: Job, audio: Path, details: dict, apart) -> dict:
        """The transcription starts at once; meanwhile the video's page is read (a YouTube video: its channel's
        picture and the rest, a second or two) and the piece named from what is known of it (the model, a second
        or two), and once both are done it is filed under its names."""
        details = dict(details)
        named = threading.Event()  # its names are there: given (she gives them), or found
        if job.piece:
            named.set()

        def meanwhile():
            try:
                details.update(_video_page(job.source))
            except Exception as exc:  # the piece is made all the same, with what the download said of the video
                log.debug("the video's page not read: %s", exc)
            try:
                if not job.piece:
                    started = time.monotonic()
                    self._name(job, details)
                    log.debug("named in %.1f s: %s", time.monotonic() - started, job.title)
                    self.changed()  # the phone shows its name from now on
            finally:
                named.set()
        side = threading.Thread(target=meanwhile, name="naming", daemon=True)
        side.start()
        self._check(job)
        job.status = "converting"
        self.changed()
        rec = self._transcribe(job, audio, job.video.get("duration_s") or details.get("duration_s") or 0, side, named,
                               apart)
        if details:
            self.lib.set_video(rec["id"], details)
            rec = self.lib.get(rec["id"])
        return rec

    def _name(self, job: Job, details: dict) -> None:
        """Name the piece from what was asked and what the video says, or for a recording of yours its file's name;
        the names already given stay. An artist the library has already keeps the name it has there."""
        video = {**details, **job.video, **({"file_name": Path(job.source).stem} if job.local else {})}
        named = None
        if self.name_piece:
            with contextlib.suppress(Exception):
                named = self.name_piece(job.query, video, self.lib.artist_names())
        named = named or {"piece": video.get("video_title") or Path(job.source).stem}
        job.piece = named["piece"]
        for name in ("artist", "performer", "aliases"):
            setattr(job, name, getattr(job, name) or named.get(name, ""))
        job.artist, job.performer = self.lib.known_artist(job.artist), self.lib.known_artist(job.performer)

    def _transcribe(self, job: Job, audio: Path, seconds: float, side: threading.Thread, named: threading.Event,
                    apart) -> dict:
        """The piece made, then filed under its names once the side's work is done (_file). A new piece plays as it
        is made once enough of it is there (Growing.plays) and it is named: its record comes then (one made before,
        whose file is gone, is made again whole)."""
        started, base = time.monotonic(), 0.0 if job.local else DOWNLOAD_SHARE
        ticking = threading.Event()
        growing, anew = Growing(), self.lib.by_source(job.source) is None
        playing: Path | None = None  # the file it will have, once it plays as it is made

        def tick():  # the rest of the way round, as the transcription is expected to go
            while not ticking.wait(0.5):
                done = (time.monotonic() - started) / max(1.0, seconds * self._speed)
                job.progress = base + (1 - base) * min(0.97, done)
                self.changed()

        def part(midi, until, length):  # as it is made (on this thread)
            nonlocal playing
            growing.add(midi, until, length)
            if anew and job.recording is None and not growing.whole and growing.plays():
                named.wait()
                self._check(job)
                rec = self.lib.making(job.title, composer=job.artist, performer=job.performer, aliases=job.aliases,
                                      tags=job.tags, source_type="local" if job.local else "url",
                                      source_url=job.source, duration_s=length)
                playing = Path(rec["midi_path"])
                self.growing[playing] = growing
                job.recording = rec["id"]
                log.debug("plays as it is made, %.1f s of it there: %s", until, job.title)
                job.playable.set()
                self.changed()

        if seconds:
            threading.Thread(target=tick, daemon=True).start()
        try:
            try:  # in a process of its own: the piece playing meanwhile goes on undisturbed
                info = apart.convert(audio, part)
            except InterruptedError:
                raise Stopped() from None
            took = time.monotonic() - started
            log.debug("transcribed in %.1f s on %s: %.0f s of music", took, info.get("device"),
                      info.get("audio_duration_s") or 0)
            self._check(job)
            side.join()
            self._check(job)
            if info.get("audio_duration_s"):  # the whole of it: its process and the model's loading too
                self._speed = took / info["audio_duration_s"]
            produced = Path(info["files"]["midi"])
            if job.recording is not None:
                return self.lib.made(job.recording, produced)
            return file_result(self.lib, produced, job.title, composer=job.artist, performer=job.performer,
                               aliases=job.aliases, tags=job.tags, source_type="local" if job.local else "url",
                               source_url=job.source)
        except BaseException:
            if job.recording is not None:  # it played as it was made: it goes, from the player too
                job.playable.clear()  # until its next version can play, if it has one, or it ends
                gone, job.recording = job.recording, None
                growing.fail()
                self.lib.delete(gone)
                for listener in list(self.unmade):
                    listener(gone)
            raise
        finally:
            ticking.set()
            if playing is not None:  # made, its file plays from now on (a player already on it goes on as it was)
                del self.growing[playing]

    @staticmethod
    def _check(job: Job) -> None:
        if job.stopped.is_set():
            raise Stopped()
