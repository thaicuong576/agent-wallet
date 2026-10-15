"""SQLite chat store of one owner: ``chat_messages`` in the owner's ``webapp.sqlite``.

Text and ids only. A step shown under an agent message is rebuilt from the audit log by the job
id, so no order body, key or model transcript is stored here.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SqliteChat:
    """ChatStore over one SQLite file. A connection per call, no shared state."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS chat_messages(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK (role IN ('user', 'agent')),
            text TEXT NOT NULL, job_id TEXT, created_at TEXT NOT NULL);
    """

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        with self._connect() as db:
            db.executescript(self._SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=30, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            yield db

    def append(
        self, role: str, text: str, job_id: str | None, created_at: datetime
    ) -> dict[str, Any]:
        message_id = uuid.uuid4().hex
        stamp = _ts(created_at)
        with self._connect() as db:
            db.execute(
                "INSERT INTO chat_messages(id, role, text, job_id, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (message_id, role, text, job_id, stamp),
            )
        return {
            "id": message_id,
            "role": role,
            "text": text,
            "job_id": job_id,
            "created_at": stamp,
        }

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, role, text, job_id, created_at FROM ("
                " SELECT * FROM chat_messages ORDER BY seq DESC LIMIT ?) ORDER BY seq",
                (max(int(limit), 0),),
            ).fetchall()
        return [dict(r) for r in rows]
