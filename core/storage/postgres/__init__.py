"""Postgres adapters behind the storage Protocols (design 5.2, ticket P2)."""

from __future__ import annotations

from core.storage.postgres.adapters import (
    ActiveJobExists,
    PgAuditStore,
    PgAuthStore,
    PgGatewayLockProvider,
    PgIntentStore,
    PgJobStore,
    PgKeystoreStore,
    PgLedgerQueries,
    PgOwnerRegistry,
    PgPolicySelectionStore,
    PgSettings,
    PgTiers,
    PgTopupStore,
)
from core.storage.postgres.backend import PgTenantStores, PostgresBackend
from core.storage.postgres.pool import PgPool

__all__ = [
    "ActiveJobExists",
    "PgAuditStore",
    "PgAuthStore",
    "PgGatewayLockProvider",
    "PgIntentStore",
    "PgJobStore",
    "PgKeystoreStore",
    "PgLedgerQueries",
    "PgOwnerRegistry",
    "PgPolicySelectionStore",
    "PgPool",
    "PgSettings",
    "PgTenantStores",
    "PgTiers",
    "PgTopupStore",
    "PostgresBackend",
]
