"""Local MIDI library: a small SQLite index of every MIDI file PianaSoul knows about.

    python -m pianasoul.library import ~/Desktop/midi      # copy MIDI files into the library folder
    python -m pianasoul.library list
    python -m pianasoul.library enrich                     # let the chat model add artists, aliases, tags

The library is a folder of music (MUSIC_DIR): yours, as you put it there, and Transcribed/, where PianaSoul files
the pieces it makes. Its index and other lasting data are in DATA_DIR, and work files that may go in CACHE_DIR.
Each can be moved with an environment variable (PIANASOUL_MUSIC, PIANASOUL_DATA, PIANASOUL_CACHE).
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import re
import shutil
import sqlite3
import sys
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pretty_midi
from send2trash import send2trash

from .cleanup import pedal_intervals


def _place(variable: str, default: Path) -> Path:
    return Path(os.environ.get(variable) or default).expanduser().resolve()


MUSIC_DIR = _place("PIANASOUL_MUSIC", Path.home() / "Music" / "PianaSoul")  # the library: your music, and
TRANSCRIBED = "Transcribed"  # the subfolder only PianaSoul writes to, with the pieces it made
UPLOADED = "Uploaded"  # where files sent from the phone go: yours, as if put there by hand, and left as they are
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
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
ADDED_COLUMNS = {"favorited_at": "TEXT", "last_played_at": "TEXT",
                 "video_title": "TEXT NOT NULL DEFAULT ''", "channel": "TEXT NOT NULL DEFAULT ''",
                 "views": "INTEGER", "published": "TEXT NOT NULL DEFAULT ''",
                 "thumbnail": "TEXT NOT NULL DEFAULT ''", "channel_avatar": "TEXT NOT NULL DEFAULT ''",
                 "fingerprint": "TEXT NOT NULL DEFAULT ''", "stamp": "TEXT NOT NULL DEFAULT ''",
                 "named_at": "TEXT"}
VIDEO_FIELDS = ("video_title", "channel", "views", "published", "thumbnail", "channel_avatar")
STOPWORDS = {"the", "a", "an", "of", "in", "and", "by", "for", "piano", "solo", "version", "play", "me"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    What the title leaves out comes from the composer and performer fields."""
    artist, dash, rest = title.partition(" - ")
    if not dash:
        artist, rest = "", title
    found = re.search(r"\s*[(（]([^()（）]*)[)）]\s*$", rest)
    piece = (rest[:found.start()] if found else rest).strip()
    return {"artist": artist.strip() or composer, "piece": piece or title.strip(),
            "performer": (found.group(1).strip() if found else "") or performer}


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
    """Bring a library made by an earlier version up to this one's tables and columns."""
    db.executescript(SCHEMA)
    have = {row[1] for row in db.execute("PRAGMA table_info(recordings)")}
    for name, kind in ADDED_COLUMNS.items():
        if name not in have:
            db.execute(f"ALTER TABLE recordings ADD COLUMN {name} {kind}")
    db.execute("CREATE INDEX IF NOT EXISTS recordings_fingerprint ON recordings(fingerprint)")
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
        """Add or update a recording."""
        midi_path = Path(midi_path).resolve()
        stats = midi_stats(midi_path)
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
             stats["duration_s"], _now(), fingerprint(midi_path), stamp(midi_path)))
        return self._public(self.execute("SELECT * FROM recordings WHERE midi_path = ?", (str(midi_path),))[0])

    def refresh(self, rec_id: int) -> None:
        """A file changed where it is: what the library knows of its content follows."""
        rec = self.get(rec_id)
        path = Path(rec["midi_path"])
        stats = midi_stats(path)
        self.execute("UPDATE recordings SET notes = ?, velocity_min = ?, velocity_max = ?, pedal_presses = ?, "
                     "duration_s = ?, fingerprint = ?, stamp = ? WHERE id = ?",
                     (*(stats[k] for k in ("notes", "velocity_min", "velocity_max", "pedal_presses", "duration_s")),
                      fingerprint(path), stamp(path), rec_id))

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
        safe, n = safe_name(title), 2
        dest = self.transcribed / f"{safe}.mid"
        while self._taken(dest) and not (dest.is_file() and filecmp.cmp(src, dest, shallow=False)):
            dest, n = self.transcribed / f"{safe} ({n}).mid", n + 1
        if not dest.exists():
            shutil.copy2(src, dest)
        return dest

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
        """New names from the app: the title becomes "原作者 - 曲名 (演奏者)", and composer and performer follow."""
        piece, artist, performer = piece.strip(), artist.strip(), performer.strip()
        if not piece:
            return self.get(rec_id)
        return self.update(rec_id, title=make_title(piece, artist, performer), composer=artist, performer=performer)

    def delete(self, rec_id: int) -> bool:
        """Take a recording out of the library, its file to the Trash (yours as well as one in Transcribed/). The
        recording it was transcribed from stays, to be transcribed again."""
        rec = self.get(rec_id)
        if rec is None:
            return False
        if rec["file_exists"]:
            send2trash(rec["midi_path"])
        with self.lock:
            self.db.execute("DELETE FROM recordings WHERE id = ?", (rec_id,))
            self.db.execute("UPDATE audio SET recording_id = NULL WHERE recording_id = ?", (rec_id,))
            self.db.commit()
        return True

    def favorite(self, rec_id: int, on: bool) -> None:
        self.execute("UPDATE recordings SET favorited_at = ? WHERE id = ?", (_now() if on else None, rec_id))

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

    def playlist(self) -> list[dict]:
        """Every recording whose file exists, in the order it was added."""
        rows = (self._public(r) for r in self.execute("SELECT * FROM recordings ORDER BY id"))
        return [r for r in rows if r["file_exists"]]

    def search(self, query: str, limit: int = 8) -> list[dict]:
        wanted = _terms(query)
        if not wanted:
            return []
        query_norm = _norm(query)
        scored = []
        for row in self.execute("SELECT * FROM recordings"):
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
        d["file_exists"] = self.here(Path(d["midi_path"]))
        d["origin"] = self.origin(Path(d["midi_path"]))
        return d


def enrich(lib: Library, complete_json) -> int:
    """Ask the chat model to fill in the artist (where the library has none), aliases (other-language titles)
    and tags: once for each piece, which the model may not know either."""
    todo = [r for r in lib.all() if not r["named_at"] and (not r["composer"] or not r["aliases"])]
    changed = 0
    for i in range(0, len(todo), 30):
        batch = todo[i:i + 30]
        listing = "\n".join(f'{r["id"]}: {r["title"]}' for r in batch)
        prompt = (
            "These are titles of piano recordings (MIDI files). For each, return a JSON object mapping the id "
            f"to {{\"artist\": str, \"aliases\": str, \"tags\": str}}. artist: {NAMES['artist']}. "
            f"aliases: {ALIASES}. tags: 2-4 short English words about genre "
            "and mood, separated by ', '. Use an empty string when unsure; do not guess names you do not know."
            "\nReturn only the JSON object.\n\n" + listing)
        data = complete_json(prompt)
        for key, info in (data or {}).items():
            if not str(key).isdigit() or not isinstance(info, dict):
                continue
            rec = lib.get(int(key))
            if rec is None:
                continue
            lib.update(int(key), composer=rec["composer"] or info.get("artist", ""),
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
