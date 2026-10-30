"""Bring a library from where a checkout kept it (output/: library.db, MIDI Files/ and .cache/) to where it lives
now: the library folder, the data folder and the cache folder (library.py).

    pianasoul migrate --from ~/Documents/PianaSoul/output

Everything is copied; the old folder stays as it was, a full backup, with a note in it saying where its contents
went. Done once on its own too (Library), when a checkout that has an old library first runs with the real
places.

The old library is put in order on a copy first: a piece whose file is not in the old MIDI Files/ (gone, or where
the library was before it was moved or copied) is matched to the file of that name there, if its notes and length
are the same; pieces that then share a file become one, which keeps everything any of them had (names, favorite,
plays, video, and where she played them). A piece whose file is still
nowhere stays in the library out of sight, as any piece whose file is gone does (folder.py), and is listed.

The pieces PianaSoul made (from a video or a recording) go to Transcribed/, the others to the library folder itself.
Everything is made ready in a temporary folder and only then put in place, the library last; if anything fails,
what was put in place is taken out again, so there is never half a new library.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .library import CACHE_DIR, DATA_DIR, MIDI_TYPES, MUSIC_DIR, TRANSCRIBED, fingerprint, midi_stats, stamp, upgrade

MADE = {"url", "youtube", "local"}  # source types of the pieces PianaSoul made: they go to Transcribed/
DATA_ITEMS = ("phone-token", "phone-tls", "models", "voice")  # in the old .cache/, but lasting: the data folder
CACHE_ITEMS = ("downloads", "extras")  # in the old .cache/, and may go: the cache folder
KEPT = ("favorited_at", "last_played_at", "video_title", "aliases", "tags", "composer", "performer", "source_url")
NOTE = "MOVED TO THE NEW PLACES.txt"


class MigrationError(Exception):
    """The library cannot be moved: why, in a sentence."""


@dataclass
class Report:
    pieces: int = 0
    merged: int = 0
    missing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # data already in the new places, left as it was

    def summary(self) -> str:
        lines = [f"Moved the library: {self.pieces} piece(s), {self.merged} duplicate record(s) merged."]
        if self.missing:
            lines.append(f"{len(self.missing)} piece(s) whose file was not found are kept, out of sight:")
            lines += [f"  {title}" for title in self.missing]
        lines += [f"Already there, left as it was: {name}" for name in self.skipped]
        return "\n".join(lines)


def migrate(old: Path, music: Path = MUSIC_DIR, data: Path = DATA_DIR, cache: Path = CACHE_DIR) -> Report:
    old, music, data, cache = (Path(p).expanduser().resolve() for p in (old, music, data, cache))
    if not (old / "library.db").is_file():
        raise MigrationError(f"there is no library.db in {old}")
    if (data / "library.db").exists():
        raise MigrationError(f"{data / 'library.db'} already exists: the library has been moved, or started anew. "
                             "Nothing was changed.")
    report = Report()
    with tempfile.TemporaryDirectory(prefix="pianasoul-migrate-") as tmp:
        stage = Path(tmp)
        db = sqlite3.connect(stage / "library.db")
        try:
            db.row_factory = sqlite3.Row
            source = sqlite3.connect(old / "library.db")
            source.backup(db)
            source.close()
            upgrade(db)
            report.merged = _merge(db, _recover(db, old / "MIDI Files"))
            moves = _place_files(db, old, music, stage / "music", report)
            moves += _place_items(old / ".cache", DATA_ITEMS, data, stage / "data", report)
            moves += _place_items(old / ".cache", CACHE_ITEMS, cache, stage / "cache", report)
            db.commit()
        finally:
            db.close()
        _put_in_place(moves, stage / "library.db", data, report)
    (old / NOTE).write_text(
        f"On {datetime.now():%Y-%m-%d %H:%M} PianaSoul copied this library to its new places:\n\n"
        f"  the music     {music}\n  the library   {data / 'library.db'}\n  its data      {data}\n"
        f"  work files    {cache}\n\nNothing here was changed. Once PianaSoul shows all your pieces, you may delete "
        f"this folder.\n", encoding="utf-8")
    return report


def _recover(db: sqlite3.Connection, folder: Path) -> dict[int, str]:
    """Where each piece's file is: a piece whose file is not in the old MIDI Files/ takes the file of the same name
    there, when its notes and length are the piece's (its file is gone, or it is where the library was before it was
    moved or copied). By id."""
    folder, found = folder.resolve(), {}
    for rec in db.execute("SELECT id, midi_path, notes, duration_s FROM recordings").fetchall():
        found[rec["id"]] = rec["midi_path"]
        candidate = folder / Path(rec["midi_path"]).name
        if Path(rec["midi_path"]).parent == folder or not candidate.is_file() or rec["notes"] is None:
            continue
        try:
            stats = midi_stats(candidate)
        except Exception:
            continue
        if stats["notes"] == rec["notes"] and abs((stats["duration_s"] or 0) - (rec["duration_s"] or 0)) <= 0.05:
            found[rec["id"]] = str(candidate)
    return found


def _merge(db: sqlite3.Connection, paths: dict[int, str]) -> int:
    """Pieces that share one file become one: the one with the most about it, with what the others had besides; each
    piece then points at its file. Returns how many records went."""
    rows = [dict(r) for r in db.execute("SELECT * FROM recordings ORDER BY id")]
    groups: dict[str, list[dict]] = {}
    for rec in rows:
        rec["midi_path"] = paths[rec["id"]]
        groups.setdefault(rec["midi_path"], []).append(rec)
    merged = 0
    for path, recs in groups.items():
        if len(recs) < 2:
            db.execute("UPDATE recordings SET midi_path = ? WHERE id = ?", (path, recs[0]["id"]))
            continue

        def richness(rec):
            mentions = db.execute("SELECT (SELECT COUNT(*) FROM messages WHERE recording_id = :id) + "
                                  "(SELECT COUNT(*) FROM requests WHERE recording_id = :id)", rec).fetchone()[0]
            named = rec["title"] != Path(rec["midi_path"]).stem
            return sum(bool(rec[k]) for k in KEPT) + named + mentions, -rec["id"]
        keep, *others = sorted(recs, key=richness, reverse=True)
        fields = {k: keep[k] or next((o[k] for o in others if o[k]), keep[k]) for k in KEPT}
        fields["favorited_at"] = min((r["favorited_at"] for r in recs if r["favorited_at"]), default=None)
        fields["last_played_at"] = max((r["last_played_at"] for r in recs if r["last_played_at"]), default=None)
        for k in ("channel", "views", "published", "thumbnail", "channel_avatar"):  # the video's, with its title
            fields[k] = keep[k] if keep["video_title"] else next((o[k] for o in others if o["video_title"]), keep[k])
        if keep["source_type"] not in MADE:
            fields["source_type"] = next((o["source_type"] for o in others if o["source_type"] in MADE),
                                         keep["source_type"])
        for other in others:
            for table in ("messages", "requests"):
                db.execute(f"UPDATE {table} SET recording_id = ? WHERE recording_id = ?", (keep["id"], other["id"]))
            db.execute("DELETE FROM recordings WHERE id = ?", (other["id"],))
            merged += 1
        fields["midi_path"] = path
        assignments = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE recordings SET {assignments} WHERE id = ?", (*fields.values(), keep["id"]))
    return merged


def _place_files(db: sqlite3.Connection, old: Path, music: Path, stage: Path, report: Report) -> list:
    """Copy each piece's file into the stage, where it will go in the library folder, and point the piece at where it
    will be. Files in the old MIDI Files/ that no piece had go in as yours. Returns (staged, final) pairs."""
    moves, taken = [], set()

    def spot(folder: Path, name: str) -> Path:
        stem, suffix, n = Path(name).stem, Path(name).suffix, 2
        dest = folder / name
        while dest in taken or dest.exists():
            dest, n = folder / f"{stem} ({n}){suffix}", n + 1
        taken.add(dest)
        return dest

    def copy(src: Path, final: Path) -> None:
        staged = stage / final.relative_to(music)
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, staged)
        moves.append((staged, final))

    had = set()
    for rec in db.execute("SELECT id, title, source_type, midi_path FROM recordings ORDER BY id").fetchall():
        src = Path(rec["midi_path"])
        if not src.is_file():
            report.missing.append(rec["title"])
            continue
        had.add(src.resolve())
        final = spot(music / TRANSCRIBED if rec["source_type"] in MADE else music, src.name)
        copy(src, final)
        db.execute("UPDATE recordings SET midi_path = ?, fingerprint = ? WHERE id = ?",
                   (str(final), fingerprint(src), rec["id"]))
        report.pieces += 1
    for src in sorted((old / "MIDI Files").glob("*")):
        if src.suffix.lower() in MIDI_TYPES and src.resolve() not in had and not src.name.startswith("."):
            copy(src, spot(music, src.name))
    return moves


def _place_items(cache: Path, names: tuple, dest: Path, stage: Path, report: Report) -> list:
    """Copy the old cache's items that belong in dest into the stage; one already in dest is left as it is."""
    moves = []
    for name in names:
        src = cache / name
        if not src.exists():
            continue
        if (dest / name).exists():
            report.skipped.append(str(dest / name))
            continue
        stage.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, stage / name)
        moves.append((stage / name, dest / name))
    return moves


