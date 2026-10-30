"""Local MIDI library: a small SQLite index of every MIDI file PianaSoul knows about.

    python -m pianasoul.library import ~/Desktop/midi      # copy MIDI files in and index them
    python -m pianasoul.library list
    python -m pianasoul.library enrich                     # let the chat model add artists, aliases, tags
"""

from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import sqlite3
import sys
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pretty_midi

from .cleanup import pedal_intervals

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"  # in the project, wherever a command runs from
MIDI_DIR = OUTPUT_DIR / "MIDI Files"          # the MIDI files you play
DB_PATH = OUTPUT_DIR / "library.db"
CACHE_DIR = OUTPUT_DIR / ".cache"              # downloads, raw MIDI, statistics, task state

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
"""
# Columns added since the first libraries were made; an older library gets them when it is opened.
ADDED_COLUMNS = {"favorited_at": "TEXT", "last_played_at": "TEXT",
                 "video_title": "TEXT NOT NULL DEFAULT ''", "channel": "TEXT NOT NULL DEFAULT ''",
                 "views": "INTEGER", "published": "TEXT NOT NULL DEFAULT ''",
                 "thumbnail": "TEXT NOT NULL DEFAULT ''", "channel_avatar": "TEXT NOT NULL DEFAULT ''"}
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
ALIASES = "other names the piece is known by (in other languages, or its full catalog title), separated by '; '"


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


class Library:
    """The recordings, and the app's other small tables, in one SQLite file. Safe to use from several threads:
    the app's server, its downloads and the piano all use the same library."""

    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.midi_dir = db_path.parent / MIDI_DIR.name  # the MIDI files and playlist sit next to their index
        self.lock = threading.RLock()  # one statement and its commit at a time
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        have = {row["name"] for row in self.db.execute("PRAGMA table_info(recordings)")}
        for name, kind in ADDED_COLUMNS.items():
            if name not in have:
                self.db.execute(f"ALTER TABLE recordings ADD COLUMN {name} {kind}")
        # Files keep the names of their pieces: one renamed before its file followed takes its title now.
        renamed = False
        for row in self.db.execute("SELECT id, title, midi_path FROM recordings").fetchall():
            named = str(self._named(Path(row["midi_path"]), row["title"]))
            if named != row["midi_path"]:
                self.db.execute("UPDATE recordings SET midi_path = ? WHERE id = ?", (named, row["id"]))
                renamed = True
        self.db.commit()
        if renamed:
            self.write_playlist()

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
            aliases: str = "", tags: str = "", source_type: str = "local", source_url: str = "",
            playlist: bool = True) -> dict:
        """Add or update a recording; playlist=False leaves the playlist to the caller, for many at once."""
        midi_path = Path(midi_path).resolve()
        stats = midi_stats(midi_path)
        self.execute(
            """INSERT INTO recordings (title, composer, performer, aliases, tags, source_type, source_url,
                   midi_path, notes, velocity_min, velocity_max, pedal_presses, duration_s, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(midi_path) DO UPDATE SET title = excluded.title,
                   composer = COALESCE(NULLIF(excluded.composer, ''), composer),
                   performer = COALESCE(NULLIF(excluded.performer, ''), performer),
                   aliases = COALESCE(NULLIF(excluded.aliases, ''), aliases),
                   tags = COALESCE(NULLIF(excluded.tags, ''), tags),
                   source_type = excluded.source_type,
                   source_url = COALESCE(NULLIF(excluded.source_url, ''), source_url),
                   notes = excluded.notes, velocity_min = excluded.velocity_min,
                   velocity_max = excluded.velocity_max, pedal_presses = excluded.pedal_presses,
                   duration_s = excluded.duration_s""",
            (title, composer, performer, aliases, tags, source_type, source_url, str(midi_path),
             stats["notes"], stats["velocity_min"], stats["velocity_max"], stats["pedal_presses"],
             stats["duration_s"], _now()))
        if playlist:
            self.write_playlist()
        return self._public(self.execute("SELECT * FROM recordings WHERE midi_path = ?", (str(midi_path),))[0])

    def update(self, rec_id: int, **fields) -> dict | None:
        fields = {k: v for k, v in fields.items() if k in FIELDS and v is not None}
        if fields:
            with self.lock:
                rec = self.get(rec_id)
                if rec and "title" in fields:
                    fields["midi_path"] = str(self._named(Path(rec["midi_path"]), fields["title"]))
                assignments = ", ".join(f"{k} = ?" for k in fields)
                self.execute(f"UPDATE recordings SET {assignments} WHERE id = ?", (*fields.values(), rec_id))
            self.write_playlist()
        return self.get(rec_id)

    def _taken(self, path: Path, own: Path | None = None) -> bool:
        """Whether a name in the MIDI folder is another piece's: its file is there, or its record still is (the file
        gone by hand). A piece never takes another's name, so a file and its record always belong together."""
        if path.exists() and not (own and path.samefile(own)):  # samefile: only the letters' case changes
            return True
        query = "SELECT 1 FROM recordings WHERE midi_path = ?"
        return self.db.execute(query, (str(path.resolve()),)).fetchone() is not None

    def place(self, src: Path, title: str) -> Path:
        """Copy a MIDI file into the MIDI folder as '<title>.mid', or '<title> (2).mid' and so on when the name is
        another piece's; the same file already there is used as it is."""
        self.midi_dir.mkdir(parents=True, exist_ok=True)
        safe, n = safe_name(title), 2
        dest = self.midi_dir / f"{safe}.mid"
        while self._taken(dest) and not (dest.is_file() and filecmp.cmp(src, dest, shallow=False)):
            dest, n = self.midi_dir / f"{safe} ({n}).mid", n + 1
        if not dest.exists():
            shutil.copy2(src, dest)
        return dest

    def _named(self, midi: Path, title: str) -> Path:
        """A file in the library's MIDI folder is named after its title, as place names a new one; a file imported
        in place keeps its name. Returns where the file is now."""
        if midi.parent != self.midi_dir.resolve() or not midi.is_file():
            return midi
        safe = safe_name(title)
        if re.fullmatch(re.escape(safe) + r"( \(\d+\))?", midi.stem):  # " (2)": another piece has the same title
            return midi
        dest, n = self.midi_dir / f"{safe}.mid", 2
        while self._taken(dest, midi):
            dest, n = self.midi_dir / f"{safe} ({n}).mid", n + 1
        midi.rename(dest)
        return dest.resolve()

    def edit(self, rec_id: int, piece: str, artist: str = "", performer: str = "") -> dict | None:
        """New names from the app: the title becomes "原作者 - 曲名 (演奏者)", and composer and performer follow."""
        piece, artist, performer = piece.strip(), artist.strip(), performer.strip()
        if not piece:
            return self.get(rec_id)
        return self.update(rec_id, title=make_title(piece, artist, performer), composer=artist, performer=performer)

    def delete(self, rec_id: int) -> bool:
        """Take a recording out of the library. Its MIDI file goes too when it is in the library's MIDI folder;
        a file imported in place stays where it is."""
        rec = self.get(rec_id)
        if rec is None:
            return False
        with self.lock:
            self.db.execute("DELETE FROM recordings WHERE id = ?", (rec_id,))
            self.db.commit()
        midi = Path(rec["midi_path"])
        if midi.parent == self.midi_dir.resolve():
            midi.unlink(missing_ok=True)
        self.write_playlist()
        return True

    def favorite(self, rec_id: int, on: bool) -> None:
        self.execute("UPDATE recordings SET favorited_at = ? WHERE id = ?", (_now() if on else None, rec_id))

    def played(self, rec_id: int, on: bool = True) -> None:
        """When a recording last played (on=False takes it out of 最近播放)."""
        self.execute("UPDATE recordings SET last_played_at = ? WHERE id = ?", (_now() if on else None, rec_id))

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

    def write_playlist(self) -> None:
        """Rewrite the .m3u playlist from playlist(); files deleted by hand drop out."""
        path = self.midi_dir / "playlist.m3u"
        path.parent.mkdir(parents=True, exist_ok=True)
        folder = path.parent.resolve()
        lines = ["#EXTM3U"]
        for r in self.playlist():
            midi = Path(r["midi_path"])
            entry = midi.name if midi.parent == folder else str(midi)
            lines += [f'#EXTINF:{round(r["duration_s"] or 0)},{r["title"]}', entry]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

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

    def index_midi_folder(self) -> int:
        """Pick up MIDI files put into the MIDI folder by hand, and bring the playlist up to date."""
        known = {r["midi_path"] for r in self.all()}
        added = 0
        for f in sorted(self.midi_dir.glob("*.mid")):
            if str(f.resolve()) not in known:
                try:
                    self.add(f, f.stem, source_type="folder", playlist=False)
                    added += 1
                except Exception:
                    pass
        self.write_playlist()  # once; files deleted by hand drop out too
        return added

    def import_folder(self, folder: Path, *, copy: bool = True) -> list[dict]:
        added = []
        for src in sorted(Path(folder).expanduser().rglob("*")):
            if src.suffix.lower() not in {".mid", ".midi"} or src.name.startswith("."):
                continue
            if src.name.endswith(".raw.mid") or "extras" in src.parts:  # PianaSoul side files
                continue
            try:
                path = self.place(src, src.stem) if copy else src
                added.append(self.add(path, src.stem, source_type="import", source_url=str(src.resolve()),
                                      playlist=False))
            except Exception as exc:  # skip unreadable files, keep importing
                print(f"  skipped {src.name}: {exc}", file=sys.stderr)
        self.write_playlist()
        return added

    def close(self) -> None:
        with self.lock:
            self.db.close()

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["file_exists"] = Path(d["midi_path"]).is_file()
        return d


def enrich(lib: Library, complete_json) -> int:
    """Ask the chat model to fill in the artist (where the library has none), aliases (other-language titles)
    and tags."""
    todo = [r for r in lib.all() if not r["composer"] or not r["aliases"]]
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
    return changed


def format_row(r: dict) -> str:
    who = " · ".join(x for x in (r["composer"], r["performer"]) if x)
    missing = "" if r["file_exists"] else "  [file missing]"
    return f'{r["id"]:>4}  {r["title"]}{"  (" + who + ")" if who else ""}  — {r["notes"]} notes{missing}'


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pianasoul library", description="Manage the local MIDI library.")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="add MIDI files from a folder (copied into output/MIDI Files)")
    imp.add_argument("folder")
    imp.add_argument("--in-place", action="store_true", help="index the files where they are instead of copying")
    sub.add_parser("list", help="list the library")
    sub.add_parser("enrich", help="use the chat model to add artists, aliases and tags")
    args = p.parse_args(argv)

    lib = Library()
    if args.cmd == "import":
        added = lib.import_folder(Path(args.folder), copy=not args.in_place)
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
