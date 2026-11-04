"""Local MIDI library: a small SQLite index of every MIDI file PianaSoul knows about.

    python -m pianasoul.library import ~/Desktop/midi      # copy MIDI files into the library folder
    python -m pianasoul.library list
    python -m pianasoul.library enrich                     # let the chat model add artists, aliases, tags

The library is a folder of music (MUSIC_DIR): yours, as you put it there, Transcribed/, where PianaSoul files the
pieces it makes, Uploaded/, where the phone's files go, and Artwork/, the pictures chosen for pieces and artists. Its index and other lasting data are in DATA_DIR, and work files that may go in CACHE_DIR.
Each can be moved with an environment variable (PIANASOUL_MUSIC, PIANASOUL_DATA, PIANASOUL_CACHE).
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pretty_midi
from send2trash import send2trash

from .cleanup import pedal_intervals
from .llm import Refused


def _place(variable: str, default: Path) -> Path:
    return Path(os.environ.get(variable) or default).expanduser().resolve()


MUSIC_DIR = _place("PIANASOUL_MUSIC", Path.home() / "Music" / "PianaSoul")  # the library: your music, and
TRANSCRIBED = "Transcribed"  # the subfolder only PianaSoul writes to, with the pieces it made
UPLOADED = "Uploaded"  # where files sent from the phone go: yours, as if put there by hand, and left as they are
# The user's own pictures, kept with the music so that they go wherever it goes: a piece's artwork by its file's
# content (Pieces/<fingerprint>.jpg: renamed, moved, or on another Mac with a new library, it is still its), an
# artist's picture by name (Artists/<name>.jpg). A playlist lives in the library database, its artwork beside it.
ARTWORK = "Artwork"
DATA_DIR = _place("PIANASOUL_DATA", Path.home() / "Library" / "Application Support" / "PianaSoul")  # what lasts
CACHE_DIR = _place("PIANASOUL_CACHE", Path.home() / "Library" / "Caches" / "PianaSoul")  # what may go
DB_PATH = DATA_DIR / "library.db"
OLD_OUTPUT = Path(__file__).resolve().parents[2] / "output"  # where a checkout kept everything before (migrate.py)
MIDI_TYPES = {".mid", ".midi"}
SF_DATALESS = 0x40000000  # st_flags of a file whose content is still in iCloud (stat.SF_DATALESS from Python 3.13)

FIELDS = ("title", "composer", "performer", "aliases", "tags")
SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    composer TEXT NOT NULL DEFAULT '',
    performer TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'local',
    source_url TEXT NOT NULL DEFAULT '',
    midi_path TEXT NOT NULL UNIQUE,
    notes INTEGER,
    velocity_min INTEGER,
    velocity_max INTEGER,
    pedal_presses INTEGER,
    duration_s REAL,
    added_at TEXT NOT NULL,
    favorited_at TEXT,
    last_played_at TEXT,
    video_title TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    views INTEGER,
    published TEXT NOT NULL DEFAULT '',
    thumbnail TEXT NOT NULL DEFAULT '',
    channel_avatar TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS recordings_source_url ON recordings(source_url);
-- What was asked for each new piece, before conversations were kept; read once, to make them (conversations.py).
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    asked_at TEXT NOT NULL,
    text TEXT NOT NULL,
    recording_id INTEGER REFERENCES recordings(id)
);
-- The photo a picture (a piece's artwork, a playlist's, an artist's) was moved and scaled from, by the picture's own
-- content (its SHA-256): so it follows the picture wherever it goes. x, y, side: the square of the photo it is.
CREATE TABLE IF NOT EXISTS picture_originals (
    digest TEXT PRIMARY KEY,
    x REAL NOT NULL,
    y REAL NOT NULL,
    side REAL NOT NULL
);
-- Each time a piece was heard (half of it at least: Playback): how often, and when, for what is listened to most.
CREATE TABLE IF NOT EXISTS plays (
    recording_id INTEGER NOT NULL REFERENCES recordings(id),
    played_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plays_recording ON plays(recording_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Playlists (歌单): the user's own lists of pieces, each piece once, in their order. Not files in the library folder:
-- a piece renamed or moved stays in its playlists, one deleted leaves them (Library.delete).
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- What the user wrote about an artist (简介), by name: 原作者 and 演奏者 of one name share it, as their picture.
CREATE TABLE IF NOT EXISTS artists (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id),
    recording_id INTEGER NOT NULL REFERENCES recordings(id),
    position INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, recording_id)
);
-- The recordings (mp3 and the like) in the library folder, each to be made into a piece: once on its own, when it is
-- first seen and this Mac can transcribe (status waiting until then), and again only when asked.
CREATE TABLE IF NOT EXISTS audio (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL DEFAULT '',
    stamp TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'waiting',  -- waiting, queued, converting, done or failed
    recording_id INTEGER REFERENCES recordings(id),
    error TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);
"""
# Columns added since the first libraries were made; an older library gets them when it is opened.
# fingerprint: the file's sha256, to know it again when it moves; stamp: its size and time, to see it change.
# named_at: when the chat model was asked for the piece's other names (enrich).
# making: 1 for a piece being made, which plays as it is (Library.making); its file comes once it is made.
ADDED_COLUMNS = {"recordings": {"favorited_at": "TEXT", "last_played_at": "TEXT",
                                "video_title": "TEXT NOT NULL DEFAULT ''", "channel": "TEXT NOT NULL DEFAULT ''",
                                "views": "INTEGER", "published": "TEXT NOT NULL DEFAULT ''",
                                "thumbnail": "TEXT NOT NULL DEFAULT ''", "channel_avatar": "TEXT NOT NULL DEFAULT ''",
                                "fingerprint": "TEXT NOT NULL DEFAULT ''", "stamp": "TEXT NOT NULL DEFAULT ''",
                                "named_at": "TEXT", "making": "INTEGER NOT NULL DEFAULT 0"},
                 "playlists": {"description": "TEXT NOT NULL DEFAULT ''"}}
VIDEO_FIELDS = ("video_title", "channel", "views", "published", "thumbnail", "channel_avatar")
STOPWORDS = {"the", "a", "an", "of", "in", "and", "by", "for", "piano", "solo", "version", "play", "me"}


def _version(path: Path) -> int | None:
    """When a picture was last kept (the page asks for it again once this changes), or None when there is none."""
    try:
        return path.stat().st_mtime_ns
    except OSError:  # none
        return None


def _keep_picture(path: Path, jpeg: bytes | None) -> None:
    """Keep a picture, whole or not at all (written aside, then put in place), or take it away (None)."""
    if jpeg is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f".{path.name}.part")
    part.write_bytes(jpeg)
    part.replace(path)


