"""PgOwnerRegistry: implements OwnerRegistry protocol (core.storage.interfaces)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg

from core.storage.postgres.common import _now, _utc, validate_owner
from core.storage.postgres.pool import PgPool


class PgOwnerRegistry:
    """Registered owners (global table)."""

    def __init__(self, pool: PgPool) -> None:
        self._pool = pool

    def create(self, owner: str, agent_address: str) -> None:
        validate_owner(owner)
        try:
            with self._pool.cursor() as cur:
                cur.execute(
                    "INSERT INTO owners (address, agent_address, created_at) VALUES (%s, %s, %s)",
                    (owner, agent_address, _now()),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f"Owner already registered: {owner}") from exc

    def get(self, owner: str) -> dict[str, Any] | None:
        validate_owner(owner)
        with self._pool.cursor() as cur:
            cur.execute(
                "SELECT address, agent_address, created_at FROM owners WHERE address = %s",
                (owner,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {"address": str(row[0]), "agent_address": str(row[1]), "created_at": row[2]}

    def count_created_since(self, since: datetime) -> int:
        with self._pool.cursor() as cur:
            cur.execute("SELECT count(*) FROM owners WHERE created_at > %s", (_utc(since),))
            row = cur.fetchone()
        return int(row[0]) if row else 0
