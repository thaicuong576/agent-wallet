"""SQLite chat store of one owner: ``chat_messages`` and ``chat_threads`` in the owner's
``webapp.sqlite``.

Text and ids only. A step shown under an agent message is rebuilt from the audit log by the job
id, so no order body, key or model transcript is stored here. A message with a NULL ``thread_id``
belongs to the implicit default thread.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.storage.interfaces import (
    DEFAULT_THREAD,
    NEW_THREAD_TITLE,
    thread_status,
    thread_title,
)


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_iso(value: datetime | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return ""
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return s
    dt_utc = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    s = str(value or "").strip()
    if not s:
        return datetime.min.replace(tzinfo=UTC)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class SqliteChat:
    """ChatStore over one SQLite file. A connection per call, no shared state."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS chat_messages(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK (role IN ('user', 'agent')),
            text TEXT NOT NULL, job_id TEXT, created_at TEXT NOT NULL, thread_id TEXT);
        CREATE TABLE IF NOT EXISTS chat_threads(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL);
    """

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        with self._connect() as db:
            db.executescript(self._SCHEMA)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(chat_messages)")}
            if "thread_id" not in columns:  # a file created before ticket 123
                db.execute("ALTER TABLE chat_messages ADD COLUMN thread_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS chat_messages_thread_seq"
                " ON chat_messages(thread_id, seq)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS chat_messages_job ON chat_messages(job_id)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=30, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            yield db

    def append(
        self,
        role: str,
        text: str,
        job_id: str | None,
        created_at: datetime,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        message_id = uuid.uuid4().hex
        stamp = _ts(created_at)
        thread = None if thread_id == DEFAULT_THREAD else thread_id
        with self._connect() as db:
            if thread is None and job_id is not None:
                # An agent message joins the thread of the user message of its job.
                row = db.execute(
                    "SELECT thread_id FROM chat_messages WHERE job_id = ? AND role = 'user'"
                    " ORDER BY seq LIMIT 1",
                    (job_id,),
                ).fetchone()
                thread = None if row is None else row["thread_id"]
            db.execute(
                "INSERT INTO chat_messages(id, role, text, job_id, created_at, thread_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, role, text, job_id, stamp, thread),
            )
        return {
            "id": message_id,
            "role": role,
            "text": text,
            "job_id": job_id,
            "created_at": stamp,
        }

    def list(self, limit: int = 50, thread_id: str | None = None) -> list[dict[str, Any]]:
        thread = None if thread_id in (None, DEFAULT_THREAD) else thread_id
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, role, text, job_id, created_at FROM ("
                " SELECT * FROM chat_messages WHERE thread_id IS ? ORDER BY seq DESC LIMIT ?)"
                " ORDER BY seq",
                (thread, max(int(limit), 0)),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_thread(self, created_at: datetime) -> dict[str, Any]:
        thread_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO chat_threads(id, title, created_at) VALUES (?, ?, ?)",
                (thread_id, NEW_THREAD_TITLE, _ts(created_at)),
            )
        return {"id": thread_id, "title": NEW_THREAD_TITLE}

    def newest_empty_thread(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id, title FROM chat_threads ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            has_messages = db.execute(
                "SELECT 1 FROM chat_messages WHERE thread_id = ? LIMIT 1",
                (row["id"],),
            ).fetchone()
            if has_messages is not None:
                return None
            return {"id": row["id"], "title": row["title"]}

    def thread_exists(self, thread_id: str) -> bool:
        if thread_id == DEFAULT_THREAD:
            return True
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM chat_threads WHERE id = ?", (thread_id,)).fetchone()
        return row is not None

    def list_threads(self, limit: int = 30) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        with self._connect() as db:
            # `jobs` lives in the same file; it may not exist yet when only chat was opened.
            has_jobs = (
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
                ).fetchone()
                is not None
            )
            status_col = "j.status" if has_jobs else "NULL"
            join = "LEFT JOIN jobs j ON j.job_id = m.job_id" if has_jobs else ""
            last_sql = (
                f"SELECT m.created_at, {status_col} AS job_status FROM chat_messages m {join}"
                " WHERE m.thread_id IS ? ORDER BY m.seq DESC LIMIT 1"
            )
            for head in db.execute(
                "SELECT id, title, created_at FROM chat_threads ORDER BY rowid DESC"
            ).fetchall():
                last = db.execute(last_sql, (head["id"],)).fetchone()
                raw_ts = last["created_at"] if last else head["created_at"]
                items.append(
                    {
                        "id": head["id"],
                        "title": head["title"],
                        "last_message_at": _normalize_iso(raw_ts),
                        "status": thread_status(last["job_status"] if last else None),
                        "_dt": _parse_ts(raw_ts),
                        "_empty": last is None,
                    }
                )
            last = db.execute(last_sql, (None,)).fetchone()
            if last is not None:
                first = db.execute(
                    "SELECT text FROM chat_messages WHERE thread_id IS NULL AND role = 'user'"
                    " ORDER BY seq LIMIT 1"
                ).fetchone()
                raw_ts = last["created_at"]
                items.append(
                    {
                        "id": DEFAULT_THREAD,
                        "title": thread_title(first["text"]) if first else NEW_THREAD_TITLE,
                        "last_message_at": _normalize_iso(raw_ts),
                        "status": thread_status(last["job_status"]),
                        "_dt": _parse_ts(raw_ts),
                        "_empty": False,
                    }
                )
        items.sort(
            key=lambda t: (
                t["_dt"],
                1 if t["_empty"] else 0,
                0 if t["id"] == DEFAULT_THREAD else 1,
                t["id"],
            ),
            reverse=True,
        )
        for t in items:
            t.pop("_dt", None)
            t.pop("_empty", None)
        return items[: max(int(limit), 0)]

    def attach_job(self, thread_id: str | None, job_id: str) -> None:
        if thread_id is None or thread_id == DEFAULT_THREAD:
            return
        with self._connect() as db:
            db.execute(
                "UPDATE chat_messages SET thread_id = ? WHERE job_id = ?", (thread_id, job_id)
            )
            first = db.execute(
                "SELECT text FROM chat_messages WHERE thread_id = ? AND role = 'user'"
                " ORDER BY seq LIMIT 1",
                (thread_id,),
            ).fetchone()
            if first is not None:
                db.execute(
                    "UPDATE chat_threads SET title = ? WHERE id = ? AND title = ?",
                    (thread_title(first["text"]), thread_id, NEW_THREAD_TITLE),
                )