NAME_LIMIT = 100  # characters in a playlist's name: each is in what she is told with every request
PLAYLIST_TIME = "microseconds"  # which of two playlists changed last, even within a second (her choice by name)
PLAYS_SINCE = "plays_since"  # the setting: when the first play was counted
DESCRIPTION_LIMIT = 1000  # characters in a playlist's or an artist's description (简介)


def _playlist_name(name: str) -> str:
    name = str(name).strip()
    if not name:
        raise ValueError("a playlist needs a name")
    if len(name) > NAME_LIMIT:
        raise ValueError("a playlist's name is too long")
    return name


def _description(text: str) -> str:
    text = str(text).strip()
    if len(text) > DESCRIPTION_LIMIT:
        raise ValueError("a description is too long")
    return text


def _now(timespec: str = "seconds") -> str:
    return datetime.now(timezone.utc).isoformat(timespec=timespec)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[\W_]+", " ", text).strip()


def _terms(text: str) -> set[str]:
    norm = _norm(text)
    terms = {w for w in norm.split() if w not in STOPWORDS}
    for run in re.findall(r"[\u3400-\u9fff]+", norm):  # Chinese has no spaces: use characters and pairs
        terms.update(run)
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def present(path: Path) -> bool:
    """Whether a file is there to be read: not missing, and not one whose content iCloud has not brought down."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return bool(st.st_mode & 0o100000) and not st.st_flags & SF_DATALESS  # a regular file


def stamp(path: Path) -> str:
    """A file's size and modification time: when they change, so may its content."""
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"


def fingerprint(path: Path) -> str:
    """The sha256 of a file's content, by which a file that moved is known again."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def midi_stats(path: Path) -> dict:
    midi = pretty_midi.PrettyMIDI(str(path))
    notes = [n for inst in midi.instruments for n in inst.notes]
    velocities = [n.velocity for n in notes]
    return {
        "notes": len(notes),
        "velocity_min": min(velocities) if velocities else None,
        "velocity_max": max(velocities) if velocities else None,
        "pedal_presses": len(pedal_intervals([c for inst in midi.instruments for c in inst.control_changes])),
        "duration_s": round(midi.get_end_time(), 1),
    }


# A piece's three names, as everything that names a piece asks for them: the app's edit sheet, her tools and a
# new piece from a video. Its title is made from them (make_title), and its composer field holds the artist.
NAMES = {
    "piece": "the name of the piece or song as a listener says it, in the user's language: one short name "
             "(月光奏鸣曲第三乐章, Clair de Lune, 晴天), never followed by another name in brackets",
    "artist": "its composer, or the singer for a song, by the short name they are best known by (Beethoven, 周杰伦)",
    "performer": "the pianist (often the video's channel)",
}
# Its other names, kept for finding it, never in its title.
ALIASES = ("other names the piece is known by, separated by '; ': its name in Chinese and in English (whichever the "
           "piece's name is not in), other spellings, its full catalog title")


def title_parts(title: str, composer: str = "", performer: str = "") -> dict:
    """The parts of a library title, "原作者 - 曲名 (演奏者)": "周杰伦 - 搁浅 (MappleZS)" is 周杰伦, 搁浅 and MappleZS.
    The performer is what the parentheses that end it hold, theirs too ("菊花台 (William Gan (PianoMan))"). What the
    title leaves out comes from the composer and performer fields."""
    artist, dash, rest = title.partition(" - ")
    if not dash:
        artist, rest = "", title
    rest = rest.rstrip()
    opened = _closing_parentheses(rest)
    piece = (rest[:opened] if opened is not None else rest).strip()
    return {"artist": artist.strip() or composer, "piece": piece or title.strip(),
            "performer": (rest[opened + 1:-1].strip() if opened is not None else "") or performer}


def _closing_parentheses(text: str) -> int | None:
    """Where the parentheses that end text open, any inside them included; None when it does not end in them."""
    depth = 0
    for at in range(len(text) - 1, -1, -1):
        if text[at] in ")）":
            depth += 1
        elif text[at] in "(（":
            depth -= 1
        if depth == 0:
            return at if at < len(text) - 1 else None
    return None


def artist_key(name: str) -> str:
    """One artist however their name is written: the letters' case and width, accents, spaces and punctuation aside
    (AC/DC and AC_DC, Adele and adele, Dvořák and Dvorak). Empty for a name with no letters or digits."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(name)).casefold() if ch.isalnum())


def make_title(piece: str, artist: str = "", performer: str = "") -> str:
    """title_parts() the other way round: the one way a piece's title is made from its names (NAMES)."""
    title = f"{artist} - {piece}" if artist else piece
    return f"{title} ({performer})" if performer else title


