"""New pieces: download a recording (or take a local audio file), transcribe it, and file it in the library.

One piece at a time, in the order asked, whether she asked for it (transcribe_url) or you did (the app's download
button): the same work on the same Mac. A recording put in the library folder is transcribed on its own (auto),
after everything asked for. Each piece has a status (queued, downloading, converting) and a progress that goes once
round: the download is the first part of it, the transcription the rest, estimated from how fast this Mac
transcribed before. A recording in the library folder (Library.audio) shows the status of its piece's making.
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

from .library import CACHE_DIR, file_result, make_title, safe_name

log = logging.getLogger(__spec__.name)

AUDIO_DIR = CACHE_DIR / "downloads"  # audio waits here until its piece is in the library; a retry reuses it
DOWNLOAD_SHARE = 0.3  # of the way round, for the download
SECONDS_PER_SECOND = 0.6  # transcription time per second of audio, until this Mac has been timed
TRACKING_PARAMS = {"si", "feature", "pp", "spm_id_from", "vd_source", "share_source", "share_medium",
                   "share_plat", "share_from", "from_spmid", "unique_k", "bbid"}


def clean_url(url: str) -> str:
    """Canonical form of a pasted link, so the same video is recognized when it comes back."""
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
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if k not in TRACKING_PARAMS and not k.startswith("utm_")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), ""))


def _video_page(url: str) -> dict:
    """For a YouTube video, what its page says, with the channel's picture (online search); else nothing."""
    from . import online

    return online.details([url]).get(url, {}) if online.video_id(url) else {}


class Stopped(Exception):
    """The piece was stopped from the app."""


@dataclass
class Job:
    id: int
    source: str  # a video's address, or a local audio file
    piece: str = ""  # its names (library.NAMES); the piece is named before it is transcribed when not given
    artist: str = ""
    performer: str = ""
    aliases: str = ""
    tags: str = ""
    query: str = ""  # what was asked for or searched, to name the piece by
    video: dict = field(default_factory=dict)  # what the search said about the video
    auto: bool = False  # not asked for: a recording put in the library folder, after everything asked for
    audio: int | None = None  # the recording in the library folder it is made from (Library.audio)
    status: str = "queued"  # downloading, converting, then done, failed or stopped
    progress: float = 0.0
    result: dict | None = None
    error: str = ""
    stopped: threading.Event = field(default_factory=threading.Event)  # set by stop() only
    finished: threading.Event = field(default_factory=threading.Event)

    @property
    def local(self) -> bool:
        return not re.match(r"https?://", self.source)

    @property
    def title(self) -> str:
        return make_title(self.piece, self.artist, self.performer) if self.piece else ""

    def public(self) -> dict:
        return {"id": self.id, "source": self.source, "title": self.title or self.video.get("video_title", ""),
                "status": self.status, "progress": round(self.progress, 3),
                "duration_s": self.video.get("duration_s"), "audio": self.audio}


