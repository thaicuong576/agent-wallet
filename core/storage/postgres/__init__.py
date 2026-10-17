"""Postgres adapters behind the storage Protocols (design 5.2).

All SQL is a string literal with bound parameters (``tests/storage/test_pg_no_string_sql.py``).
Every tenant statement runs in a transaction bound to its owner (``PgPool.tenant``), so row-level
security applies on top of the ``owner = %s`` predicate in each query.
"""

from __future__ import annotations

from core.storage.postgres.audit import PgAuditStore
from core.storage.postgres.auth import PgAuthStore
from core.storage.postgres.backend import PgTenantStores, PostgresBackend
from core.storage.postgres.chat import PgChatStore
from core.storage.postgres.common import ActiveJobExists
from core.storage.postgres.intents import PgIntentStore
from core.storage.postgres.jobs import PgJobStore
from core.storage.postgres.keystore import PgKeystoreStore
from core.storage.postgres.ledger import PgLedgerQueries
from core.storage.postgres.locks import PgGatewayLockProvider
from core.storage.postgres.owners import PgOwnerRegistry
from core.storage.postgres.policy import PgPolicySelectionStore
from core.storage.postgres.pool import PgPool
from core.storage.postgres.settings import PgSettings
from core.storage.postgres.tiers import PgTiers
from core.storage.postgres.topups import PgTopupStore

__all__ = [
    "ActiveJobExists",
    "PgAuditStore",
    "PgAuthStore",
    "PgGatewayLockProvider",
    "PgIntentStore",
    "PgChatStore",
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
