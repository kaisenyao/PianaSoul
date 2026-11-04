"""Online search: pieces found by name on YouTube and on Bilibili (哔哩哔哩), those of them chosen (search_sources:
one setting, for her and every phone), and what a video says about itself. No key is needed (SPEC 9.4): YouTube is
searched through yt-dlp, the same way the downloads go; Bilibili with its web page's own search, the one yt-dlp's
bilisearch asks too, sent through yt-dlp all the same, so that it goes the way the downloads do (proxy,
certificates). Each site's search is its own, in its own order; the sites chosen are searched at once and their
videos merged into one list (search_sites). The videos and channels stay named as they are: a piece's source is
still the video it came from. Another site is one entry in each table below that names them all, and its search."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
import queue
import re
import socket
import threading
import time
from urllib.parse import urlsplit
import uuid

from .audio import MAX_AUDIO_S

SOURCES = {"youtube": "YouTube", "bilibili": "Bilibili"}  # what online search can search (merged in this order)
HOSTS = {"youtube.com": "youtube", "youtu.be": "youtube", "bilibili.com": "bilibili", "b23.tv": "bilibili"}  # site_of
# What a title or channel can give away: a warning sign for the chat model (not a rejection); the app does not offer
# such a video (piano_versions). The words Bilibili's titles use (教程 onwards) were checked, like the word a search on
# Bilibili adds (PIANO_WORD), by a test of 30 queries on it (2026-11-04), which added those from 琴谱 on as its titles
# wrote them (simplified: a traditional form it did not see is not guessed). Of the videos flagged a tutorial only for
# their category, 音乐教学, none was a performance.
FLAG_WORDS = {
    "synthesia": "falling-notes", "midi": "midi", "tutorial": "tutorial", "how to play": "tutorial",
    "lesson": "tutorial", "easy": "easy", "slow": "easy", "beginner": "easy", "piano tiles": "game",
    "compilation": "compilation", "playlist": "compilation", "1 hour": "compilation", "hours": "compilation",
    "8 bit": "not-piano", "8-bit": "not-piano", "remix": "remix",
    "教学": "tutorial", "教學": "tutorial", "简易": "easy", "簡易": "easy", "简单版": "easy", "簡單版": "easy",
    "慢速": "easy", "入门": "easy", "入門": "easy",
    "教程": "tutorial", "瀑布流": "falling-notes", "简谱": "score", "簡譜": "score", "动态谱": "score", "動態譜": "score",
    "弹唱": "sung", "彈唱": "sung", "合集": "compilation", "串烧": "compilation", "串燒": "compilation",
    "琴谱": "score", "乐谱": "score", "光遇": "game", "原神演奏": "game", "原琴": "game", "伴奏": "backing",
    "教你": "tutorial", "教会你": "tutorial",
}
FLAG_TEXT = {  # each flag as the chat model reads it
    "falling-notes": "falling-notes (Synthesia) video", "midi": "mentions MIDI", "tutorial": "tutorial or lesson",
    "easy": "easy, slowed or beginner arrangement", "game": "game", "compilation": "compilation",
    "not-piano": "not acoustic piano", "remix": "remix", "short": "very short",
    "long": "over 20 minutes (compilation or multi-movement)",
    "score": "the score shown as it plays (often a MIDI rendering)", "sung": "sung, with the piano",
    "backing": "accompaniment only (a backing track)",
}


class OnlineError(RuntimeError):
    """A site that could not be searched or read, and why, as the app and she say it (reason): unreachable (no
    answer in time, no connection), refused (it answered that it will not: risk control, too many requests, a check
    for bots) or failed (anything else). source: the site (SOURCES). The message is as the site or yt-dlp said it."""

    def __init__(self, message: str, reason: str = "failed", source: str = ""):
        super().__init__(message)
        self.reason, self.source = reason, source


class _Silent:
    """yt-dlp prints nothing: what fails comes back as OnlineError, saying why."""
    def debug(self, message: str) -> None:
        pass

    info = warning = error = debug


# yt-dlp solves YouTube's JavaScript challenges with deno, which the advanced extra installs beside Python (where
# yt-dlp looks first); node, should the Mac have it, too.
OPTIONS = {"quiet": True, "no_warnings": True, "logger": _Silent(), "socket_timeout": 20,
           "extractor_retries": 1,  # a page that fails is tried again once, not three times: a stall is not minutes
           "js_runtimes": {"deno": {}, "node": {}}}
# A search, which someone waits for, gives up within seconds where its site cannot be reached (YouTube from mainland
# China), and is not tried again, so that the app soon says why; reading a video's page, and its download, keep theirs.
SEARCH_OPTIONS = {"socket_timeout": 8, "extractor_retries": 0}
REFUSED_STATUS = {412, 429, 567}  # Bilibili's risk control, too many requests, Bilibili's servers saying no


def reason_of(exc: BaseException) -> str:
    """Why yt-dlp, or a site through it, failed (OnlineError.reason): from the error, or what caused it."""
    from yt_dlp.networking.exceptions import HTTPError, TransportError

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OnlineError):
            return exc.reason
        if isinstance(exc, HTTPError):
            return "refused" if exc.status in REFUSED_STATUS else "failed"
        if isinstance(exc, (TransportError, TimeoutError, ConnectionError, socket.gaierror)):
            return "unreachable"
        # YouTube's "Sign in to confirm you're not a bot"; what yt-dlp says of a Bilibili video's risk-control page.
        if "not a bot" in str(exc) or "exceeded the rate limit" in str(exc):
            return "refused"
        # An ExtractorError says its cause; a DownloadError, the error it reports.
        exc = getattr(exc, "cause", None) or (getattr(exc, "exc_info", None) or (None, None))[1] or exc.__cause__
    return "failed"


def _extract(url: str, listed: bool = False, **options) -> dict:
    """What yt-dlp makes of a YouTube address or search, without downloading anything; OnlineError, saying why, when
    it cannot. listed: its entries are read here too (a search's pages are fetched only as they are read), so that
    their failure says why."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import YoutubeDLError

    try:
        with YoutubeDL({**OPTIONS, **options}) as ydl:
            info = ydl.extract_info(url, download=False, process=False) or {}
            if listed:
                info["entries"] = list(info.get("entries") or [])
            return info
    except YoutubeDLError as exc:  # DownloadError and ExtractorError alike
        raise OnlineError(str(exc).removeprefix("ERROR: "), reason_of(exc), "youtube") from None


def flags_for(title: str, channel: str, description: str, seconds: int, query: str = "",
              category: str = "") -> list[str]:
    """Warning flags from what a video says about itself (category: what its site files it under, Bilibili's 音乐教学
    among them); a word that is in what was searched for is the piece's own ("Easy On Me", "24 Hours"), not a
    warning."""
    text, asked = f"{title} {channel} {category}".casefold(), query.casefold()
    flags = {flag for word, flag in FLAG_WORDS.items() if word in text and word not in asked}
    if "synthesia" in description.casefold():
        flags.add("falling-notes")
    if seconds and seconds < 60:
        flags.add("short")
    elif seconds > 20 * 60:
        flags.add("long")
    return sorted(flags)


PIANO_WORDS = ("piano", "钢琴", "鋼琴")
# What a search adds when its words do not ask for piano; and the word for it each site is asked in (for_site).
PIANO_WORD = {"youtube": "piano", "bilibili": "钢琴"}
ENOUGH = 5  # piano versions enough for a search's offer: with fewer, the other videos follow them
MOST = {"youtube": 15, "bilibili": 20}  # the most a search gives: on Bilibili, its one page (one request)
LATE_S = 2.0  # a site still searching this long after another gave videos is not waited for (search_sites)


def chosen_sources(lib) -> list[str]:
    """The sites online search searches, for her and every phone (the setting search_sources, kept as their names
    with commas between), in SOURCES' order: all of them until some are chosen."""
    return sources_of(lib.setting("search_sources").split(",")) or list(SOURCES)


def sources_of(value) -> list[str]:
    """A choice of sites to search, in SOURCES' order; empty when it is not one: a list of sites online search
    knows, at least one."""
    if not isinstance(value, list) or not value or not all(isinstance(s, str) and s in SOURCES for s in value):
        return []
    return [s for s in SOURCES if s in value]


def piano_versions(query: str, source: str = "youtube") -> list[dict]:
    """What the app offers to add, as she would choose: the search asks for piano, as many as its site gives, and a
    video with any warning flag is left out. Those whose title or channel say piano come first; when they are fewer
    than ENOUGH, the others follow (on Bilibili, only as many as make ENOUGH). A piece written for the piano has its
    best recordings say nothing of it (Yiruma's own, a pianist's on DG), while a song's own recording, sung, only
    comes in where its piano versions are few. On Bilibili the others are mostly other instruments, songs sung and
    games, its few piano ones first (in the 30-query test, 4 of the 28 offered this way were piano, all of them among
    the first)."""
    if not any(w in query.casefold() for w in PIANO_WORDS):
        query += f" {PIANO_WORD[source]}"
    found = [r for r in search(query, MOST[source], source) if not r["flags"]]
    said = [r for r in found if any(w in f"{r['video_title']} {r['channel']}".casefold() for w in PIANO_WORDS)]
    if len(said) >= ENOUGH:
        return said
    others = [r for r in found if r not in said]
    return said + (others[:ENOUGH - len(said)] if source == "bilibili" else others)


def describe(flags: list[str]) -> list[str]:
    return [FLAG_TEXT[f] for f in flags]


def video_id(url: str) -> str | None:
    found = re.fullmatch(r"https://www\.youtube\.com/watch\?v=([\w-]{11})", url)
    return found.group(1) if found else None


def site_of(url: str) -> str:
    """The site (SOURCES) a video's address is on, or empty for another."""
    host = re.sub(r"^(www|m|music)\.", "", urlsplit(url.strip()).netloc.lower())
    return HOSTS.get(host, "")


def resolved(url: str) -> str:
    """A Bilibili short link (b23.tv, as its app shares a video) as the address it stands for, so that it is the
    video a search finds; any other link as it is. OnlineError, saying why, when it cannot be followed."""
    url = url.strip()
    if urlsplit(url).netloc.lower() != "b23.tv":
        return url
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request
    from yt_dlp.networking.exceptions import HTTPError
    from yt_dlp.utils import YoutubeDLError

    try:
        with YoutubeDL({**OPTIONS, **SEARCH_OPTIONS}) as ydl, ydl.urlopen(Request(url, method="HEAD")) as response:
            return response.url
    except HTTPError as exc:
        if urlsplit(exc.response.url).netloc.lower() != "b23.tv":  # it led there, and that page would not answer
            return exc.response.url
        raise OnlineError(str(exc), reason_of(exc), "bilibili") from None
    except YoutubeDLError as exc:
        raise OnlineError(str(exc), reason_of(exc), "bilibili") from None


def search(query: str, max_results: int = 8, source: str = "youtube") -> list[dict]:
    """Videos for query on source (SOURCES), the site's best first, at most max_results (1 to the site's MOST), each
    with its site; none too long to transcribe. OnlineError, saying why, when the site cannot be searched."""
    max_results = max(1, min(int(max_results), MOST[source]))
    return [{**r, "site": source} for r in SEARCHES[source](query, max_results)]


def search_sites(query: str, max_results: int, sources: list[str]) -> tuple[list[dict], list[OnlineError]]:
    """Videos for query on each of sources (SOURCES), all searched at once, each in a thread of its own, by its own
    rules and asked for piano in its own word (for_site); merged, at most max_results. With them, why each site that
    could not be searched could not, in SOURCES' order. A site still searching LATE_S after another gave videos is
    not waited for, and counts as out of reach: where YouTube is (mainland China), its 8 s would hold the videos
    there are."""
    answers = queue.Queue()

    def ask(source: str) -> None:
        try:
            answers.put((source, search(for_site(query, source), max_results, source)))
        except Exception as exc:  # its failure (OnlineError, or anything else its answer brings), the others kept
            answers.put((source, OnlineError(str(exc), reason_of(exc), source)))

    for source in sources:
        threading.Thread(target=ask, args=(source,), daemon=True).start()  # one left behind ends on its own
    found, failed, until = {}, {}, None
    while len(found) + len(failed) < len(sources):
        try:
            source, answer = answers.get(timeout=None if until is None else max(0.0, until - time.monotonic()))
        except queue.Empty:
            break
        if isinstance(answer, OnlineError):
            failed[source] = answer
            continue
        found[source] = answer
        if answer and until is None:
            until = time.monotonic() + LATE_S
    for source in sources:
        if source not in found and source not in failed:
            failed[source] = OnlineError(f"no answer {LATE_S:g} s after the others", "unreachable", source)
    return (merged([found[s] for s in SOURCES if s in found])[:max_results],
            [failed[s] for s in SOURCES if s in failed])


def for_site(query: str, source: str) -> str:
    """The words of a search of several sites as source is asked them: the word for piano that ends them (where she
    puts it) in the site's own (PIANO_WORD), so that each finds its piano versions as it would on its own."""
    words = query.split()
    if not words or words[-1].casefold() not in PIANO_WORDS:
        return query
    return " ".join([*words[:-1], PIANO_WORD[source]])


def merged(lists: list[list[dict]]) -> list[dict]:
    """Several sites' videos as one list: the first of each, then the second of each, and so on (round robin, the
    usual way to merge the ranked lists of several engines), a video already in it left out (_same). The phone's
    merge (web/online.js) is the same, tested with the same cases."""
    together = []
    for rank in range(max(map(len, lists), default=0)):
        for videos in lists:
            if rank < len(videos) and not any(_same(videos[rank], kept) for kept in together):
                together.append(videos[rank])
    return together


def _same(a: dict, b: dict) -> bool:
    """One video: the same address, or the same title and length (to 2 s), as one performance posted on both sites
    is (a live search for 童话 found one, its title the same on each)."""
    def title(video):
        return " ".join(str(video.get("video_title") or "").lower().split())
    return a["url"] == b["url"] or bool(title(a)) and title(a) == title(b) \
        and abs((a.get("duration_s") or 0) - (b.get("duration_s") or 0)) <= 2


def _youtube(query: str, n: int) -> list[dict]:
    """YouTube's videos for query: the list alone (about a second for 8), so no publishing date, which only a
    video's own page gives."""
    found = _extract(f"ytsearch{n}:{query}", listed=True, **SEARCH_OPTIONS)
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


BILIBILI_SEARCH = "https://api.bilibili.com/x/web-interface/search/type"
BILIBILI_REFUSED = {-412, -352}  # its answer's codes for a request intercepted by its risk control
RETRY_S = 2.5  # a refusal is often over a moment later: Bilibili is asked once more, after this long, and no more
BVID = re.compile(r"BV[0-9A-Za-z]{10}")
BEIJING = timezone(timedelta(hours=8))  # the dates Bilibili shows
AVATARS_KEPT = 300
_avatars: dict[str, str] = {}  # the UP主's picture of each video the latest Bilibili searches found, by its address


def _bilibili(query: str, n: int) -> list[dict]:
    """Bilibili's videos for query: one page of its search, with all a video needs (its UP主's picture too). Refused
    (often only for a moment), it is asked once more, a moment later."""
    try:
        page = _bilibili_page(query)
    except OnlineError as exc:
        if exc.reason != "refused":
            raise
        time.sleep(RETRY_S)
        page = _bilibili_page(query)
    results = []
    for item in page.get("result") or []:
        bvid, seconds = str(item.get("bvid") or ""), _seconds(item.get("duration"))
        # Only videos (not a paid course), whole to anyone (not paid for, nor for the UP主's supporters only), and
        # short enough to transcribe.
        if item.get("type") != "video" or item.get("is_pay") or item.get("is_charge_video") \
                or not BVID.fullmatch(bvid) or seconds > MAX_AUDIO_S:
            continue
        title = html.unescape(re.sub(r"</?em\b[^>]*>", "", str(item.get("title") or "")))  # the search's highlights
        channel, description = str(item.get("author") or ""), str(item.get("description") or "").strip()
        views, published = item.get("play"), item.get("pubdate")
        url, avatar = f"https://www.bilibili.com/video/{bvid}", https_url(item.get("upic"))
        results.append({
            "url": url, "video_title": title, "channel": channel, "channel_avatar": avatar,
            "views": views if isinstance(views, int) else None,
            "published": datetime.fromtimestamp(published, BEIJING).strftime("%Y-%m-%d")
            if isinstance(published, int) and published > 0 else "",
            "thumbnail": https_url(item.get("pic")), "duration_s": seconds, "description": description[:300],
            "flags": flags_for(title, channel, description, seconds, query, str(item.get("typename") or "")),
        })
        _avatars.pop(url, None)  # the latest last
        _avatars[url] = avatar
    for old in list(_avatars)[:-AVATARS_KEPT]:
        _avatars.pop(old, None)
    return results[:n]


def _bilibili_page(query: str) -> dict:
    """The first page of Bilibili's search for query, as its web page asks for it: with a visitor's cookie (buvid3,
    random, as yt-dlp makes one) and its own page as the referrer. OnlineError, saying why, when it will not answer,
    as when it says it found nothing but asks to be shown a human is asking (v_voucher)."""
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request
    from yt_dlp.utils import YoutubeDLError

    request = Request(BILIBILI_SEARCH, query={"search_type": "video", "keyword": query, "page": 1},
                      headers={"Referer": "https://www.bilibili.com/", "Cookie": f"buvid3={uuid.uuid4()}infoc"})
    try:
        with YoutubeDL({**OPTIONS, **SEARCH_OPTIONS}) as ydl, ydl.urlopen(request) as response:
            answer = json.loads(response.read())
    except YoutubeDLError as exc:
        raise OnlineError(str(exc), reason_of(exc), "bilibili") from None
    except ValueError:
        raise OnlineError("Bilibili's answer is not JSON", "failed", "bilibili") from None
    code, data = answer.get("code"), answer.get("data") or {}
    said = f"Bilibili: {answer.get('message') or 'no message'} ({code})"
    if code in BILIBILI_REFUSED or code == 0 and not data.get("result") and data.get("v_voucher"):
        raise OnlineError(said, "refused", "bilibili")
    if code != 0:
        raise OnlineError(said, "failed", "bilibili")
    return data


SEARCHES = {"youtube": _youtube, "bilibili": _bilibili}  # each site's own search (search)


def avatar_found(url: str) -> str:
    """The UP主's picture of a Bilibili video that a recent search found, for the piece made of it (no page yt-dlp
    reads has it); empty for any other."""
    return _avatars.get(url, "")


def _seconds(clock) -> int:
    """Bilibili's length of a video ("4:28", "39:3": minutes and seconds, unpadded; or with hours) in seconds; 0 when
    it gives none."""
    try:
        return sum(int(part) * 60 ** i for i, part in enumerate(reversed(str(clock).split(":"))))
    except ValueError:
        return 0


def https_url(url) -> str:
    """A picture's address over https (Bilibili gives "//…" and "http://…"), as the phone's page, itself https,
    shows it."""
    url = str(url or "")
    return "https:" + url if url.startswith("//") else re.sub(r"^http://", "https://", url)


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