class Downloads:
    def __init__(self, lib, *, engine=None, name_piece=None):
        self.lib = lib
        self.jobs: list[Job] = []  # queued and running, in order
        self.listeners: list = []  # called after every change, on the downloads' thread or the caller's
        self.name_piece = name_piece  # (query, video, artists) -> {piece, artist, performer, aliases}, or None: the video's
        self._engine = engine
        self._speed = SECONDS_PER_SECOND
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        threading.Thread(target=self._work, daemon=True).start()

    def snapshot(self) -> list[dict]:
        with self._cond:
            return [j.public() for j in self.jobs]

    def changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    # ---- asking for a piece -------------------------------------------------------------------------
    def add(self, source: str, *, piece: str = "", artist: str = "", performer: str = "", aliases: str = "",
            tags: str = "", query: str = "", video: dict | None = None, auto: bool = False) -> Job:
        """Queue a piece, by its names when they are known; a piece already in the library, or already queued,
        is not done twice (one queued on its own and now asked for moves up)."""
        local = not re.match(r"https?://", source.strip())
        source = str(Path(source).expanduser().resolve()) if local else clean_url(source)
        with self._cond:
            for job in self.jobs:
                if job.source == source:
                    job.auto = job.auto and auto
                    return job
            job = Job(next(self._ids), source, piece.strip(), artist.strip(), performer.strip(), aliases.strip(), tags,
                      query.strip(), dict(video or {}), auto)
            known = self.lib.by_source(source)
            if known and known["file_exists"]:
                job.status, job.result = "done", known
                job.finished.set()
                return job
            recording = self.lib.audio_at(Path(source)) if local else None
            if recording:
                job.audio = recording["id"]
                self.lib.set_audio(job.audio, status="queued", error="")
            self.jobs.append(job)
            self._cond.notify_all()
        self.changed()
        return job

    def run(self, source: str, **details) -> dict:
        """Queue a piece and wait for it: the library record, or {"error": ...}."""
        job = self.add(source, **details)
        job.finished.wait()
        return job.result if job.result else {"error": job.error or job.status}

    def stop(self, job_id: int) -> None:
        """Stop a piece: it leaves the queue at once, and nothing it made stays (a transcription under way is
        thrown away when it ends)."""
        with self._cond:
            job = next((j for j in self.jobs if j.id == job_id), None)
            if job is None:
                return
            self.jobs.remove(job)
            job.stopped.set()
            if job.status == "queued":
                job.status, job.error = "stopped", "stopped"
                self._ended(job)
                job.finished.set()
        self.changed()

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
                job.status = "done"
            except Stopped:
                log.debug("stopped: %s", job.title or job.source)
                job.status, job.error = "stopped", "stopped"
            except Exception as exc:  # this piece fails; the queue goes on
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
                print(f"  · could not get {job.title or job.source}: {job.error}", flush=True)
            with self._cond:
                if job in self.jobs:
                    self.jobs.remove(job)
            self._ended(job)
            job.finished.set()
            self.changed()

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
        if job.local:
            return self._file(job, Path(job.source), {})
        from .download import download_audio, task_id

        def progress(fraction: float) -> None:
            self._check(job)
            job.progress = DOWNLOAD_SHARE * fraction
            self.changed()

        try:
            started = time.monotonic()
            audio, details = download_audio(job.source, AUDIO_DIR, progress)
            log.debug("downloaded in %.1f s: %s", time.monotonic() - started, audio.name)
            rec = self._file(job, audio, details)
        except Exception:
            if job.stopped.is_set():  # whatever the downloader made of the stop, nothing it got stays
                for leftover in AUDIO_DIR.glob(f"{task_id(job.source)}.*"):
                    leftover.unlink(missing_ok=True)
                raise Stopped() from None
            raise  # the audio stays for a retry
        for leftover in AUDIO_DIR.glob(f"{audio.stem}.*"):  # no longer needed once the MIDI is in the library
            leftover.unlink(missing_ok=True)
        return rec

    def _file(self, job: Job, audio: Path, details: dict) -> dict:
        """Everything slow but the transcription comes first: after it, only filing the piece is left."""
        details = {**details, **_video_page(job.source)}
        self._check(job)
        if not job.piece:
            started = time.monotonic()
            self._name(job, details)
            log.debug("named in %.1f s: %s", time.monotonic() - started, job.title)
        self._check(job)
        job.status = "converting"
        self.changed()
        rec = self._transcribe(job, audio, job.video.get("duration_s") or details.get("duration_s") or 0)
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

    def _transcribe(self, job: Job, audio: Path, seconds: float) -> dict:
        from .pipeline import Options, convert_file

        if self._engine is None:
            from .engine import TranskunEngine

            self._engine = TranskunEngine()
        started, base = time.monotonic(), 0.0 if job.local else DOWNLOAD_SHARE
        ticking = threading.Event()

        def tick():  # the rest of the way round, as the transcription is expected to go
            while not ticking.wait(0.5):
                done = (time.monotonic() - started) / max(1.0, seconds * self._speed)
                job.progress = base + (1 - base) * min(0.97, done)
                self.changed()

        if seconds:
            threading.Thread(target=tick, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                info = convert_file(self._engine, audio, Path(tmp), Options(), output_name=safe_name(job.title))
                self._check(job)
                if info.get("audio_duration_s"):
                    self._speed = info["transcribe_seconds"] / info["audio_duration_s"]
                return file_result(self.lib, Path(info["files"]["midi"]), job.title, composer=job.artist,
                                   performer=job.performer, aliases=job.aliases, tags=job.tags,
                                   source_type="local" if job.local else "url", source_url=job.source)
        finally:
            ticking.set()

    @staticmethod
    def _check(job: Job) -> None:
        if job.stopped.is_set():
            raise Stopped()
