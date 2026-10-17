"""PgAuditStore: implements AuditStore protocol (core.storage.interfaces)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from core.gateway.audit import canonical, record_hash
from core.storage.postgres.common import GENESIS_PREV_HASH, _now, _parse_ts
from core.storage.postgres.pool import PgPool


def _derived_columns(rec: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """function, token, amount_value and decimals as the ledger reads them from the payload."""
    order = rec.get("order")
    if not isinstance(order, dict):
        return None, None, None, None
    function = order.get("function")
    amount = order.get("amount")
    if not isinstance(amount, dict) or amount.get("value") is None:
        return function, None, None, None
    try:
        value = Decimal(str(amount["value"]))
        decimals = int(amount.get("decimals", 0))
    except (InvalidOperation, ValueError, TypeError):
        return function, None, None, None
    return function, str(order.get("token", "USDC")), value, decimals


class PgAuditStore:
    """AuditStore: one hash chain per owner, appended under a per-owner advisory lock."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        rec = json.loads(json.dumps(record))  # deep copy; also proves the record is JSON
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("aw:audit:" + self._owner,),
            )
            cur.execute(
                "SELECT seq, hash FROM audit_records WHERE owner = %s ORDER BY seq DESC LIMIT 1",
                (self._owner,),
            )
            last = cur.fetchone()
            seq = 0 if last is None else int(last[0]) + 1
            prev_hash = GENESIS_PREV_HASH if last is None else str(last[1])
            rec["seq"] = seq
            rec["prev_hash"] = prev_hash
            rec["hash"] = record_hash(rec)
            payload = canonical(rec).decode("utf-8")
            created_raw = rec.get("created_at")
            created_at = _parse_ts(created_raw) if created_raw else _now()
            function, token, amount_value, decimals = _derived_columns(rec)
            cur.execute(
                "INSERT INTO audit_records (owner, seq, order_id, outcome, function, token, "
                "amount_value, decimals, created_at, prev_hash, hash, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    self._owner,
                    seq,
                    rec.get("order_id"),
                    str(rec.get("outcome") or ""),
                    function,
                    token,
                    amount_value,
                    decimals,
                    created_at,
                    prev_hash,
                    rec["hash"],
                    payload,
                ),
            )
        return rec

    def find_by_order_id(self, order_id: str) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT payload FROM audit_records WHERE owner = %s AND order_id = %s "
                "ORDER BY seq DESC LIMIT 1",
                (self._owner, order_id),
            )
            row = cur.fetchone()
        return None if row is None else json.loads(row[0])

    def list(
        self,
        after_seq: int | None = None,
        limit: int = 100,
        order_id: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        """Records in seq order, filtered; ``after_seq`` is exclusive."""
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT payload FROM audit_records WHERE owner = %(owner)s "
                "AND (%(after)s::bigint IS NULL OR seq > %(after)s::bigint) "
                "AND (%(order_id)s::text IS NULL OR order_id = %(order_id)s::text) "
                "AND (%(outcome)s::text IS NULL OR outcome = %(outcome)s::text) "
                "ORDER BY seq ASC LIMIT %(limit)s",
                {
                    "owner": self._owner,
                    "after": after_seq,
                    "order_id": order_id,
                    "outcome": outcome,
                    "limit": max(int(limit), 0),
                },
            )
            return [json.loads(r[0]) for r in cur.fetchall()]

    def iter_all(self) -> Iterator[dict[str, Any]]:
        after: int | None = None
        while True:
            page = self.list(after_seq=after, limit=500)
            if not page:
                return
            yield from page
            after = int(page[-1]["seq"])
