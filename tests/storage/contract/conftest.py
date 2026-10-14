"""Backend fixture of the storage contract suite (design 5.3).

Every test here runs on ``sqlite`` and on ``postgres``. The ``postgres`` parameter is skipped
unless ``TEST_DATABASE_URL`` is set (CI service container, local compose).
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.gateway.intents import IntentStore
from core.storage.sqlite import SqliteTenantStores, open_tenant
from tests.storage.test_pg_support import SKIP_REASON, TEST_DATABASE_URL, PgContractBackend

OWNER_A = "0x" + "a" * 40
OWNER_B = "0x" + "b" * 40
PRICES = {"USDC": Decimal("1.00"), "WETH": Decimal("2000.00")}


def _price(token: str) -> Decimal:
    return PRICES[token]


class SqliteBackend:
    """Opens per-owner stores in a temporary DATA_DIR and can reach the raw audit tables."""

    name = "sqlite"

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._open: list[SqliteTenantStores] = []

    def open(self, owner: str, *, lock_timeout_s: float = 15.0) -> SqliteTenantStores:
        stores = open_tenant(
            self.data_dir,
            owner,
            intents=IntentStore,
            lock_timeout_s=lock_timeout_s,
            price_fn=_price,
        )
        self._open.append(stores)
        return stores

    def raw_audit_sql(self, stores: SqliteTenantStores, sql: str) -> None:
        """Run a raw statement against the owner's audit tables as an out-of-band client."""
        conn = sqlite3.connect(str(stores.path / "audit.sqlite"))
        try:
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    # Exception raised by a raw UPDATE or DELETE against an append-only table.
    append_only_error: type[Exception] = sqlite3.DatabaseError

    def close(self) -> None:
        for stores in self._open:
            try:
                stores.close()
            except Exception:
                pass


def _postgres_backend(_data_dir: Path) -> Any:
    if not TEST_DATABASE_URL:
        pytest.skip(SKIP_REASON)
    return PgContractBackend()


BACKENDS: dict[str, Callable[[Path], Any]] = {
    "sqlite": SqliteBackend,
    "postgres": _postgres_backend,
}


@pytest.fixture(params=sorted(BACKENDS))
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    be = BACKENDS[request.param](tmp_path / "data")
    try:
        yield be
    finally:
        be.close()


@pytest.fixture
def stores(backend: Any) -> Any:
    return backend.open(OWNER_A)


def make_record(
    outcome: str = "executed",
    *,
    function: str = "supply",
    value: str = "50000000",
    decimals: int = 6,
    token: str = "USDC",
    created_at: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """A minimal audit record in the shape the ledger reads."""
    oid = order_id or str(uuid.uuid4())
    created = created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema_version": 1,
        "seq": 0,
        "order_id": oid,
        "order": {
            "order_id": oid,
            "function": function,
            "token": token,
            "amount": {"value": value, "decimals": decimals},
        },
        "outcome": outcome,
        "created_at": created,
    }


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
