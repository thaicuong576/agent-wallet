"""Audit ledger reading spend and count metrics from audit_records."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def _format_since(since: datetime) -> str:
    """Format datetime as UTC ISO-8601 string matching audit log timestamps."""
    if since.tzinfo is None:
        since_utc = since.replace(tzinfo=UTC)
    else:
        since_utc = since.astimezone(UTC)
    return since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditLedger:
    """Read-only audit log reader for persistent limits (spend and tx count)."""

    def __init__(
        self,
        audit_db: Path | str,
        price_fn: Callable[[str], Decimal] | None = None,
    ) -> None:
        self.audit_db = str(audit_db)
        self.price_fn = price_fn or self._default_price_usd
        if self.audit_db == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            path = Path(self.audit_db)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(str(path)) as init_conn:
                    init_conn.execute("PRAGMA journal_mode = WAL;")
            uri = f"file:{path.resolve().as_posix()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)

    @staticmethod
    def _default_price_usd(token: str) -> Decimal:
        try:
            from core.runner.prices import price_usd

            return price_usd(token)
        except Exception:
            if token == "USDC":
                return Decimal("1.00")
            raise KeyError(f"Price unavailable for token {token}") from None

    def spent_usd_since(self, since: datetime) -> Decimal:
        """Sum USD amount of executed transactions since timestamp, excluding approve."""
        since_iso = _format_since(since)
        try:
            cursor = self._conn.execute(
                "SELECT payload FROM audit_records WHERE outcome = 'executed' AND created_at > ?;",
                (since_iso,),
            )
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            return Decimal("0.00")

        total_usd = Decimal("0")
        for (payload_str,) in rows:
            rec = json.loads(payload_str)
            order = rec.get("order")
            if not isinstance(order, dict):
                continue
            function = order.get("function")
            if function == "approve":
                continue
            amount_obj = order.get("amount")
            if not isinstance(amount_obj, dict):
                continue
            value_raw = amount_obj.get("value")
            if value_raw is None:
                continue
            value = Decimal(str(value_raw))
            decimals = int(amount_obj.get("decimals", 0))
            token = str(order.get("token", "USDC"))
            units = value / (Decimal(10) ** decimals)
            price = self.price_fn(token)
            total_usd += units * price

        return total_usd

    def executed_count_since(self, since: datetime) -> int:
        """Count executed transactions since timestamp (all functions included)."""
        since_iso = _format_since(since)
        try:
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM audit_records WHERE outcome = 'executed' AND created_at > ?;",
                (since_iso,),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except sqlite3.OperationalError:
            return 0

    def close(self) -> None:
        """Close SQLite database connection."""
        self._conn.close()

    def __enter__(self) -> AuditLedger:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
