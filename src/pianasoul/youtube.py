"""YouTube search through the official YouTube Data API v3 (needs YOUTUBE_API_KEY)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .audio import MAX_AUDIO_S

API = "https://www.googleapis.com/youtube/v3"

# What a title or channel can give away: a warning sign for the chat model (not a rejection); the app does not offer
# such a video (piano_versions).
FLAG_WORDS = {
    "synthesia": "falling-notes", "midi": "midi", "tutorial": "tutorial", "how to play": "tutorial",
    "lesson": "tutorial", "easy": "easy", "slow": "easy", "beginner": "easy", "piano tiles": "game",
    "compilation": "compilation", "playlist": "compilation", "1 hour": "compilation", "hours": "compilation",
    "8 bit": "not-piano", "8-bit": "not-piano", "remix": "remix",
}
FLAG_TEXT = {  # each flag as the chat model reads it
    "falling-notes": "falling-notes (Synthesia) video", "midi": "mentions MIDI", "tutorial": "tutorial or lesson",
    "easy": "easy, slowed or beginner arrangement", "game": "game", "compilation": "compilation",
    "not-piano": "not acoustic piano", "remix": "remix", "short": "very short",
    "long": "over 20 minutes (compilation or multi-movement)",
}


class YouTubeError(RuntimeError):
    pass


def api_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY") or None


def _get(endpoint: str, params: dict, key: str) -> dict:
    url = f"{API}/{endpoint}?{urllib.parse.urlencode({**params, 'key': key})}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc)["error"]["message"]
        except Exception:
            message = str(exc)
        raise YouTubeError(f"YouTube API error {exc.code}: {message}") from None
    except urllib.error.URLError as exc:
        raise YouTubeError(f"cannot reach YouTube API: {exc.reason}") from None


def parse_duration(iso: str) -> int:
    """'PT1H2M3S' -> 3723 seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def flags_for(title: str, channel: str, description: str, seconds: int, query: str = "") -> list[str]:
    """Warning flags from what a video says about itself; a word that is in what was searched for is the piece's
    own ("Easy On Me", "24 Hours"), not a warning."""
    text, asked = f"{title} {channel}".casefold(), query.casefold()
    flags = {flag for word, flag in FLAG_WORDS.items() if word in text and word not in asked}
    if "synthesia" in description.casefold():
        flags.add("falling-notes")
    if seconds and seconds < 60:
        flags.add("short")
    elif seconds > 20 * 60:
        flags.add("long")
    return sorted(flags)


PIANO_WORDS = ("piano", "钢琴", "鋼琴")


def piano_versions(query: str, key: str | None = None) -> list[dict]:
    """What the app offers to add: piano versions only, as she would choose them. The search asks for piano, and a
    video whose title and channel do not say piano, or that has any warning flag, is left out."""
    if not any(w in query.casefold() for w in PIANO_WORDS):
        query += " piano"
    return [r for r in search(query, 15, key)
            if not r["flags"] and any(w in f"{r['video_title']} {r['channel']}".casefold() for w in PIANO_WORDS)]


def describe(flags: list[str]) -> list[str]:
    return [FLAG_TEXT[f] for f in flags]


def video_id(url: str) -> str | None:
    found = re.fullmatch(r"https://www\.youtube\.com/watch\?v=([\w-]{11})", url)
    return found.group(1) if found else None


def search(query: str, max_results: int = 8, key: str | None = None) -> list[dict]:
    key = key or api_key()
    if not key:
        raise YouTubeError("YOUTUBE_API_KEY is not set")
    max_results = max(1, min(int(max_results), 15))
    found = _get("search", {"part": "snippet", "type": "video", "q": query, "maxResults": max_results}, key)
    ids = [it["id"]["videoId"] for it in found.get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return []
    details = _get("videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(ids)}, key)
    by_id = {v["id"]: v for v in details.get("items", [])}
    results = []
    for vid in ids:  # keep YouTube's relevance order
        v = by_id.get(vid)
        if not v:
            continue
        sn, cd = v.get("snippet", {}), v.get("contentDetails", {})
        seconds = parse_duration(cd.get("duration", ""))
        if seconds > MAX_AUDIO_S:  # too long to transcribe, so never offered
            continue
        description = (sn.get("description") or "").strip()
        results.append({
            "url": f"https://www.youtube.com/watch?v={vid}", **_details(v),
            "description": description[:300],
            "flags": flags_for(sn.get("title", ""), sn.get("channelTitle", ""), description, seconds, query),
        })
    return results


def details(urls: list[str], key: str | None = None) -> dict[str, dict]:
    """What YouTube says about these videos (by url), as the library keeps it, with each channel's picture: one
    request for up to 50 videos, and one for their channels."""
    key = key or api_key()
    ids = {video_id(u): u for u in urls if video_id(u)}
    videos = []
    for i in range(0, len(ids), 50):
        page = _get("videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(list(ids)[i:i + 50])}, key)
        videos += [v for v in page.get("items", []) if v.get("id") in ids]
    channels = list(dict.fromkeys(v["snippet"]["channelId"] for v in videos if v.get("snippet", {}).get("channelId")))
    avatars = {}
    for i in range(0, len(channels), 50):
        page = _get("channels", {"part": "snippet", "id": ",".join(channels[i:i + 50])}, key)
        avatars.update({c["id"]: _picture(c.get("snippet", {})) for c in page.get("items", [])})
    return {ids[v["id"]]: {**_details(v), "channel_avatar": avatars.get(v["snippet"].get("channelId"), "")}
            for v in videos}


def _picture(snippet: dict) -> str:
    thumbs = snippet.get("thumbnails", {})
    return next((thumbs[k]["url"] for k in ("high", "medium", "default") if thumbs.get(k, {}).get("url")), "")


def _details(video: dict) -> dict:
    sn, st = video.get("snippet", {}), video.get("statistics", {})
    return {"video_title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
            "views": int(st["viewCount"]) if st.get("viewCount", "").isdigit() else None,
            "published": (sn.get("publishedAt") or "")[:10], "thumbnail": _picture(sn),
            "duration_s": parse_duration(video.get("contentDetails", {}).get("duration", ""))}
