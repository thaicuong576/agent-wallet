"""Storage Protocols (design 5.1). No import of sqlite3 or psycopg lives here.

The gateway, runner and API see only these Protocols. The existing split is kept: a read protocol
for the gateway and agent side, a write protocol that only OwnerControls receives. Adapters
(SQLite in ticket P1, Postgres in P2) must not change the behaviour of the classes they wrap.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


class StorageUnavailable(Exception):
    """A backend cannot be reached. The gateway fails closed: nothing is signed (503)."""


class JobInProgress(Exception):
    """The owner already has a queued or running job (HTTP 409)."""

    def __init__(self, job_id: str) -> None:
        super().__init__(job_id)
        self.job_id = job_id


class RateLimited(Exception):
    """The hourly limit for a job kind is used up (HTTP 429)."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(retry_after_s)
        self.retry_after_s = retry_after_s


@runtime_checkable
class TierRead(Protocol):
    """Read side of tiers and approvals (replaces core.tiers.view.TierView)."""

    def current(self) -> str: ...

    def is_stopped(self) -> bool: ...

    def pending_orders(self) -> list[str]: ...

    def approval_status(self, order_id: str) -> str | None: ...

    def get_approval(self, order_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class TierWrite(Protocol):
    """Write side of tiers and approvals (replaces core.tiers.owner.OwnerControls)."""

    def set_tier(self, tier: str) -> None: ...

    def engage_stop(self) -> None: ...

    def release_stop(self) -> None: ...

    def request_approval(self, order_id: str) -> None: ...

    def approve(self, order_id: str) -> None: ...

    def reject(self, order_id: str) -> None: ...


@runtime_checkable
class AuditStore(Protocol):
    """Append-only hash chain, per owner (replaces SqliteAuditSink)."""

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Assign seq, prev_hash and hash; return the stored record."""
        ...

    def find_by_order_id(self, order_id: str) -> dict[str, Any] | None: ...

    def list(
        self,
        after_seq: int | None = None,
        limit: int = 100,
        order_id: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def iter_all(self) -> Iterator[dict[str, Any]]: ...


@runtime_checkable
class IntentStore(Protocol):
    """Insert-only signing intents, written before the signer is called."""

    def begin_signing(self, order_id: str, body_hash: str, now: str) -> None: ...

    def unfinished(self, order_id: str) -> bool: ...

    def get_intent(self, order_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class LedgerQueries(Protocol):
    """Spend and count queries over executed records (replaces AuditLedger)."""

    def spent_usd_since(self, since: datetime) -> Decimal: ...

    def executed_count_since(self, since: datetime) -> int: ...


@runtime_checkable
class GatewayLockProvider(Protocol):
    """One holder at a time per owner; raises GatewayBusy on timeout, before any read."""

    def held(self, timeout_s: float | None = None) -> AbstractContextManager[None]: ...


@runtime_checkable
class KeystoreStore(Protocol):
    """Encrypted keystore JSON per owner (file or row). Never a plaintext key."""

    def put(self, owner: str, blob: str, *, overwrite: bool = False) -> None:
        """Store the encrypted JSON. Raises FileExistsError if present and not overwrite."""
        ...

    def get(self, owner: str) -> str | None: ...

    def exists(self, owner: str) -> bool: ...

    def owners(self) -> Iterable[str]:
        """Every owner that has a keystore (used by rotation)."""
        ...


@runtime_checkable
class OwnerRegistry(Protocol):
    def create(self, owner: str, agent_address: str) -> None: ...

    def get(self, owner: str) -> dict[str, Any] | None: ...

    def count_created_since(self, since: datetime) -> int: ...


@runtime_checkable
class AuthStore(Protocol):
    """Sign-in nonces, sessions and step-up challenges (shared between tenants)."""

    def put_nonce(self, address: str, nonce: str, expires_at: datetime) -> None: ...

    def consume_nonce(self, address: str, nonce: str, now: datetime) -> bool: ...

    def put_session(self, jti: str, address: str, expires_at: datetime) -> None: ...

    def get_session(self, jti: str, now: datetime) -> dict[str, Any] | None: ...

    def revoke(self, jti: str) -> None: ...

    def put_stepup(
        self, challenge_id: str, payload: Mapping[str, Any], expires_at: datetime
    ) -> None: ...

    def consume_stepup(self, challenge_id: str, now: datetime) -> dict[str, Any] | None: ...

    def purge_expired(self, now: datetime) -> int: ...


@runtime_checkable
class JobStore(Protocol):
    """Run jobs; at most one active job per owner."""

    def create(
        self,
        job_id: str,
        kind: str,
        created_at: datetime,
        *,
        limit: int | None = None,
        window: timedelta = timedelta(hours=1),
        count_kinds: Sequence[str] | None = None,
    ) -> None:
        """Insert a queued job atomically.

        Raises JobInProgress when the owner already has an active job, and RateLimited when
        ``limit`` jobs were created within ``window`` before ``created_at``. The jobs counted are
        those of ``count_kinds`` (default: only ``kind``), so chat and run share one limit.
        """
        ...

    def update(self, job_id: str, **fields: Any) -> None: ...

    def get(self, job_id: str) -> dict[str, Any] | None: ...

    def list(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def mark_interrupted(self) -> int: ...


@runtime_checkable
class ChatStore(Protocol):
    """Chat messages of one owner. Text and ids only: a step is rebuilt from the audit log by
    the job id, so no order body, key or model transcript is stored here."""

    def append(
        self, role: str, text: str, job_id: str | None, created_at: datetime
    ) -> dict[str, Any]:
        """Store one message (``role`` is ``user`` or ``agent``) and return it with its id."""
        ...

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """The most recent ``limit`` messages, oldest first."""
        ...


@runtime_checkable
class TopupStore(Protocol):
    """Top-up rows of one owner. A transaction hash is unknown to every other owner."""

    def get(self, tx_hash: str) -> dict[str, Any] | None: ...

    def put(self, tx_hash: str, amount_wei: int, now: datetime) -> dict[str, Any]:
        """Insert a pending row unless the hash is known; return the stored row."""
        ...

    def settle(self, tx_hash: str, status: str, block_number: int | None) -> None:
        """Move a pending row to ``confirmed`` or ``failed``; a settled row never changes."""
        ...


@runtime_checkable
class PolicySelectionStore(Protocol):
    def append(self, preset_id: str, ips_hash: str, signature_hash: str) -> None: ...

    def latest(self) -> dict[str, Any] | None: ...


@runtime_checkable
class SettingsStore(Protocol):
    def global_stop(self) -> bool:
        """True when the operator stop is set. Fail closed: an error reads as True."""
        ...


@runtime_checkable
class TenantStores(Protocol):
    """Everything one owner's gateway, runner and API routes need."""

    tier_read: TierRead
    tier_write: TierWrite
    audit: AuditStore
    intents: IntentStore
    ledger: LedgerQueries
    lock: GatewayLockProvider
    keystore: KeystoreStore
    jobs: JobStore
    policy_selection: PolicySelectionStore
    topups: TopupStore
    chat: ChatStore


@runtime_checkable
class StorageBackend(Protocol):
    def open_tenant(self, owner: str) -> TenantStores: ...

    def auth(self) -> AuthStore: ...

    def settings(self) -> SettingsStore: ...

    def keystore_admin(self) -> KeystoreStore:
        """Keystore handle not bound to one owner: ``exists`` creates nothing, rotation uses it."""
        ...

    def mark_jobs_interrupted(self) -> int:
        """Startup recovery: queued and running jobs of every owner become ``interrupted``."""
        ...

    def migrate(self) -> None: ...

    def close(self) -> None: ...
