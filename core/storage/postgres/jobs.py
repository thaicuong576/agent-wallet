"""PgJobStore: implements JobStore protocol (core.storage.interfaces)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import psycopg

from core.storage.interfaces import RateLimited
from core.storage.postgres.common import ActiveJobExists, _iso, _now, _parse_ts, _utc
from core.storage.postgres.pool import PgPool

_JOB_FIELDS = (
    "kind",
    "status",
    "planner_client",
    "order_ids",
    "seqs",
    "error_code",
    "started_at",
    "finished_at",
)
_JOB_TIME_FIELDS = ("started_at", "finished_at")


class PgJobStore:
    """Run jobs; at most one queued or running job per owner (partial unique index)."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    def _active_job_id(self) -> str:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT job_id FROM jobs WHERE owner = %s AND status IN ('queued', 'running') "
                "LIMIT 1",
                (self._owner,),
            )
            row = cur.fetchone()
        return "" if row is None else str(row[0])

    def create(
        self,
        job_id: str,
        kind: str,
        created_at: datetime,
        *,
        limit: int | None = None,
        window: timedelta = timedelta(hours=1),
        count_kinds: Sequence[str] | None = None,
        inquiry_limit: int | None = None,
    ) -> None:
        """Insert a queued job. Raises ActiveJobExists (a JobInProgress), or RateLimited."""
        created = _utc(created_at)
        try:
            with self._pool.tenant(self._owner) as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("aw:jobs:" + self._owner,),
                )
                cur.execute(
                    "SELECT job_id FROM jobs WHERE owner = %s AND status IN ('queued', 'running') "
                    "LIMIT 1",
                    (self._owner,),
                )
                active = cur.fetchone()
                if active is not None:
                    raise ActiveJobExists(str(active[0]))
                if inquiry_limit is not None:
                    cur.execute(
                        "SELECT created_at FROM jobs WHERE owner = %s AND kind = 'chat_inquiry' "
                        "AND created_at > %s ORDER BY created_at",
                        (self._owner, created - window),
                    )
                    rows_inq = cur.fetchall()
                    if len(rows_inq) >= inquiry_limit:
                        oldest = _utc(rows_inq[len(rows_inq) - inquiry_limit][0])
                        wait = int((oldest + window - created).total_seconds()) + 1
                        raise RateLimited(wait)
                if limit is not None:
                    cur.execute(
                        "SELECT created_at FROM jobs WHERE owner = %s AND kind = ANY(%s) "
                        "AND created_at > %s ORDER BY created_at",
                        (self._owner, list(count_kinds or (kind,)), created - window),
                    )
                    rows = cur.fetchall()
                    if len(rows) >= limit:
                        oldest = _utc(rows[len(rows) - limit][0])
                        wait = int((oldest + window - created).total_seconds()) + 1
                        raise RateLimited(wait)
                cur.execute(
                    "INSERT INTO jobs (owner, job_id, kind, status, created_at) "
                    "VALUES (%s, %s, %s, 'queued', %s)",
                    (self._owner, job_id, kind, created),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise ActiveJobExists(self._active_job_id()) from exc

    def update(self, job_id: str, **fields: Any) -> None:
        unknown = set(fields) - set(_JOB_FIELDS)
        if unknown:
            raise ValueError(f"Unknown job fields: {sorted(unknown)}")
        values: dict[str, Any] = {"owner": self._owner, "job_id": job_id}
        for name in _JOB_FIELDS:
            value = fields.get(name)
            if name == "order_ids" and name in fields:
                ids = list(value or [])
                if not all(isinstance(i, str) for i in ids):
                    raise ValueError("order_ids holds order id strings only")
                value = json.dumps(ids)
            if name == "seqs" and name in fields:
                numbers = list(value or [])
                if not all(isinstance(n, int) and not isinstance(n, bool) for n in numbers):
                    raise ValueError("seqs holds audit sequence numbers only")
                value = json.dumps(numbers)
            if name in _JOB_TIME_FIELDS and value is not None:
                value = _utc(value) if isinstance(value, datetime) else _parse_ts(str(value))
            values["set_" + name] = name in fields
            values[name] = value
        try:
            with self._pool.tenant(self._owner) as cur:
                cur.execute(
                    "UPDATE jobs SET "
                    "kind = CASE WHEN %(set_kind)s THEN %(kind)s ELSE kind END, "
                    "status = CASE WHEN %(set_status)s THEN %(status)s ELSE status END, "
                    "planner_client = CASE WHEN %(set_planner_client)s "
                    "THEN %(planner_client)s ELSE planner_client END, "
                    "order_ids = CASE WHEN %(set_order_ids)s "
                    "THEN %(order_ids)s::jsonb ELSE order_ids END, "
                    "seqs = CASE WHEN %(set_seqs)s "
                    "THEN %(seqs)s::jsonb ELSE seqs END, "
                    "error_code = CASE WHEN %(set_error_code)s "
                    "THEN %(error_code)s ELSE error_code END, "
                    "started_at = CASE WHEN %(set_started_at)s "
                    "THEN %(started_at)s::timestamptz ELSE started_at END, "
                    "finished_at = CASE WHEN %(set_finished_at)s "
                    "THEN %(finished_at)s::timestamptz ELSE finished_at END "
                    "WHERE owner = %(owner)s AND job_id = %(job_id)s",
                    values,
                )
                if cur.rowcount == 0:
                    raise KeyError(job_id)
        except psycopg.errors.UniqueViolation as exc:
            raise ActiveJobExists(self._active_job_id()) from exc

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "job_id": str(row[0]),
            "kind": str(row[1]),
            "status": str(row[2]),
            "planner_client": row[3] or "none",
            "order_ids": list(row[4]),
            "error_code": row[5],
            "created_at": _iso(row[6]),
            "started_at": _iso(row[7]),
            "finished_at": _iso(row[8]),
            "seqs": list(row[9]),
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT job_id, kind, status, planner_client, order_ids, error_code, created_at, "
                "started_at, finished_at, seqs FROM jobs WHERE owner = %s AND job_id = %s",
                (self._owner, job_id),
            )
            row = cur.fetchone()
        return None if row is None else self._row(row)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT job_id, kind, status, planner_client, order_ids, error_code, created_at, "
                "started_at, finished_at, seqs FROM jobs WHERE owner = %s "
                "ORDER BY created_at DESC, job_id DESC LIMIT %s",
                (self._owner, max(int(limit), 0)),
            )
            return [self._row(r) for r in cur.fetchall()]

    def mark_interrupted(self) -> int:
        """Mark this owner's queued and running jobs interrupted (startup recovery)."""
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "UPDATE jobs SET status = 'interrupted', finished_at = %s "
                "WHERE owner = %s AND status IN ('queued', 'running')",
                (_now(), self._owner),
            )
            return int(cur.rowcount)
