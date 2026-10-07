"""Single-user demo memory, stored on disk and independent of an LLM session."""

import datetime as dt
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Memory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT,
                    role TEXT, payload TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, text TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, session_id TEXT,
                    message_id INTEGER UNIQUE, title TEXT, evidence TEXT, status TEXT, created TEXT);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def sessions(self):
        with self.connect() as con:
            return [
                dict(x)
                for x in con.execute("SELECT * FROM sessions ORDER BY created DESC LIMIT 100")
            ]

    def create_session(self):
        record = {"id": str(uuid.uuid4()), "title": "New conversation", "created": now()}
        with self.connect() as con:
            con.execute("INSERT INTO sessions VALUES (:id,:title,:created)", record)
        return record

    def messages(self, session_id):
        with self.connect() as con:
            if not con.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise KeyError("Conversation not found")
            records = [
                dict(x)
                for x in con.execute(
                    "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
                )
            ]
        return [{**r, "payload": json.loads(r["payload"])} for r in records]

    def append(self, session_id, role, payload):
        self.messages(session_id)
        with self.connect() as con:
            cursor = con.execute(
                "INSERT INTO messages(session_id,role,payload,created) VALUES (?,?,?,?)",
                (session_id, role, json.dumps(payload, default=str), now()),
            )
            if role == "user":
                con.execute(
                    "UPDATE sessions SET title=? WHERE id=? AND title='New conversation'",
                    (payload["message"][:70], session_id),
                )
            return cursor.lastrowid

    def memories(self):
        with self.connect() as con:
            return [dict(x) for x in con.execute("SELECT * FROM memories ORDER BY created DESC")]

    def remember(self, text):
        if len(self.memories()) >= 25:
            raise ValueError("Remove a saved preference before adding more (limit 25)")
        record = {"id": str(uuid.uuid4()), "text": text.strip(), "created": now()}
        with self.connect() as con:
            con.execute("INSERT INTO memories VALUES (:id,:text,:created)", record)
        return record

    def forget(self, memory_id):
        with self.connect() as con:
            con.execute("DELETE FROM memories WHERE id=?", (memory_id,))

    def delete_session(self, session_id):
        with self.connect() as con:
            con.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            con.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def reviews(self):
        with self.connect() as con:
            return [
                {**dict(r), "evidence": json.loads(r["evidence"])}
                for r in con.execute("SELECT * FROM reviews ORDER BY created DESC")
            ]

    def queue_review(self, session_id, message_id):
        messages = self.messages(session_id)
        match = next(
            (m for m in messages if m["id"] == message_id and m["role"] == "assistant"), None
        )
        if not match or not (
            match["payload"].get("result")
            or (match["payload"].get("retrieval") or {}).get("documents")
        ):
            raise ValueError("Only completed query or document evidence can be saved for review")
        record = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "message_id": message_id,
            "title": match["payload"].get("question", "Query review")[:160],
            "evidence": json.dumps(match["payload"]),
            "status": "pending",
            "created": now(),
        }
        with self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO reviews VALUES (:id,:session_id,:message_id,:title,:evidence,:status,:created)",
                record,
            )
        return self.reviews()

    def review(self, review_id, status):
        if status not in {"pending", "reviewed", "dismissed"}:
            raise ValueError("Invalid review status")
        with self.connect() as con:
            con.execute("UPDATE reviews SET status=? WHERE id=?", (status, review_id))