def _put_in_place(moves: list, library: Path, data: Path, report: Report) -> None:
    """Move everything made ready into place, the library last; on any failure take back what was moved, and the
    folders made for it."""
    made, placed = [], []

    def folder(path: Path) -> None:
        missing = [p for p in [path, *path.parents] if not p.exists()]
        path.mkdir(parents=True, exist_ok=True)
        made.extend(reversed(missing))  # the outermost first, so they are taken back innermost first

    try:
        for staged, final in moves:
            folder(final.parent)
            shutil.move(staged, final)
            placed.append(final)
        con = sqlite3.connect(library)  # the files' stamps where they are now, before the library goes in
        for rec_id, path in con.execute("SELECT id, midi_path FROM recordings").fetchall():
            if Path(path).is_file():
                con.execute("UPDATE recordings SET stamp = ? WHERE id = ?", (stamp(Path(path)), rec_id))
        con.commit()
        con.close()
        folder(data)
        shutil.copy2(library, data / ".library.db.part")
        os.replace(data / ".library.db.part", data / "library.db")
    except BaseException as exc:
        (data / ".library.db.part").unlink(missing_ok=True)
        for final in reversed(placed):
            shutil.rmtree(final) if final.is_dir() else final.unlink(missing_ok=True)
        for path in reversed(made):
            try:
                path.rmdir()
            except OSError:
                pass
        if isinstance(exc, Exception):
            raise MigrationError(f"{exc}. Nothing was left in the new places; the old library is as it was.") from exc
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pianasoul migrate",
                                description="Copy a library from a checkout's output/ folder to the new places.")
    p.add_argument("--from", dest="old", required=True, metavar="OUTPUT", help="the old output folder")
    args = p.parse_args(argv)
    print(f"Copying {args.old} to:\n  music  {MUSIC_DIR}\n  data   {DATA_DIR}\n  cache  {CACHE_DIR}", flush=True)
    try:
        print(migrate(Path(args.old)).summary())
    except MigrationError as exc:
        print(f"Cannot move the library: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