def safe_name(name: str) -> str:
    """A file name from a title: no characters file systems reject, and not too long."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip(" .")
    name = name.encode("utf-8")[:160].decode("utf-8", errors="ignore").rstrip(" .")
    return name or "Untitled"


def free_name(folder: Path, name: str, same_as: Path | None = None) -> Path:
    """Where a file named name goes in folder: name itself, or "<stem> (2)<suffix>" and so on when it is taken. A file
    there with the same content as same_as is the one wanted, and is returned as it is."""
    stem, suffix = Path(name).stem, Path(name).suffix
    dest, n = folder / name, 2
    while dest.exists() and not (same_as and dest.is_file() and filecmp.cmp(same_as, dest, shallow=False)):
        dest, n = folder / f"{stem} ({n}){suffix}", n + 1
    return dest


def file_result(lib: "Library", produced: Path, title: str, *, source_type: str, source_url: str,
                composer: str = "", performer: str = "", aliases: str = "", tags: str = "") -> dict:
    """Put a freshly transcribed MIDI into the MIDI folder and the library.

    A new transcription of a source that is already in the library goes to that piece's file, and puts it back if
    the file was gone.
    """
    known = lib.by_source(source_url)
    if known:
        dest = Path(known["midi_path"])
        if Path(produced).resolve() != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(produced, dest)
        return lib.add(dest, known["title"], composer=composer, performer=performer,
                       aliases=aliases, tags=tags, source_type=source_type, source_url=source_url)
    return lib.add(lib.place(Path(produced), title), title, composer=composer, performer=performer,
                   aliases=aliases, tags=tags, source_type=source_type, source_url=source_url)


def upgrade(db: sqlite3.Connection) -> None:
    """Bring a library made by an earlier version up to this one's tables and columns, and its links' form."""
    from .downloads import clean_url  # here: downloads imports this module

    db.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    db.execute("CREATE INDEX IF NOT EXISTS recordings_fingerprint ON recordings(fingerprint)")
    # A Bilibili video's link as an earlier version kept it (m., av…, a trailing slash, ?p=1) takes the one form it has
    # now, so that its piece is known when the video comes back.
    for (url,) in db.execute("SELECT DISTINCT source_url FROM recordings "
                             "WHERE source_url LIKE '%bilibili.com/video/%'").fetchall():
        if (canonical := clean_url(url)) != url:
            db.execute("UPDATE recordings SET source_url = ? WHERE source_url = ?", (canonical, url))
    db.commit()


def move_old_library() -> None:
    """A checkout's library from before (output/), moved over to the new places once: before anything else is
    written there, so the first time PianaSoul starts it is the first thing it does. Not when the data folder is
    chosen (PIANASOUL_DATA, as in development and tests)."""
    if not DB_PATH.exists() and not os.environ.get("PIANASOUL_DATA") and (OLD_OUTPUT / "library.db").is_file():
        from .migrate import migrate

        print(migrate(OLD_OUTPUT, MUSIC_DIR, DATA_DIR).summary(), flush=True)


class Library:
    """The recordings, and the app's other small tables, in one SQLite file. Safe to use from several threads:
    the app's server, its downloads and the piano all use the same library."""

    def __init__(self, db_path: Path = DB_PATH, music_dir: Path = MUSIC_DIR):
        if db_path == DB_PATH:
            move_old_library()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.music_dir = Path(music_dir).resolve()
        self.transcribed = self.music_dir / TRANSCRIBED  # where the pieces PianaSoul makes are filed, by title
        self.uploaded = self.music_dir / UPLOADED  # where the phone's files go
        self.artwork = self.music_dir / ARTWORK
        self.playlist_artwork = Path(db_path).parent / "playlist-artwork"
        self.picture_originals = Path(db_path).parent / "picture-originals"
        self.lock = threading.RLock()  # one statement and its commit at a time
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        upgrade(self.db)
        # Files keep the names of their pieces: one renamed before its file followed takes its title now.
        for row in self.db.execute("SELECT id, title, midi_path FROM recordings").fetchall():
            named = str(self._named(Path(row["midi_path"]), row["title"]))
            if named != row["midi_path"]:
                self.db.execute("UPDATE recordings SET midi_path = ? WHERE id = ?", (named, row["id"]))
        self.db.commit()
        self._merge_artists()

    def execute(self, sql: str, params=()) -> list[sqlite3.Row]:
        """Run one statement, commit it, and return its rows."""
        with self.lock:
            rows = self.db.execute(sql, params).fetchall()
            self.db.commit()
            return rows

    def count(self) -> int:
        return self.execute("SELECT COUNT(*) FROM recordings")[0][0]

    def get(self, rec_id: int) -> dict | None:
        rows = self.execute("SELECT * FROM recordings WHERE id = ?", (rec_id,))
        return self._public(rows[0]) if rows else None

    def by_source(self, source_url: str) -> dict | None:
        rows = self.execute("SELECT * FROM recordings WHERE source_url = ? ORDER BY id DESC LIMIT 1", (source_url,))
        return self._public(rows[0]) if rows else None

    def all(self) -> list[dict]:
        return [self._public(r) for r in self.execute("SELECT * FROM recordings ORDER BY title")]

    def add(self, midi_path: Path, title: str, *, composer: str = "", performer: str = "",
            aliases: str = "", tags: str = "", source_type: str = "local", source_url: str = "") -> dict:
        """Add or update a recording. One already known by its path takes its new content along, its artwork too (as
        refresh)."""
        midi_path = Path(midi_path).resolve()
        stats, content = midi_stats(midi_path), fingerprint(midi_path)
        with self.lock:
            known = self.execute("SELECT fingerprint FROM recordings WHERE midi_path = ?", (str(midi_path),))
            if known and known[0][0] and known[0][0] != content:
                self._artwork_follows(known[0][0], content)
            self.execute(
                """INSERT INTO recordings (title, composer, performer, aliases, tags, source_type, source_url,
                       midi_path, notes, velocity_min, velocity_max, pedal_presses, duration_s, added_at, fingerprint,
                       stamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(midi_path) DO UPDATE SET title = excluded.title,
                       composer = COALESCE(NULLIF(excluded.composer, ''), composer),
                       performer = COALESCE(NULLIF(excluded.performer, ''), performer),
                       aliases = COALESCE(NULLIF(excluded.aliases, ''), aliases),
                       tags = COALESCE(NULLIF(excluded.tags, ''), tags),
                       source_type = excluded.source_type,
                       source_url = COALESCE(NULLIF(excluded.source_url, ''), source_url),
                       notes = excluded.notes, velocity_min = excluded.velocity_min,
                       velocity_max = excluded.velocity_max, pedal_presses = excluded.pedal_presses,
                       duration_s = excluded.duration_s, fingerprint = excluded.fingerprint, stamp = excluded.stamp""",
                (title, composer, performer, aliases, tags, source_type, source_url, str(midi_path),
                 stats["notes"], stats["velocity_min"], stats["velocity_max"], stats["pedal_presses"],
                 stats["duration_s"], _now(), content, stamp(midi_path)))
        return self._public(self.execute("SELECT * FROM recordings WHERE midi_path = ?", (str(midi_path),))[0])

    def refresh(self, rec_id: int) -> None:
        """A file changed where it is: what the library knows of its content follows, its artwork too."""
        rec = self.get(rec_id)
        path = Path(rec["midi_path"])
        stats, content = midi_stats(path), fingerprint(path)
        with self.lock:  # with a picture being kept for it, one after the other
            if rec["fingerprint"] and content != rec["fingerprint"]:
                self._artwork_follows(rec["fingerprint"], content)
            self.execute("UPDATE recordings SET notes = ?, velocity_min = ?, velocity_max = ?, pedal_presses = ?, "
                         "duration_s = ?, fingerprint = ?, stamp = ? WHERE id = ?",
                         (*(stats[k] for k in ("notes", "velocity_min", "velocity_max", "pedal_presses", "duration_s")),
                          content, stamp(path), rec_id))

    def _artwork_follows(self, old: str, new: str) -> None:
        """A piece's content changed from old to new: its artwork goes along. Copied when another piece still has the
        old content (a copy of the file), and never over the artwork the new content has already (another piece's)."""
        source, target = self.piece_artwork({"fingerprint": old}), self.piece_artwork({"fingerprint": new})
        if not source.exists():
            return
        shared = len(self.execute("SELECT id FROM recordings WHERE fingerprint = ?", (old,))) > 1
        if target.exists():
            if not shared:
                self._forget_original_of(source)
                send2trash(str(source))  # the new content has its own: this one is not lost, it can be put back
        elif shared:
            shutil.copy2(source, target)
        else:
            source.replace(target)

    def moved(self, fingerprint: str) -> dict | None:
        """The piece whose file, now gone, had this content: the file it is moved or renamed to is it."""
        rows = self.execute("SELECT * FROM recordings WHERE fingerprint = ? ORDER BY id", (fingerprint,))
        return next((rec for rec in map(self._public, rows) if not rec["file_exists"]), None)

    def move(self, rec_id: int, path: Path) -> None:
        self.execute("UPDATE recordings SET midi_path = ?, stamp = ? WHERE id = ?",
                     (str(Path(path).resolve()), stamp(path), rec_id))

    # ---- the recordings to transcribe (audio) ------------------------------------------------------
    def audio(self) -> list[dict]:
        return [dict(r) for r in self.execute("SELECT * FROM audio ORDER BY id")]

    def audio_item(self, audio_id: int) -> dict | None:
        rows = self.execute("SELECT * FROM audio WHERE id = ?", (audio_id,))
        return dict(rows[0]) if rows else None

    def audio_at(self, path: Path) -> dict | None:
        rows = self.execute("SELECT * FROM audio WHERE path = ?", (str(Path(path).resolve()),))
        return dict(rows[0]) if rows else None

    def add_audio(self, path: Path) -> None:
        path = Path(path).resolve()
        self.execute("INSERT INTO audio (path, fingerprint, stamp, added_at) VALUES (?, ?, ?, ?)",
                     (str(path), fingerprint(path), stamp(path), _now()))

    def moved_audio(self, fingerprint: str) -> dict | None:
        """The recording to transcribe whose file, now gone, had this content."""
        rows = self.execute("SELECT * FROM audio WHERE fingerprint = ? ORDER BY id", (fingerprint,))
        return next((dict(r) for r in rows if not self.here(Path(r["path"]))), None)

    def move_audio(self, audio_id: int, path: Path) -> None:
        """Its piece keeps pointing at it (source_url, as for every piece made from a file)."""
        path = Path(path).resolve()
        with self.lock:
            old = self.audio_item(audio_id)
            self.execute("UPDATE audio SET path = ?, stamp = ? WHERE id = ?", (str(path), stamp(path), audio_id))
            self.execute("UPDATE recordings SET source_url = ? WHERE source_url = ?", (str(path), old["path"]))

    def refresh_audio(self, audio_id: int) -> None:
        path = Path(self.audio_item(audio_id)["path"])
        self.execute("UPDATE audio SET fingerprint = ?, stamp = ? WHERE id = ?",
                     (fingerprint(path), stamp(path), audio_id))

    def delete_audio(self, audio_id: int) -> bool:
        """Take a recording to transcribe out of the library, its file to the Trash."""
        item = self.audio_item(audio_id)
        if item is None:
            return False
        if self.here(Path(item["path"])):
            send2trash(item["path"])
        self.execute("DELETE FROM audio WHERE id = ?", (audio_id,))
        return True

    def set_audio(self, audio_id: int, **fields) -> None:
        """status, recording_id and error."""
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE audio SET {assignments} WHERE id = ?", (*fields.values(), audio_id))

    def update(self, rec_id: int, **fields) -> dict | None:
        fields = {k: v for k, v in fields.items() if k in FIELDS and v is not None}
        if fields:
            with self.lock:
                rec = self.get(rec_id)
                if rec and "title" in fields:
                    fields["midi_path"] = str(self._named(Path(rec["midi_path"]), fields["title"]))
                assignments = ", ".join(f"{k} = ?" for k in fields)
                self.execute(f"UPDATE recordings SET {assignments} WHERE id = ?", (*fields.values(), rec_id))
        return self.get(rec_id)

    def _taken(self, path: Path, own: Path | None = None) -> bool:
        """Whether a name in Transcribed/ is another piece's: its file is there, or its record still is (the file
        gone by hand). A piece never takes another's name, so a file and its record always belong together."""
        if path.exists() and not (own and path.samefile(own)):  # samefile: only the letters' case changes
            return True
        query = "SELECT 1 FROM recordings WHERE midi_path = ?"
        return self.db.execute(query, (str(path.resolve()),)).fetchone() is not None

    def place(self, src: Path, title: str) -> Path:
        """Copy a MIDI file PianaSoul made into Transcribed/ as '<title>.mid', or '<title> (2).mid' and so on when the
        name is another piece's; the same file already there is used as it is."""
        self.transcribed.mkdir(parents=True, exist_ok=True)
        dest = self._free(title, src)
        if not dest.exists():
            shutil.copy2(src, dest)
        return dest

    def _free(self, title: str, src: Path | None = None) -> Path:
        """The name a piece titled so takes in Transcribed/: '<title>.mid', or '<title> (2).mid' and so on when that
        is another piece's; a file there the same as src is the one wanted."""
        safe, n = safe_name(title), 2
        dest = self.transcribed / f"{safe}.mid"
        while self._taken(dest) and not (src and dest.is_file() and filecmp.cmp(src, dest, shallow=False)):
            dest, n = self.transcribed / f"{safe} ({n}).mid", n + 1
        return dest

    def making(self, title: str, *, composer: str = "", performer: str = "", aliases: str = "", tags: str = "",
               source_type: str, source_url: str, duration_s: float) -> dict:
        """A new piece being made, from when it can play as it is made (Downloads): its record, under its names and
        as long as its recording, at the file it will have in Transcribed/, which comes once it is made (made). It is
        not in the library's lists and searches until then."""
        with self.lock:
            dest = self._free(title).resolve()
            self.execute("""INSERT INTO recordings (title, composer, performer, aliases, tags, source_type, source_url,
                                midi_path, duration_s, added_at, making) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                         (title, composer, performer, aliases, tags, source_type, source_url, str(dest),
                          round(duration_s, 1), _now()))
            return self._public(self.execute("SELECT * FROM recordings WHERE midi_path = ?", (str(dest),))[0])

    def made(self, rec_id: int, produced: Path) -> dict:
        """A piece being made, made: its MIDI file into Transcribed/, written whole and then named (by its title as it
        is now, renamed meanwhile), and it is in the library as any other piece."""
        with self.lock:
            rec = self.get(rec_id)
            dest = Path(rec["midi_path"])
            if dest.exists():  # a file put there by hand meanwhile: the piece takes the next name
                dest = self._free(rec["title"]).resolve()
            self.transcribed.mkdir(parents=True, exist_ok=True)
            staged = dest.with_name(f".{dest.name}")  # hidden from the library folder's watch until it is whole
            shutil.copy2(produced, staged)
            os.replace(staged, dest)
            dest = self._named(dest, rec["title"])
            self.execute("UPDATE recordings SET midi_path = ?, making = 0 WHERE id = ?", (str(dest), rec_id))
            self.refresh(rec_id)
        return self.get(rec_id)

    def _named(self, midi: Path, title: str) -> Path:
        """A file in Transcribed/ is named after its title, as place names a new one; any other file is yours and
        keeps its name. Returns where the file is now."""
        if midi.parent != self.transcribed or not midi.is_file():
            return midi
        safe = safe_name(title)
        if re.fullmatch(re.escape(safe) + r"( \(\d+\))?", midi.stem):  # " (2)": another piece has the same title
            return midi
        dest, n = self.transcribed / f"{safe}.mid", 2
        while self._taken(dest, midi):
            dest, n = self.transcribed / f"{safe} ({n}).mid", n + 1
        midi.rename(dest)
        return dest.resolve()

    def edit(self, rec_id: int, piece: str, artist: str = "", performer: str = "") -> dict | None:
        """New names from the app (or her): the title becomes "原作者 - 曲名 (演奏者)", and composer and performer
        follow. The piece's own artist written anew (the same artist: artist_key) is written so from now on, on all
        their pieces (respell); another artist the library has keeps the name it has there (known_artist)."""
        rec, piece = self.get(rec_id), piece.strip()
        if rec is None or not piece:
            return rec
        was = title_parts(rec["title"], rec["composer"], rec["performer"])
        names = {}
        for part, name in (("artist", artist), ("performer", performer)):
            name = str(name).strip()
            names[part] = name if artist_key(name) == artist_key(was[part]) else self.known_artist(name, rec_id)
        self.update(rec_id, title=make_title(piece, names["artist"], names["performer"]),
                    composer=names["artist"], performer=names["performer"])
        for part, name in names.items():
            if name != was[part] and artist_key(name) == artist_key(was[part]):
                self.respell(name)
        return self.get(rec_id)

    def delete(self, rec_id: int) -> bool:
        """Take a recording out of the library, its file to the Trash (yours as well as one in Transcribed/). The
        recording it was transcribed from stays, to be transcribed again."""
        with self.lock:  # its artwork neither moved by a refresh nor counted by another delete meanwhile
            rec = self.get(rec_id)
            if rec is None:
                return False
            if rec["file_exists"]:
                send2trash(rec["midi_path"])
            artwork = self.piece_artwork(rec) if rec["fingerprint"] else None
            sharing = self.execute("SELECT id FROM recordings WHERE fingerprint = ?", (rec["fingerprint"],))
            if artwork and artwork.exists() and len(sharing) == 1:
                self._forget_original_of(artwork)
                send2trash(str(artwork))  # with its file: put back together, they belong together again
            self.db.execute("DELETE FROM recordings WHERE id = ?", (rec_id,))
            self.db.execute("UPDATE audio SET recording_id = NULL WHERE recording_id = ?", (rec_id,))
            self.db.execute("DELETE FROM plays WHERE recording_id = ?", (rec_id,))
            for row in self.db.execute("SELECT playlist_id FROM playlist_items WHERE recording_id = ?", (rec_id,)).fetchall():
                self._renumber(row[0], [i for i in self._playlist_ids(row[0]) if i != rec_id])
            self.db.commit()
        return True

    # ---- playlists ------------------------------------------------------------------------------------
    def playlists(self) -> list[dict]:
        """Every playlist, the newest first: id, name, description, ids (its pieces in order), created_at, updated_at,
        artwork (its version, or None)."""
        with self.lock:
            rows = self.db.execute("SELECT * FROM playlists ORDER BY created_at DESC, id DESC").fetchall()
            return [self._playlist(row) for row in rows]

    def playlist(self, playlist_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
            return self._playlist(row) if row else None

    def new_playlist(self, name: str, ids=()) -> dict:
        """A new playlist with these pieces (each once, those in the library). ValueError for an empty name."""
        name, now = _playlist_name(name), _now(PLAYLIST_TIME)
        with self.lock:
            playlist_id = self.db.execute("INSERT INTO playlists (name, created_at, updated_at) VALUES (?, ?, ?)",
                                          (name, now, now)).lastrowid
            self._renumber(playlist_id, self._known(ids))
            self.db.commit()
            self._replace_picture(self.playlist_cover(playlist_id), None)  # one deleted before may have had this id
        return self.playlist(playlist_id)

    def edit_playlist(self, playlist_id: int, name: str, description: str) -> dict | None:
        """Its name and its description (简介), as edited together. ValueError for an empty name or a description
        over DESCRIPTION_LIMIT; None when there is no such playlist."""
        name, description = _playlist_name(name), _description(description)
        with self.lock:
            if not self._touch(playlist_id, "name = ?, description = ?", (name, description)):
                return None
            self.db.commit()
        return self.playlist(playlist_id)

    def delete_playlist(self, playlist_id: int) -> bool:
        """The playlist goes, with its artwork; its pieces stay in the library."""
        with self.lock:
            self.db.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
            deleted = self.db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,)).rowcount > 0
            self.db.commit()
            self._replace_picture(self.playlist_cover(playlist_id), None)  # before a new playlist can take its id
        return deleted

    def add_to_playlist(self, playlist_id: int, ids) -> list[int] | None:
        """At its end, those of the pieces not in it yet; returns them (None when there is no such playlist)."""
        with self.lock:
            have = self._playlist_ids(playlist_id)
            added = [i for i in self._known(ids) if i not in have]
            if not added:  # nothing changes, and it is not changed last
                return [] if self.db.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() else None
            if not self._touch(playlist_id):
                return None
            self._renumber(playlist_id, have + added)
            self.db.commit()
        return added

    def remove_from_playlist(self, playlist_id: int, rec_id: int) -> bool:
        with self.lock:
            have = self._playlist_ids(playlist_id)
            if rec_id not in have:
                return False
            self._touch(playlist_id)
            self._renumber(playlist_id, [i for i in have if i != rec_id])
            self.db.commit()
        return True

    def move_in_playlist(self, playlist_id: int, rec_id: int, index: int) -> bool:
        """The piece to index in the playlist (past either end: first or last)."""
        with self.lock:
            have = self._playlist_ids(playlist_id)
            if rec_id not in have:
                return False
            have.remove(rec_id)
            have.insert(max(0, min(int(index), len(have))), rec_id)
            self._touch(playlist_id)
            self._renumber(playlist_id, have)
            self.db.commit()
        return True

    def _playlist(self, row: sqlite3.Row) -> dict:
        return {"id": row["id"], "name": row["name"], "description": row["description"], "ids": self._playlist_ids(row["id"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "artwork": _version(self.playlist_cover(row["id"]))}

    # ---- artwork and pictures -------------------------------------------------------------------------
    def piece_artwork(self, rec: dict) -> Path:
        return self.artwork / "Pieces" / f"{rec['fingerprint']}.jpg"

    def artist_picture(self, name: str) -> Path:
        return self.artwork / "Artists" / f"{safe_name(name)}.jpg"

    def playlist_cover(self, playlist_id: int) -> Path:
        return self.playlist_artwork / f"{int(playlist_id)}.jpg"

    def set_piece_artwork(self, rec_id: int, jpeg: bytes | None) -> bool:
        """A piece's artwork (None takes it away). False when there is no such piece, or its file is not there."""
        with self.lock:  # its content does not change meanwhile (refresh)
            rec = self.get(rec_id)
            if not rec or not rec["file_exists"] or not rec["fingerprint"]:
                return False
            self._replace_picture(self.piece_artwork(rec), jpeg)
        return True

    def set_artist_picture(self, name: str, jpeg: bytes | None) -> None:
        with self.lock:
            self._replace_picture(self.artist_picture(name), jpeg)

    def set_playlist_cover(self, playlist_id: int, jpeg: bytes | None) -> bool:
        with self.lock:  # not for a playlist deleted meanwhile
            if not self._touch(playlist_id):
                return False
            self.db.commit()
            self._replace_picture(self.playlist_cover(playlist_id), jpeg)
        return True

    def _replace_picture(self, path: Path, jpeg: bytes | None) -> None:
        """A picture kept anew, or taken away: the photo the one there was made from goes with it (one kept anew
        comes with its own, from the phone, once it is kept)."""
        if path.is_file() and path.read_bytes() != jpeg:
            self._forget_original_of(path)
        _keep_picture(path, jpeg)

    def _forget_original_of(self, path: Path) -> None:
        """A picture going (replaced, taken away, to the Trash with its piece, its playlist deleted): the photo it was
        made from goes too, unless another picture is the very same one (the same bytes), whose photo it is as well."""
        try:
            content = path.read_bytes()
        except OSError:  # none there
            return
        digest = hashlib.sha256(content).hexdigest()
        if not self._picture_kept(digest, len(content), besides=path):
            self.forget_picture_original(digest)

    def _picture_kept(self, digest: str, size: int, besides: Path | None = None) -> bool:
        """Whether a picture kept now (a piece's artwork, an artist's picture, a playlist's cover), besides the one at
        besides, has this content. One that cannot be read is taken to have it: nothing it may be made from is lost."""
        for folder in (self.artwork / "Pieces", self.artwork / "Artists", self.playlist_artwork):
            for other in folder.glob("*.jpg"):
                try:
                    if besides is not None and other.samefile(besides):
                        continue
                    if other.stat().st_size == size and hashlib.sha256(other.read_bytes()).hexdigest() == digest:
                        return True
                except OSError:
                    return True
        return False

    # ---- the photos pictures were made from ----------------------------------------------------------
    def keep_picture_original(self, digest: str, size: int, jpeg: bytes, at: dict) -> None:
        """The photo the picture with this content (digest: its SHA-256, in hex; size: its bytes) was made from, and the
        square of it the picture is (at: x, y, side in the photo's pixels). ValueError for a digest or a square that is
        not one; LookupError when no picture kept now is that one (replaced or gone while its photo was on its way)."""
        x, y, side = (float(at[k]) for k in ("x", "y", "side"))
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not (x >= 0 and y >= 0 and 0 < side < 1e5):
            raise ValueError("not a picture's digest and square")
        with self.lock:
            if not self._picture_kept(digest, size):
                raise LookupError("no picture is made from it")
            _keep_picture(self.picture_originals / f"{digest}.jpg", jpeg)
            self.db.execute("INSERT INTO picture_originals (digest, x, y, side) VALUES (?, ?, ?, ?) ON CONFLICT (digest) "
                            "DO UPDATE SET x = excluded.x, y = excluded.y, side = excluded.side", (digest, x, y, side))
            self.db.commit()

    def picture_original(self, digest: str) -> tuple[Path, dict] | None:
        """The photo a picture was made from, and where in it, or None (made before photos were kept, or gone)."""
        path = self.picture_originals / f"{digest}.jpg"
        rows = self.execute("SELECT x, y, side FROM picture_originals WHERE digest = ?", (digest,))
        return (path, dict(rows[0])) if rows and path.is_file() else None

    def forget_picture_original(self, digest: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM picture_originals WHERE digest = ?", (digest,))
            self.db.commit()
            (self.picture_originals / f"{digest}.jpg").unlink(missing_ok=True)

    def pictures(self, names) -> dict[str, int]:
        """Of these artists, those with a picture, and its version."""
        return {name: version for name in set(names) if (version := _version(self.artist_picture(name)))}

    # ---- artists: one name each ---------------------------------------------------------------------------
    def artist_names(self, besides: int | None = None) -> list[str]:
        """The artists in the library (原作者 and 演奏者 alike, as the app and she see them: title_parts), the one
        with the most pieces first; besides: not counting that piece."""
        counts = Counter()
        for row in self.execute("SELECT id, title, composer, performer FROM recordings"):
            if row["id"] != besides:
                parts = title_parts(row["title"], row["composer"], row["performer"])
                counts.update({name for name in (parts["artist"], parts["performer"]) if name})
        return sorted(counts, key=lambda name: (-counts[name], name))

    def artist_spellings(self, besides: int | None = None) -> dict[str, str]:
        """Each artist's name as the library has it (the spelling with the most pieces), by artist_key."""
        spellings: dict[str, str] = {}
        for name in self.artist_names(besides):
            spellings.setdefault(artist_key(name), name)
        spellings.pop("", None)
        return spellings

    def known_artist(self, name: str, besides: int | None = None) -> str:
        """The name the library already has for this artist (artist_key), else the name as given: a new piece by
        Adele, named adele, is Adele's. besides: not counting that piece's own names (being edited)."""
        name = str(name).strip()
        return self.artist_spellings(besides).get(artist_key(name), name)

    def respell(self, kept: str) -> None:
        """An artist's other spellings (artist_key) all become this one."""
        for other in self.artist_names():
            if other != kept and artist_key(other) == artist_key(kept):
                self._take_spelling(other, kept)

    def _merge_artists(self) -> None:
        """An artist the library has under several spellings (from before names were matched as they came in) is
        one artist, with the spelling that has the most pieces."""
        spellings: dict[str, list[str]] = {}
        for name in self.artist_names():
            spellings.setdefault(artist_key(name), []).append(name)
        for key, (kept, *others) in spellings.items():
            for other in others if key else ():
                self._take_spelling(other, kept)

    def _take_spelling(self, other: str, kept: str) -> None:
        """Each piece of other's is kept's: its composer and performer, and its title where the title is made of its
        names (one that does not come apart into them and back, as make_title writes it, is left as it is). other's
        picture goes to kept where kept has none (else to the Trash), and its description after kept's (as much as one
        holds)."""
        swap = lambda name: kept if name == other else name
        with self.lock:
            for row in self.execute("SELECT id, title, composer, performer FROM recordings"):
                own, title = title_parts(row["title"]), row["title"]
                if other in (own["artist"], own["performer"]) and make_title(**own) == title:
                    title = make_title(own["piece"], swap(own["artist"]), swap(own["performer"]))
                if title != row["title"] or other in (row["composer"], row["performer"]):
                    self.update(row["id"], title=title, composer=swap(row["composer"]), performer=swap(row["performer"]))
            picture, theirs = self.artist_picture(other), self.artist_picture(kept)
            if picture.is_file() and not theirs.exists():
                picture.rename(theirs)
            elif picture.is_file() and not picture.samefile(theirs):  # kept has its own: this one to the Trash
                self._forget_original_of(picture)
                send2trash(str(picture))
            said = dict(self.db.execute("SELECT name, description FROM artists WHERE name IN (?, ?)", (other, kept)).fetchall())
            if other in said:
                both = "\n\n".join(dict.fromkeys(t for t in (said.get(kept), said[other]) if t))[:DESCRIPTION_LIMIT]
                self.db.execute("DELETE FROM artists WHERE name = ?", (other,))
                self.db.execute("INSERT INTO artists (name, description) VALUES (?, ?) ON CONFLICT (name) DO UPDATE "
                                "SET description = excluded.description", (kept, both))
                self.db.commit()

    # ---- what the user wrote about artists ------------------------------------------------------------
    def set_artist_description(self, name: str, description: str) -> None:
        """An artist's description (简介), by name; an empty one takes it away. ValueError for one over
        DESCRIPTION_LIMIT."""
        description = _description(description)
        with self.lock:
            if description:
                self.db.execute("INSERT INTO artists (name, description) VALUES (?, ?) "
                                "ON CONFLICT (name) DO UPDATE SET description = excluded.description", (name, description))
            else:
                self.db.execute("DELETE FROM artists WHERE name = ?", (name,))
            self.db.commit()

    def descriptions(self, names) -> dict[str, str]:
        """Of these artists, those with a description, and it."""
        names = set(names)
        with self.lock:
            rows = self.db.execute("SELECT name, description FROM artists").fetchall()
        return {name: text for name, text in rows if name in names}

    def _playlist_ids(self, playlist_id: int) -> list[int]:
        return [r[0] for r in self.db.execute(
            "SELECT recording_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (playlist_id,))]

    def _known(self, ids) -> list[int]:
        """The ids of pieces in the library, each once, in their order."""
        known = {r[0] for r in self.db.execute("SELECT id FROM recordings")}
        return [i for i in dict.fromkeys(int(i) for i in ids) if i in known]

    def _touch(self, playlist_id: int, change: str = "", params=()) -> bool:
        """Changed now (and with change, a column set): False when there is no such playlist."""
        sets = ", ".join(filter(None, [change, "updated_at = ?"]))
        return self.db.execute(f"UPDATE playlists SET {sets} WHERE id = ?",
                               (*params, _now(PLAYLIST_TIME), playlist_id)).rowcount > 0

    def _renumber(self, playlist_id: int, ids: list[int]) -> None:
        self.db.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        self.db.executemany("INSERT INTO playlist_items (playlist_id, recording_id, position) VALUES (?, ?, ?)",
                            [(playlist_id, rec_id, n) for n, rec_id in enumerate(ids)])

    def favorite(self, rec_id: int, on: bool) -> None:
        self.execute("UPDATE recordings SET favorited_at = ? WHERE id = ?", (_now() if on else None, rec_id))

    def heard(self, rec_id: int) -> None:
        """A piece heard once more (a play): counted, with when; the first one also says since when plays are counted."""
        now = _now()
        self.execute("INSERT INTO plays (recording_id, played_at) VALUES (?, ?)", (rec_id, now))
        if not self.setting(PLAYS_SINCE):
            self.set_setting(PLAYS_SINCE, now)

    def play_counts(self, since: str = "") -> dict[int, int]:
        """How often each piece was heard, since then (an ISO time) if given; pieces never heard are left out."""
        rows = self.execute("SELECT recording_id, COUNT(*) FROM plays WHERE played_at >= ? GROUP BY recording_id", (since,))
        return {rec_id: n for rec_id, n in rows}

    def last_plays(self) -> dict[int, str]:
        """When each piece was last heard (a play, not a start); pieces never heard are left out."""
        return dict(self.execute("SELECT recording_id, MAX(played_at) FROM plays GROUP BY recording_id"))

    def first_play(self) -> str | None:
        """Since when plays are counted: kept on its own, so that deleting a piece (and its plays) does not move it."""
        return self.setting(PLAYS_SINCE) or None

    def played(self, rec_id: int, on: bool = True) -> None:
        """When a recording last played (on=False takes it out of 最近播放)."""
        self.execute("UPDATE recordings SET last_played_at = ? WHERE id = ?", (_now() if on else None, rec_id))

    def mark_named(self, ids: list[int]) -> None:
        """The chat model has been asked for these pieces' names (enrich): not again."""
        self.execute(f"UPDATE recordings SET named_at = ? WHERE id IN ({', '.join('?' * len(ids))})", (_now(), *ids))

    def set_video(self, rec_id: int, video: dict) -> None:
        """What the recording's video page said when it was downloaded: title, channel (and its picture), views,
        date and thumbnail."""
        fields = {k: video[k] for k in VIDEO_FIELDS if video.get(k) is not None}
        if fields:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            self.execute(f"UPDATE recordings SET {assignments} WHERE id = ?", (*fields.values(), rec_id))

    def setting(self, key: str, default: str = "") -> str:
        rows = self.execute("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0][0] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))

    def playable(self) -> list[dict]:
        """Every recording whose file exists, in the order it was added."""
        rows = (self._public(r) for r in self.execute("SELECT * FROM recordings ORDER BY id"))
        return [r for r in rows if r["file_exists"]]

    def search(self, query: str, limit: int = 8) -> list[dict]:
        wanted = _terms(query)
        if not wanted:
            return []
        query_norm = _norm(query)
        scored = []
        for row in self.execute("SELECT * FROM recordings WHERE NOT making"):  # one being made: in it once made
            hay_text = " ".join(row[f] for f in FIELDS)
            have = _terms(hay_text)
            score = sum(2.0 if len(t) > 1 else 0.5 for t in wanted & have)
            if query_norm and query_norm in _norm(hay_text):
                score += 3
            if score > 0:
                scored.append((score, row))
        # The best match first; among equally good ones a favorite, then the one played last, then the newest.
        scored.sort(key=lambda s: (s[0], s[1]["favorited_at"] or "", s[1]["last_played_at"] or "", s[1]["added_at"]),
                    reverse=True)
        return [self._public(r) | {"score": round(s, 1)} for s, r in scored[:limit]]

    def import_folder(self, folder: Path) -> list[dict]:
        """Copy the MIDI files in a folder into the library folder, as yours, and add them."""
        added = []
        for src in sorted(Path(folder).expanduser().rglob("*")):
            if src.suffix.lower() not in MIDI_TYPES or src.name.startswith("."):
                continue
            if src.name.endswith(".raw.mid") or "extras" in src.parts:  # PianaSoul side files
                continue
            try:
                self.music_dir.mkdir(parents=True, exist_ok=True)
                dest = free_name(self.music_dir, src.name, same_as=src)
                if not dest.exists():
                    shutil.copy2(src, dest)
                added.append(self.add(dest, src.stem, source_type="import", source_url=str(src.resolve())))
            except Exception as exc:  # skip unreadable files, keep importing
                print(f"  skipped {src.name}: {exc}", file=sys.stderr)
        return added

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def here(self, path: Path) -> bool:
        """Whether a file is in the library: in its folder (the one in use now), and there to be read."""
        return path.is_relative_to(self.music_dir) and present(path)

    def origin(self, path: Path) -> str:
        """Where a file is, and so how it came: transcribed (in Transcribed/, made by PianaSoul), uploaded (in
        Uploaded/, sent from the phone) or yours (put in the library folder on the Mac). Moved, it is what its place
        says."""
        return ("transcribed" if path.is_relative_to(self.transcribed) else
                "uploaded" if path.is_relative_to(self.uploaded) else "yours")

    def _public(self, row: sqlite3.Row) -> dict:
        """A record, with whether its file is in the library and how it came (origin)."""
        d = dict(row)
        d["making"] = bool(d["making"])
        d["file_exists"] = self.here(Path(d["midi_path"]))
        d["origin"] = self.origin(Path(d["midi_path"]))
        d["artwork"] = _version(self.piece_artwork(d)) if d["fingerprint"] else None
        return d


def known_artists(names: list[str], most: int = 200) -> str:
    """For the chat model naming pieces: the artists the library has already (the most pieces first), whose names
    it is to use as they are for the same person in another language or spelling (周杰伦 for Jay Chou)."""
    names = names[:most]
    return ("when it is one of the artists the library has already, in any language or spelling, their name exactly as "
            f"it is here: {json.dumps(names, ensure_ascii=False)}") if names else ""


def enrich(lib: Library, complete_json) -> int:
    """Ask the chat model to fill in the artist (where the library has none), aliases (other-language titles)
    and tags: once for each piece, which the model may not know either. Pieces asked about with titles the
    provider refuses (Refused) stay without them: asked again, they would be refused again."""
    todo = [r for r in lib.all() if not r["named_at"] and (not r["composer"] or not r["aliases"])]
    changed = 0
    spellings = lib.artist_spellings()  # once: the artists the model names are matched to them
    for i in range(0, len(todo), 30):
        batch = todo[i:i + 30]
        listing = "\n".join(f'{r["id"]}: {r["title"]}' for r in batch)
        prompt = (
            "These are titles of piano recordings (MIDI files). For each, return a JSON object mapping the id "
            f"to {{\"artist\": str, \"aliases\": str, \"tags\": str}}. artist: {NAMES['artist']}; {known_artists(lib.artist_names())} "
            f"aliases: {ALIASES}. tags: 2-4 short English words about genre "
            "and mood, separated by ', '. Use an empty string when unsure; do not guess names you do not know."
            "\nReturn only the JSON object.\n\n" + listing)
        try:
            data = complete_json(prompt)
        except Refused as exc:
            print(f"  ({len(batch)} piece(s) not named: {exc})", flush=True)
            data = {}
        for key, info in (data or {}).items():
            if not str(key).isdigit() or not isinstance(info, dict):
                continue
            rec = lib.get(int(key))
            if rec is None:
                continue
            artist = str(info.get("artist") or "").strip()
            lib.update(int(key), composer=rec["composer"] or spellings.get(artist_key(artist), artist),
                       aliases=rec["aliases"] or info.get("aliases", ""),
                       tags=rec["tags"] or info.get("tags", ""))
            changed += 1
        lib.mark_named([r["id"] for r in batch])
    return changed


def format_row(r: dict) -> str:
    who = " · ".join(x for x in (r["composer"], r["performer"]) if x)
    missing = "" if r["file_exists"] else "  [file missing]"
    return f'{r["id"]:>4}  {r["title"]}{"  (" + who + ")" if who else ""}  — {r["notes"]} notes{missing}'


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pianasoul library", description="Manage the local MIDI library.")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="add MIDI files from a folder (copied into the library folder)")
    imp.add_argument("folder")
    sub.add_parser("list", help="list the library")
    sub.add_parser("enrich", help="use the chat model to add artists, aliases and tags")
    args = p.parse_args(argv)

    lib = Library()
    if args.cmd == "import":
        added = lib.import_folder(Path(args.folder))
        print(f"Imported {len(added)} MIDI file(s). Library now has {lib.count()} recording(s).")
    elif args.cmd == "list":
        rows = lib.all()
        for r in rows:
            print(format_row(r))
        print(f"{len(rows)} recording(s) in {DB_PATH}")
    elif args.cmd == "enrich":
        from .llm import complete_json

        print(f"Updated {enrich(lib, complete_json)} recording(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
