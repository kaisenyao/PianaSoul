"""Her conversations, kept in the library's database: what you said, what she said, and the pieces she played.

A conversation is current while you keep talking: after QUIET_S without a word, the next request starts a new one
(see Assistant.ask). Any earlier conversation can be picked up again; she then knows it as it was.
"""

from __future__ import annotations

from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    pinned_at TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    at TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    recording_id INTEGER
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, id);
"""
ROLES = ("user", "assistant", "piece")  # a piece message is a piece she played; its text is its title then
QUIET_S = 30 * 60  # after this long without a word, the next request starts a new conversation


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seconds_since(stamp: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()


class Conversations:
    def __init__(self, lib):
        self.lib = lib
        with lib.lock:
            lib.db.executescript(SCHEMA)
        if not lib.setting("conversations_from_requests"):
            self._from_requests()
            lib.set_setting("conversations_from_requests", now())

    def _from_requests(self) -> None:
        """Before conversations were kept, the library logged each request that brought in a new piece: what was
        said, when, and the piece. Those become conversations (a new one after a quiet half hour), with what was
        said and the piece she played; her replies were not kept."""
        rows = self.lib.execute("SELECT r.asked_at, r.text, r.recording_id, rec.title FROM requests r "
                                "LEFT JOIN recordings rec ON rec.id = r.recording_id ORDER BY r.asked_at, r.id")
        with self.lib.lock:
            current, last = None, None
            for asked_at, text, rec_id, title in rows:
                at = datetime.fromisoformat(asked_at)
                if current is None or (at - last).total_seconds() > QUIET_S:
                    current = self.lib.db.execute("INSERT INTO conversations (started_at, updated_at) VALUES (?, ?)",
                                                  (asked_at, asked_at)).lastrowid
                last = at
                insert = "INSERT INTO messages (conversation_id, at, role, text, recording_id) VALUES (?, ?, ?, ?, ?)"
                if text:
                    self.lib.db.execute(insert, (current, asked_at, "user", text, None))
                if title:
                    self.lib.db.execute(insert, (current, asked_at, "piece", title, rec_id))
                self.lib.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (asked_at, current))
            self.lib.db.commit()

    def create(self) -> int:
        stamp = now()
        with self.lib.lock:
            cur = self.lib.db.execute("INSERT INTO conversations (started_at, updated_at) VALUES (?, ?)", (stamp, stamp))
            self.lib.db.commit()
            return cur.lastrowid

    def add(self, conversation_id: int, role: str, text: str = "", recording_id: int | None = None) -> dict:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        stamp = now()
        with self.lib.lock:
            cur = self.lib.db.execute(
                "INSERT INTO messages (conversation_id, at, role, text, recording_id) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, stamp, role, text, recording_id))
            self.lib.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (stamp, conversation_id))
            self.lib.db.commit()
            return {"id": cur.lastrowid, "conversation_id": conversation_id, "at": stamp, "role": role,
                    "text": text, "recording_id": recording_id}

    def get(self, conversation_id: int) -> dict | None:
        rows = self.lib.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        return self._summary(rows[0]) if rows else None

    def messages(self, conversation_id: int) -> list[dict]:
        return [dict(r) for r in self.lib.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,))]

    def all(self) -> list[dict]:
        """Every conversation with something in it, the latest first."""
        rows = self.lib.execute("SELECT * FROM conversations WHERE EXISTS "
                                "(SELECT 1 FROM messages WHERE conversation_id = conversations.id) "
                                "ORDER BY updated_at DESC, id DESC")
        return [self._summary(r) for r in rows]

    def latest(self) -> dict | None:
        found = self.all()
        return found[0] if found else None

    def played(self, conversation_id: int) -> list[int]:
        """The pieces she played in it, by their numbers in the library (never looked up again by name), in the order
        first played, each once; a piece that left the library is not among them (forget)."""
        rows = self.lib.execute("SELECT recording_id FROM messages WHERE conversation_id = ? AND role = 'piece' "
                                "AND recording_id IS NOT NULL ORDER BY id", (conversation_id,))
        return list(dict.fromkeys(r[0] for r in rows))

    def history(self, conversation_id: int) -> list[tuple[str, str]]:
        """The conversation as the chat model reads it again: (role, text), with each piece as a line of hers."""
        lines = []
        for m in self.messages(conversation_id):
            if m["role"] == "piece":
                lines.append(("assistant", f"[played: {m['text']}]"))
            elif m["text"]:
                lines.append((m["role"], m["text"]))
        return lines

    def search(self, query: str) -> list[dict]:
        """Conversations with query in something said (or a piece she played), or in their name, the latest first;
        each with the latest message that has it (None when only the name does)."""
        rows = self.lib.execute(
            "SELECT c.id AS conversation, m.id, m.role, m.text, m.at, m.recording_id FROM conversations c "
            "LEFT JOIN messages m ON m.id = (SELECT id FROM messages WHERE conversation_id = c.id "
            "AND instr(lower(text), :q) ORDER BY id DESC LIMIT 1) "
            "WHERE EXISTS (SELECT 1 FROM messages WHERE conversation_id = c.id) "
            "AND (m.id IS NOT NULL OR instr(lower(c.title), :q)) ORDER BY c.updated_at DESC, c.id DESC",
            {"q": query.lower()})
        return [{"id": r["conversation"], "message": {k: r[k] for k in ("id", "role", "text", "at", "recording_id")}
                 if r["id"] is not None else None} for r in rows]

    def rename(self, conversation_id: int, title: str) -> None:
        self.lib.execute("UPDATE conversations SET title = ? WHERE id = ?", (title.strip(), conversation_id))

    def pin(self, conversation_id: int, on: bool) -> None:
        self.lib.execute("UPDATE conversations SET pinned_at = ? WHERE id = ?", (now() if on else None, conversation_id))

    def forget(self, recording_id: int) -> None:
        """A piece left the library: where she played it, it is gone (its number may be given to a new piece)."""
        self.lib.execute("UPDATE messages SET recording_id = NULL WHERE recording_id = ?", (recording_id,))

    def delete(self, conversation_id: int) -> None:
        with self.lib.lock:
            self.lib.db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            self.lib.db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            self.lib.db.commit()

    def _summary(self, row) -> dict:
        """A conversation for the list: what its title is made of when it has none of its own."""
        c = dict(row)
        firsts = self.lib.execute("SELECT text FROM messages WHERE conversation_id = ? AND role = 'user' "
                                  "ORDER BY id LIMIT 1", (c["id"],))
        played = self.lib.execute("SELECT text FROM messages WHERE conversation_id = ? AND role = 'piece' "
                                  "ORDER BY id", (c["id"],))
        c["first"] = firsts[0][0] if firsts else ""
        c["pieces"] = list(dict.fromkeys(r[0] for r in played))[:3]
        return c
