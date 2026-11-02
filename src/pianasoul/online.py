"""Online search: pieces found on YouTube by name, and what a video says about itself, through yt-dlp, the same way
the downloads go, so no key is needed (SPEC 9.4). Its search is YouTube's own, in YouTube's order. The videos and
channels stay named as they are: a piece's source is still the YouTube video it came from."""

from __future__ import annotations

import re

from .audio import MAX_AUDIO_S

# What a title or channel can give away: a warning sign for the chat model (not a rejection); the app does not offer
# such a video (piano_versions).
FLAG_WORDS = {
    "synthesia": "falling-notes", "midi": "midi", "tutorial": "tutorial", "how to play": "tutorial",
    "lesson": "tutorial", "easy": "easy", "slow": "easy", "beginner": "easy", "piano tiles": "game",
    "compilation": "compilation", "playlist": "compilation", "1 hour": "compilation", "hours": "compilation",
    "8 bit": "not-piano", "8-bit": "not-piano", "remix": "remix",
    "教学": "tutorial", "教學": "tutorial", "简易": "easy", "簡易": "easy", "简单": "easy", "簡單": "easy",
    "慢速": "easy", "入门": "easy", "入門": "easy",
}
FLAG_TEXT = {  # each flag as the chat model reads it
    "falling-notes": "falling-notes (Synthesia) video", "midi": "mentions MIDI", "tutorial": "tutorial or lesson",
    "easy": "easy, slowed or beginner arrangement", "game": "game", "compilation": "compilation",
    "not-piano": "not acoustic piano", "remix": "remix", "short": "very short",
    "long": "over 20 minutes (compilation or multi-movement)",
}
class OnlineError(RuntimeError):
    pass


class _Silent:
    """yt-dlp prints nothing: what fails comes back as OnlineError, saying why."""
    def debug(self, message: str) -> None:
        pass

    info = warning = error = debug


# yt-dlp solves YouTube's JavaScript challenges with deno, which the advanced extra installs beside Python (where
# yt-dlp looks first); node, should the Mac have it, too.
OPTIONS = {"quiet": True, "no_warnings": True, "logger": _Silent(), "socket_timeout": 20,
           "js_runtimes": {"deno": {}, "node": {}}}


def _extract(url: str, **options) -> dict:
    """What yt-dlp makes of url, without downloading anything; OnlineError, saying why, when it cannot."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    try:
        with YoutubeDL({**OPTIONS, **options}) as ydl:
            return ydl.extract_info(url, download=False, process=False) or {}
    except DownloadError as exc:
        raise OnlineError(str(exc).removeprefix("ERROR: ")) from None


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
ENOUGH = 5  # piano versions enough for a search's offer: with fewer, the other videos follow them


def piano_versions(query: str) -> list[dict]:
    """What the app offers to add, as she would choose: the search asks for piano, and a video with any warning
    flag is left out. Those whose title or channel say piano come first; when they are fewer than ENOUGH, the others
    follow. A piece written for the piano has its best recordings say nothing of it (Yiruma's own, a pianist's on
    DG), while a song's own recording, sung, only comes in where its piano versions are few."""
    if not any(w in query.casefold() for w in PIANO_WORDS):
        query += " piano"
    found = [r for r in search(query, 15) if not r["flags"]]
    said = [r for r in found if any(w in f"{r['video_title']} {r['channel']}".casefold() for w in PIANO_WORDS)]
    return said if len(said) >= ENOUGH else said + [r for r in found if r not in said]


def describe(flags: list[str]) -> list[str]:
    return [FLAG_TEXT[f] for f in flags]


def video_id(url: str) -> str | None:
    found = re.fullmatch(r"https://www\.youtube\.com/watch\?v=([\w-]{11})", url)
    return found.group(1) if found else None


def search(query: str, max_results: int = 8) -> list[dict]:
    """Videos for query, YouTube's best first: the list alone (about a second for 8), so no publishing date, which
    only a video's own page gives."""
    max_results = max(1, min(int(max_results), 15))
    found = _extract(f"ytsearch{max_results}:{query}", extract_flat="in_playlist")
    results = []
    for entry in found.get("entries") or []:
        vid = str(entry.get("id") or "")
        seconds = int(entry.get("duration") or 0)
        if len(vid) != 11 or seconds > MAX_AUDIO_S:  # not a video, or too long to transcribe: never offered
            continue
        title, channel = str(entry.get("title") or ""), str(entry.get("channel") or entry.get("uploader") or "")
        description = str(entry.get("description") or "").strip()
        views = entry.get("view_count")
        results.append({
            "url": f"https://www.youtube.com/watch?v={vid}", "video_title": title, "channel": channel,
            "views": views if isinstance(views, int) else None, "published": "",
            "thumbnail": _largest(entry.get("thumbnails")), "duration_s": seconds,
            "description": description[:300], "flags": flags_for(title, channel, description, seconds, query),
        })
    return results


def details(urls: list[str]) -> dict[str, dict]:
    """What the pages of these YouTube videos say (by url), as the library keeps it, with each channel's picture:
    one video at a time (a second or two each). A video yt-dlp cannot read (removed, private) is left out."""
    from .download import video_details

    found, avatars = {}, {}
    for url in dict.fromkeys(u for u in urls if video_id(u)):
        try:
            info = _extract(url)
        except OnlineError:
            continue
        channel = str(info.get("channel_url") or "")
        if channel not in avatars:
            avatars[channel] = channel_avatar(channel)
        video = video_details(info)  # unprocessed, its thumbnails are only listed
        found[url] = {**video, "thumbnail": video["thumbnail"] or _largest(info.get("thumbnails")),
                      "channel_avatar": avatars[channel]}
    return found


def channel_avatar(url: str) -> str:
    """A channel's picture, from its page (about half a second): the square one of its pictures (the others are
    banners); empty when the page does not give it."""
    if not url:
        return ""
    try:
        pictures = _extract(url, extract_flat="in_playlist", playlist_items="0").get("thumbnails") or []
    except OnlineError:
        return ""
    square = [p for p in pictures if p.get("url") and p.get("width") and p.get("width") == p.get("height")]
    if square:
        return max(square, key=lambda p: p["width"])["url"]
    return next((p["url"] for p in pictures if p.get("id") == "avatar_uncropped" and p.get("url")), "")


def _largest(pictures) -> str:
    """The widest of a video's thumbnails (yt-dlp lists them smallest first)."""
    sized = [p for p in pictures or [] if p.get("url")]
    return max(sized, key=lambda p: p.get("width") or 0)["url"] if sized else ""
