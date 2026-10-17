"""PgChatStore: implements ChatStore protocol (core.storage.interfaces)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from core.storage.interfaces import (
    DEFAULT_THREAD,
    NEW_THREAD_TITLE,
    thread_status,
    thread_title,
)
from core.storage.postgres.common import _iso, _parse_ts, _utc
from core.storage.postgres.pool import PgPool


class PgChatStore:
    """Chat messages of one owner: role, text and a job id, never an order body."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        out = {
            "id": str(row[0]),
            "role": str(row[1]),
            "text": str(row[2]),
            "job_id": None if row[3] is None else str(row[3]),
            "created_at": _iso(row[4]),
        }
        if len(row) > 5 and row[5] is not None:
            out["intent"] = str(row[5])
        return out

    def append(
        self,
        role: str,
        text: str,
        job_id: str | None,
        created_at: datetime,
        thread_id: str | None = None,
        intent: str | None = None,
    ) -> dict[str, Any]:
        message_id = uuid.uuid4().hex
        thread = None if thread_id == DEFAULT_THREAD else thread_id
        with self._pool.tenant(self._owner) as cur:
            if thread is None and job_id is not None:
                # An agent message joins the thread of the user message of its job.
                cur.execute(
                    "SELECT thread_id FROM chat_messages WHERE owner = %s AND job_id = %s "
                    "AND role = 'user' ORDER BY seq LIMIT 1",
                    (self._owner, job_id),
                )
                row = cur.fetchone()
                thread = None if row is None else row[0]
            cur.execute(
                "INSERT INTO chat_messages "
                "(owner, id, role, text, job_id, created_at, thread_id, intent) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (self._owner, message_id, role, text, job_id, _utc(created_at), thread, intent),
            )
        out = {
            "id": message_id,
            "role": role,
            "text": text,
            "job_id": job_id,
            "created_at": _iso(created_at),
        }
        if intent is not None:
            out["intent"] = intent
        return out

    def list(self, limit: int = 50, thread_id: str | None = None) -> list[dict[str, Any]]:
        thread = None if thread_id in (None, DEFAULT_THREAD) else thread_id
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT id, role, text, job_id, created_at, intent FROM ("
                "SELECT id, role, text, job_id, created_at, seq, intent FROM chat_messages "
                "WHERE owner = %s AND thread_id IS NOT DISTINCT FROM %s "
                "ORDER BY seq DESC LIMIT %s) recent ORDER BY seq",
                (self._owner, thread, max(int(limit), 0)),
            )
            return [self._row(r) for r in cur.fetchall()]

    def get_thread_id(self, job_id: str) -> str | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT thread_id FROM chat_messages WHERE owner = %s AND job_id = %s "
                "AND role = 'user' ORDER BY seq LIMIT 1",
                (self._owner, job_id),
            )
            row = cur.fetchone()
            return None if row is None else row[0]

    def create_thread(self, created_at: datetime) -> dict[str, Any]:
        thread_id = uuid.uuid4().hex
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "INSERT INTO chat_threads (owner, id, title, created_at) VALUES (%s, %s, %s, %s)",
                (self._owner, thread_id, NEW_THREAD_TITLE, _utc(created_at)),
            )
        return {"id": thread_id, "title": NEW_THREAD_TITLE}

    def newest_empty_thread(self) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT id, title FROM chat_threads WHERE owner = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (self._owner,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            thread_id, title = str(row[0]), str(row[1])
            cur.execute(
                "SELECT 1 FROM chat_messages WHERE owner = %s AND thread_id = %s LIMIT 1",
                (self._owner, thread_id),
            )
            if cur.fetchone() is not None:
                return None
            return {"id": thread_id, "title": title}

    def thread_exists(self, thread_id: str) -> bool:
        if thread_id == DEFAULT_THREAD:
            return True
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT 1 FROM chat_threads WHERE owner = %s AND id = %s",
                (self._owner, thread_id),
            )
            return cur.fetchone() is not None

    def list_threads(self, limit: int = 30) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT t.id, t.title, COALESCE(l.created_at, t.created_at), "
                "j.status, (l.created_at IS NULL) FROM chat_threads t "
                "LEFT JOIN LATERAL (SELECT m.created_at, m.job_id FROM chat_messages m "
                "WHERE m.owner = t.owner AND m.thread_id = t.id "
                "ORDER BY m.seq DESC LIMIT 1) l ON true "
                "LEFT JOIN jobs j ON j.owner = t.owner AND j.job_id = l.job_id "
                "WHERE t.owner = %s",
                (self._owner,),
            )
            for row in cur.fetchall():
                raw_ts = row[2]
                items.append(
                    {
                        "id": str(row[0]),
                        "title": str(row[1]),
                        "last_message_at": _iso(raw_ts),
                        "status": thread_status(row[3]),
                        "_dt": _parse_ts(raw_ts),
                        "_empty": bool(row[4]),
                    }
                )
            cur.execute(
                "SELECT m.created_at, j.status, "
                "(SELECT f.text FROM chat_messages f WHERE f.owner = m.owner "
                "AND f.thread_id IS NULL AND f.role = 'user' ORDER BY f.seq LIMIT 1) "
                "FROM chat_messages m LEFT JOIN jobs j "
                "ON j.owner = m.owner AND j.job_id = m.job_id "
                "WHERE m.owner = %s AND m.thread_id IS NULL ORDER BY m.seq DESC LIMIT 1",
                (self._owner,),
            )
            row = cur.fetchone()
            if row is not None:
                raw_ts = row[0]
                items.append(
                    {
                        "id": DEFAULT_THREAD,
                        "title": thread_title(str(row[2])) if row[2] else NEW_THREAD_TITLE,
                        "last_message_at": _iso(raw_ts),
                        "status": thread_status(row[1]),
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
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "UPDATE chat_messages SET thread_id = %s WHERE owner = %s AND job_id = %s",
                (thread_id, self._owner, job_id),
            )
            cur.execute(
                "SELECT text FROM chat_messages WHERE owner = %s AND thread_id = %s "
                "AND role = 'user' ORDER BY seq LIMIT 1",
                (self._owner, thread_id),
            )
            first = cur.fetchone()
            if first is not None:
                cur.execute(
                    "UPDATE chat_threads SET title = %s WHERE owner = %s AND id = %s "
                    "AND title = %s",
                    (thread_title(str(first[0])), self._owner, thread_id, NEW_THREAD_TITLE),
                )
