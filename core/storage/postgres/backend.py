"""PostgresBackend: the StorageBackend of the hosted and compose deployments (design 5.2)."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from core.storage.migrate import apply_migrations, check_schema
from core.storage.postgres.audit import PgAuditStore
from core.storage.postgres.auth import PgAuthStore
from core.storage.postgres.chat import PgChatStore
from core.storage.postgres.common import validate_owner
from core.storage.postgres.intents import PgIntentStore
from core.storage.postgres.jobs import PgJobStore
from core.storage.postgres.keystore import PgKeystoreStore
from core.storage.postgres.ledger import PgLedgerQueries
from core.storage.postgres.locks import PgGatewayLockProvider
from core.storage.postgres.owners import PgOwnerRegistry
from core.storage.postgres.policy import PgPolicySelectionStore
from core.storage.postgres.pool import DEFAULT_POOL_SIZE, PgPool
from core.storage.postgres.settings import PgSettings
from core.storage.postgres.tiers import PgTiers
from core.storage.postgres.topups import PgTopupStore

_TRUTHY = {"1", "true", "yes", "on"}


def _env_global_stop() -> bool:
    return os.environ.get("AGENT_WALLET_GLOBAL_STOP", "").strip().lower() in _TRUTHY


@dataclass
class PgTenantStores:
    """Everything one owner's gateway, runner and API routes need (TenantStores)."""

    owner: str
    tier_read: PgTiers
    tier_write: PgTiers
    audit: PgAuditStore
    intents: PgIntentStore
    ledger: PgLedgerQueries
    lock: PgGatewayLockProvider
    keystore: PgKeystoreStore
    jobs: PgJobStore
    policy_selection: PgPolicySelectionStore
    topups: PgTopupStore
    chat: PgChatStore

    def close(self) -> None:
        """Connections belong to the backend pool; a tenant handle holds none."""


class PostgresBackend:
    """One pool, one set of tables. ``dsn`` is the application role's URL (the pooler URL)."""

    def __init__(
        self,
        dsn: str,
        *,
        migrate_dsn: str | None = None,
        pool_size: int = DEFAULT_POOL_SIZE,
        lock_timeout_s: float = 15.0,
        price_fn: Callable[[str], Decimal] | None = None,
    ) -> None:
        self._dsn = dsn
        self._migrate_dsn = migrate_dsn or os.environ.get("DATABASE_MIGRATE_URL") or dsn
        self._pool = PgPool(dsn, max_size=pool_size)
        self._lock_timeout_s = lock_timeout_s
        self._price_fn = price_fn

    def open_tenant(self, owner: str, *, lock_timeout_s: float | None = None) -> PgTenantStores:
        validate_owner(owner)
        tiers = PgTiers(self._pool, owner)
        timeout = self._lock_timeout_s if lock_timeout_s is None else lock_timeout_s
        return PgTenantStores(
            owner=owner,
            tier_read=tiers,
            tier_write=tiers,
            audit=PgAuditStore(self._pool, owner),
            intents=PgIntentStore(self._pool, owner),
            ledger=PgLedgerQueries(self._pool, owner, self._price_fn),
            lock=PgGatewayLockProvider(self._pool, owner, timeout),
            keystore=PgKeystoreStore(self._pool, owner),
            jobs=PgJobStore(self._pool, owner),
            policy_selection=PgPolicySelectionStore(self._pool, owner),
            topups=PgTopupStore(self._pool, owner),
            chat=PgChatStore(self._pool, owner),
        )

    def auth(self) -> PgAuthStore:
        return PgAuthStore(self._pool)

    def settings(self) -> PgSettings:
        return PgSettings(self._pool, _env_global_stop)

    def owners(self) -> PgOwnerRegistry:
        return PgOwnerRegistry(self._pool)

    def keystore_admin(self) -> PgKeystoreStore:
        """Unbound keystore handle for secret rotation (reads every owner's encrypted row)."""
        return PgKeystoreStore(self._pool, None)

    def mark_jobs_interrupted(self) -> int:
        """Startup recovery: every owner's queued or running job becomes interrupted.

        Row-level security binds one owner per transaction, so the owners are visited one by one.
        """
        total = 0
        for owner in self.keystore_admin().owners():
            total += self.open_tenant(str(owner)).jobs.mark_interrupted()
        return total

    def migrate(self) -> None:
        """Apply pending migrations with the DDL-capable URL, then verify the schema."""
        apply_migrations(self._migrate_dsn)
        check_schema(self._dsn)

    def verify_schema(self) -> int:
        """Fail closed (MigrationError) when the schema is behind or newer than the code."""
        return check_schema(self._dsn)

    def close(self) -> None:
        self._pool.close()
