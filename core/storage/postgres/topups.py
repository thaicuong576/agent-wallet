"""PgTopupStore: implements TopupStore protocol (core.storage.interfaces)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.storage.postgres.common import _iso, _utc
from core.storage.postgres.pool import PgPool


class PgTopupStore:
    """Top-up rows of one owner. A foreign transaction hash is simply unknown (row-level security
    and the ``owner`` predicate)."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "tx_hash": str(row[0]),
            "amount_wei": str(row[1]),
            "status": str(row[2]),
            "block_number": None if row[3] is None else int(row[3]),
            "created_at": _iso(row[4]),
        }

    def get(self, tx_hash: str) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT tx_hash, amount_wei, status, block_number, created_at "
                "FROM topups WHERE owner = %s AND tx_hash = %s",
                (self._owner, tx_hash),
            )
            row = cur.fetchone()
        return None if row is None else self._row(row)

    def put(self, tx_hash: str, amount_wei: int, now: datetime) -> dict[str, Any]:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "INSERT INTO topups (owner, tx_hash, amount_wei, status, created_at) "
                "VALUES (%s, %s, %s, 'pending', %s) ON CONFLICT (owner, tx_hash) DO NOTHING",
                (self._owner, tx_hash, str(int(amount_wei)), _utc(now)),
            )
        stored = self.get(tx_hash)
        if stored is None:
            raise KeyError(tx_hash)
        return stored

    def settle(self, tx_hash: str, status: str, block_number: int | None) -> None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "UPDATE topups SET status = %s, block_number = %s "
                "WHERE owner = %s AND tx_hash = %s AND status = 'pending'",
                (status, block_number, self._owner, tx_hash),
            )
