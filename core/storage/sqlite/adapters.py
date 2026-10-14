"""Thin adapters that put the existing SQLite stores behind the storage Protocols.

Each adapter wraps one existing class and delegates to it. None of them changes the behaviour of
the class it wraps, so the gateway and the existing suites see the same stores as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.audit.sink import SqliteAuditSink
from core.gateway.lock import GatewayLock
from core.runner.ledger import AuditLedger
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from core.tiers.view import TierView


class SqliteTiers:
    """TierRead and TierWrite over one TierStore (TierView and OwnerControls underneath)."""

    def __init__(self, store: TierStore) -> None:
        self._store = store
        self._view = TierView(store)
        self._owner = OwnerControls(store)

    # TierRead
    def current(self) -> str:
        return self._view.current()

    def is_stopped(self) -> bool:
        return self._view.is_stopped()

    def pending_orders(self) -> list[str]:
        return self._view.pending_orders()

    def approval_status(self, order_id: str) -> str | None:
        return self._view.approval_status(order_id)

    def get_approval(self, order_id: str) -> dict[str, Any] | None:
        return self._view.get_approval(order_id)

    # TierWrite
    def set_tier(self, tier: str) -> None:
        self._owner.set_tier(tier)

    def engage_stop(self) -> None:
        self._owner.engage_stop()

    def release_stop(self) -> None:
        self._owner.release_stop()

    def request_approval(self, order_id: str) -> None:
        self._owner.request_approval(order_id)

    def approve(self, order_id: str) -> None:
        self._owner.approve(order_id)

    def reject(self, order_id: str) -> None:
        self._owner.reject(order_id)

    def close(self) -> None:
        self._store.close()


class SqliteAuditStore:
    """AuditStore over SqliteAuditSink."""

    def __init__(self, sink: SqliteAuditSink) -> None:
        self._sink = sink

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._sink.append(record)

    def find_by_order_id(self, order_id: str) -> dict[str, Any] | None:
        return self._sink.find_by_order_id(order_id)

    def list(
        self,
        after_seq: int | None = None,
        limit: int = 100,
        order_id: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        """Records in seq order, filtered; ``after_seq`` is exclusive."""
        out: list[dict[str, Any]] = []
        for rec in self._sink.records:
            if len(out) >= limit:
                break
            if after_seq is not None and int(rec["seq"]) <= after_seq:
                continue
            if order_id is not None and rec.get("order_id") != order_id:
                continue
            if outcome is not None and rec.get("outcome") != outcome:
                continue
            out.append(rec)
        return out

    def iter_all(self) -> Iterator[dict[str, Any]]:
        yield from self._sink.records

    def close(self) -> None:
        self._sink.close()


class SqliteLedgerQueries:
    """LedgerQueries over AuditLedger."""

    def __init__(self, ledger: AuditLedger) -> None:
        self._ledger = ledger

    def spent_usd_since(self, since: datetime) -> Decimal:
        return self._ledger.spent_usd_since(since)

    def executed_count_since(self, since: datetime) -> int:
        return self._ledger.executed_count_since(since)

    def close(self) -> None:
        self._ledger.close()


class SqliteGatewayLockProvider:
    """GatewayLockProvider over GatewayLock; ``timeout_s`` overrides the default per call."""

    def __init__(self, lock: GatewayLock) -> None:
        self._lock = lock

    def held(self, timeout_s: float | None = None) -> AbstractContextManager[None]:
        if timeout_s is None or self._lock.path == ":memory:":
            return self._lock.held()
        # A file lock keeps no state in the object, so a per-call instance is the same lock.
        return GatewayLock(Path(self._lock.path), acquire_timeout_s=timeout_s).held()
