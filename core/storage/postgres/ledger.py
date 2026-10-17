"""PgLedgerQueries: implements LedgerQueries protocol (core.storage.interfaces)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from core.storage.postgres.common import _NON_SPEND_FUNCTIONS, _utc
from core.storage.postgres.pool import PgPool


def _default_price_usd(token: str) -> Decimal:
    try:
        from core.runner.prices import price_usd

        return price_usd(token)
    except Exception:
        if token == "USDC":
            return Decimal("1.00")
        raise KeyError(f"Price unavailable for token {token}") from None


class PgLedgerQueries:
    """Spend and count queries in SQL over the derived audit columns."""

    def __init__(
        self, pool: PgPool, owner: str, price_fn: Callable[[str], Decimal] | None = None
    ) -> None:
        self._pool = pool
        self._owner = owner
        self._price_fn = price_fn or _default_price_usd

    def spent_usd_since(self, since: datetime) -> Decimal:
        """Executed spend since ``since`` (exclusive); approve, mint and transfer are not spend."""
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT token, decimals, sum(amount_value) FROM audit_records "
                "WHERE owner = %s AND outcome = 'executed' AND created_at > %s "
                "AND amount_value IS NOT NULL AND function IS NOT NULL "
                "AND NOT (function = ANY(%s)) GROUP BY token, decimals",
                (self._owner, _utc(since), list(_NON_SPEND_FUNCTIONS)),
            )
            rows = cur.fetchall()
        total = Decimal("0")
        for token, decimals, value in rows:
            total += Decimal(value) / (Decimal(10) ** int(decimals)) * self._price_fn(str(token))
        return total

    def executed_count_since(self, since: datetime) -> int:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT count(*) FROM audit_records "
                "WHERE owner = %s AND outcome = 'executed' AND created_at > %s",
                (self._owner, _utc(since)),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0
