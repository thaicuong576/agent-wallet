"""Per-owner SQLite layout: DATA_DIR/owners/<0x lowercase address>/*.sqlite."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.audit.sink import SqliteAuditSink
from core.gateway.lock import GatewayLock
from core.runner.ledger import AuditLedger
from core.storage.interfaces import IntentStore
from core.storage.sqlite.adapters import (
    SqliteAuditStore,
    SqliteGatewayLockProvider,
    SqliteLedgerQueries,
    SqliteTiers,
)
from core.tiers.store import TierStore

_OWNER_RE = re.compile(r"^0x[0-9a-f]{40}$")


def tenant_dir(data_dir: Path | str, owner: str) -> Path:
    """Directory of one owner. The address must already be lowercase and well formed."""
    if not _OWNER_RE.match(owner):
        raise ValueError(f"Invalid owner address: {owner!r}")
    root = (Path(data_dir) / "owners").resolve()
    path = (root / owner).resolve()
    if path.parent != root:
        raise ValueError("Owner path escapes the owners directory")
    return path


@dataclass
class SqliteTenantStores:
    """The per-owner stores of the SQLite backend that exist today (design 5.1)."""

    owner: str
    path: Path
    tier_read: SqliteTiers
    tier_write: SqliteTiers
    audit: SqliteAuditStore
    intents: IntentStore
    ledger: SqliteLedgerQueries
    lock: SqliteGatewayLockProvider
    # Webapp stores: set by SqliteBackend, left None by the bare open_tenant helper.
    keystore: Any = None
    jobs: Any = None
    policy_selection: Any = None
    topups: Any = None
    chat: Any = None

    def close(self) -> None:
        self.tier_read.close()
        self.audit.close()
        close = getattr(self.intents, "close", None)
        if callable(close):
            close()
        self.ledger.close()


def open_tenant(
    data_dir: Path | str,
    owner: str,
    *,
    intents: Callable[[Path], IntentStore],
    lock_timeout_s: float = 15.0,
    price_fn: Callable[[str], Decimal] | None = None,
) -> SqliteTenantStores:
    """Open (creating if needed) the stores of one owner.

    ``intents`` builds the intent store from the owner's audit database path. Only core.gateway
    may import the intents module, so the caller inside the gateway package supplies the factory
    (``core.gateway.intents.IntentStore`` itself already satisfies the Protocol).
    """
    path = tenant_dir(data_dir, owner)
    audit_db = path / "audit.sqlite"
    tiers = SqliteTiers(TierStore(path / "state.sqlite"))
    return SqliteTenantStores(
        owner=owner,
        path=path,
        tier_read=tiers,
        tier_write=tiers,
        audit=SqliteAuditStore(SqliteAuditSink(audit_db)),
        intents=intents(audit_db),
        ledger=SqliteLedgerQueries(AuditLedger(audit_db, price_fn=price_fn)),
        lock=SqliteGatewayLockProvider(
            GatewayLock(path / "gateway.lock.sqlite", acquire_timeout_s=lock_timeout_s)
        ),
    )
