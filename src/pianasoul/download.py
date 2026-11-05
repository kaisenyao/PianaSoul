"""Download one video's original audio; publish only a verified cache file."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .audio import MAX_AUDIO_S, ffmpeg_exe
from .online import https_url, site_of


def task_id(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()[:20]


def valid_audio(path: Path) -> bool:
    """Decode the whole audio without retaining PCM in memory; require samples."""
    if not path.is_file() or not path.stat().st_size:
        return False
    proc = subprocess.run(
        [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
         "-i", str(path), "-map", "0:a:0", "-progress", "pipe:1", "-f", "null", "-"],
        capture_output=True,
    )
    return proc.returncode == 0 and any(
        line.startswith("out_time_us=") and line.partition("=")[2].isdigit()
        and int(line.partition("=")[2]) > 0
        for line in proc.stdout.decode(errors="replace").splitlines()
    )


def video_details(info: dict) -> dict:
    """What a video's page says about it, as the library keeps it (its picture over https)."""
    date = str(info.get("upload_date") or "")
    views = info.get("view_count")
    return {"video_title": str(info.get("title") or ""),
            "channel": str(info.get("channel") or info.get("uploader") or ""),
            "views": views if isinstance(views, int) else None,
            "published": f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 and date.isdigit() else "",
            "thumbnail": https_url(info.get("thumbnail")),
            "duration_s": info.get("duration")}


class _Said:
    """What yt-dlp says, on stderr as it says it when quiet (its warnings and errors); its warnings kept too."""

    def __init__(self):
        self.warnings: list[str] = []

    def debug(self, message: str) -> None:
        pass

    info = debug

    def warning(self, message: str) -> None:
        self.warnings.append(message)
        print(f"WARNING: {message}", file=sys.stderr, flush=True)

    def error(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)


def download_audio(url: str, cache_dir: Path, progress=None) -> tuple[Path, dict]:
    """Reuse a valid cache without network access, otherwise download atomically. Returns the audio and the
    video's details (video_details; a cached audio kept without its title has its file's name for it).
    progress(fraction) follows the download; it may raise to stop it."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("expected an http(s) video URL")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = task_id(url)
    metadata = cache_dir / f"{key}.json"
    details = {"video_title": key}
    if metadata.is_file():
        try:
            saved = json.loads(metadata.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                saved.setdefault("video_title", saved.pop("title", ""))  # caches made before the details
                details = {**details, **{k: v for k, v in saved.items() if v}}
        except (ValueError, OSError):
            pass
    for cached in sorted(cache_dir.glob(f"{key}.*")):
        if cached.suffix not in {".json", ".part", ".ytdl"} and valid_audio(cached):
            return cached, details

    from yt_dlp import YoutubeDL

    with tempfile.TemporaryDirectory(dir=cache_dir, prefix=f".{key}-") as tmp:
        said = _Said()
        options = {
            "format": "bestaudio/best",
            "outtmpl": str(Path(tmp) / "audio.%(ext)s"),
            "noplaylist": True,
            "extract_flat": "in_playlist",
            "lazy_playlist": True,
            "playlistend": 1,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 2,
            "quiet": True,
            "logger": said,
            "noprogress": True,
            "fixup": "never",
            "ffmpeg_location": ffmpeg_exe(),
            "js_runtimes": {"deno": {}, "node": {}},
        }
        if site_of(url) == "bilibili":
            # One of Bilibili's servers abroad (upos-sz-mirrorcosov) answers 567 when asked for a whole file, and gives
            # it a part at a time: it is asked for in parts, as YouTube's formats are (they set their own size, which
            # this would override).
            options["http_chunk_size"] = 10 << 20
        if progress:
            def hook(d):
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if d.get("status") == "downloading" and total:
                    progress(min(1.0, (d.get("downloaded_bytes") or 0) / total))
            options["progress_hooks"] = [hook]
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info or info.get("_type", "video") != "video" or "entries" in info:
                raise ValueError("provide a single video URL, not a playlist or channel")
            if info.get("is_live") or info.get("live_status") == "is_upcoming":
                raise ValueError("live/upcoming streams are not supported")
            if (info.get("duration") or 0) > MAX_AUDIO_S:
                raise ValueError(f"the video is longer than {MAX_AUDIO_S // 60} minutes")
            # A Bilibili video for its UP主's supporters only: yt-dlp takes its preview, as it warns, not the piece.
            if site_of(url) == "bilibili" and any("only the preview" in w for w in said.warnings):
                raise ValueError("Bilibili gives only a preview of this video: it is for its uploader's supporters")
            info = ydl.process_ie_result(info, download=True)
            downloaded = Path(ydl.prepare_filename(info))
        if not valid_audio(downloaded):
            raise RuntimeError("downloaded file has no valid decodable audio")
        details = video_details(info)
        details["video_title"] = details["video_title"] or str(info.get("id") or key)
        # Metadata first, audio last. Only complete audio becomes visible as cache.
        meta_tmp = Path(tmp) / "metadata.json"
        meta_tmp.write_text(json.dumps(details, ensure_ascii=False), encoding="utf-8")
        os.replace(meta_tmp, metadata)
        target = cache_dir / f"{key}{downloaded.suffix}"
        os.replace(downloaded, target)
        return target, details


def video_info(url: str) -> dict:
    """What yt-dlp knows about one video, without downloading it."""
    from yt_dlp import YoutubeDL

    options = {"quiet": True, "noplaylist": True, "socket_timeout": 20, "js_runtimes": {"deno": {}, "node": {}}}
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
    if not info or info.get("_type", "video") != "video":
        raise ValueError("provide a single video URL, not a playlist or channel")
    return info
