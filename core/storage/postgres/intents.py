"""PgIntentStore: implements IntentStore protocol (core.storage.interfaces)."""

from __future__ import annotations

from typing import Any

from core.storage.postgres.common import _iso, _parse_ts
from core.storage.postgres.pool import PgPool


class PgIntentStore:
    """Insert-only signing intents, written in their own transaction before the signer runs."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    def begin_signing(self, order_id: str, body_hash: str, now: str) -> None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "INSERT INTO order_intents (owner, order_id, body_hash, state, created_at) "
                "VALUES (%s, %s, %s, 'signing', %s)",
                (self._owner, order_id, body_hash, _parse_ts(now)),
            )

    def get_intent(self, order_id: str) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT order_id, body_hash, state, created_at FROM order_intents "
                "WHERE owner = %s AND order_id = %s",
                (self._owner, order_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "order_id": str(row[0]),
            "body_hash": str(row[1]),
            "state": str(row[2]),
            "created_at": _iso(row[3]),
        }

    def unfinished(self, order_id: str) -> bool:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM order_intents WHERE owner = %(o)s "
                "AND order_id = %(id)s) AND NOT EXISTS (SELECT 1 FROM audit_records "
                "WHERE owner = %(o)s AND order_id = %(id)s "
                "AND outcome IN ('executed', 'failed'))",
                {"o": self._owner, "id": order_id},
            )
            row = cur.fetchone()
        return bool(row and row[0])
