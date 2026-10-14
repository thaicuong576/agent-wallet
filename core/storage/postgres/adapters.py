"""Postgres adapters for the storage Protocols (design 5.1 and 5.2).

All SQL is a string literal with bound parameters (``tests/storage/test_pg_no_string_sql.py``).
Every tenant statement runs in a transaction bound to its owner (``PgPool.tenant``), so row-level
security applies on top of the ``owner = %s`` predicate in each query.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from core.gateway.audit import canonical, record_hash
from core.gateway.exceptions import GatewayBusy
from core.storage.postgres.pool import PgPool
from core.tiers.errors import (
    EmergencyStopActiveError,
    InvalidTierError,
    OrderAlreadyDecidedError,
    OrderAlreadyExistsError,
    OrderNotFoundError,
)

GENESIS_PREV_HASH = "0" * 64
ALLOWED_TIERS = frozenset({"T0", "T1", "T2"})
LOCK_POLL_INTERVAL_S = 0.1
_OWNER_RE = re.compile(r"^0x[0-9a-f]{40}$")
_NON_SPEND_FUNCTIONS = ("approve", "mint", "transfer")


class ActiveJobExists(ValueError):
    """The owner already has a queued or running job."""


def validate_owner(owner: str) -> str:
    """The owner address must already be lowercase and well formed (design 5)."""
    if not isinstance(owner, str) or not _OWNER_RE.match(owner):
        raise ValueError(f"Invalid owner address: {owner!r}")
    return owner


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_ts(value: str) -> datetime:
    """RFC 3339 text (``...Z`` accepted) to an aware UTC datetime."""
    return _utc(datetime.fromisoformat(value))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------------------------
class PgTiers:
    """TierRead and TierWrite over tier_state and approval_queue."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    # TierRead
    def _state(self) -> tuple[str, bool]:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT tier, emergency_stop FROM tier_state WHERE owner = %s", (self._owner,)
            )
            row = cur.fetchone()
        if row is None:
            return "T0", False
        return str(row[0]), bool(row[1])

    def current(self) -> str:
        return self._state()[0]

    def is_stopped(self) -> bool:
        return self._state()[1]

    def pending_orders(self) -> list[str]:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT order_id FROM approval_queue WHERE owner = %s AND status = 'pending' "
                "ORDER BY requested_at ASC, order_id ASC",
                (self._owner,),
            )
            return [str(r[0]) for r in cur.fetchall()]

    def get_approval(self, order_id: str) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT order_id, status, requested_at, decided_at, decided_by "
                "FROM approval_queue WHERE owner = %s AND order_id = %s",
                (self._owner, order_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "order_id": str(row[0]),
            "status": str(row[1]),
            "requested_at": _iso(row[2]),
            "decided_at": _iso(row[3]),
            "decided_by": row[4],
        }

    def approval_status(self, order_id: str) -> str | None:
        record = self.get_approval(order_id)
        return None if record is None else str(record["status"])

    # TierWrite
    def set_tier(self, tier: str) -> None:
        if tier not in ALLOWED_TIERS:
            raise InvalidTierError(
                f"Invalid tier: '{tier}'. Allowed tiers are {sorted(ALLOWED_TIERS)}."
            )
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "INSERT INTO tier_state (owner, tier, emergency_stop, updated_at, changed_by) "
                "VALUES (%s, %s, false, %s, 'owner') "
                "ON CONFLICT (owner) DO UPDATE SET tier = EXCLUDED.tier, "
                "updated_at = EXCLUDED.updated_at",
                (self._owner, tier, _now()),
            )

    def _set_stop(self, stopped: bool) -> None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "INSERT INTO tier_state (owner, tier, emergency_stop, updated_at, changed_by) "
                "VALUES (%s, 'T0', %s, %s, 'owner') "
                "ON CONFLICT (owner) DO UPDATE SET emergency_stop = EXCLUDED.emergency_stop, "
                "updated_at = EXCLUDED.updated_at",
                (self._owner, stopped, _now()),
            )

    def engage_stop(self) -> None:
        self._set_stop(True)

    def release_stop(self) -> None:
        self._set_stop(False)

    def request_approval(self, order_id: str) -> None:
        if not order_id or not isinstance(order_id, str):
            raise ValueError("order_id must be a non-empty string.")
        try:
            with self._pool.tenant(self._owner) as cur:
                cur.execute(
                    "INSERT INTO approval_queue (owner, order_id, status, requested_at) "
                    "VALUES (%s, %s, 'pending', %s)",
                    (self._owner, order_id, _now()),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise OrderAlreadyExistsError(
                f"Order '{order_id}' already exists in the approval queue."
            ) from exc

    def _resolve(self, order_id: str, new_status: str) -> None:
        with self._pool.tenant(self._owner) as cur:
            if new_status == "approved":
                cur.execute(
                    "SELECT emergency_stop FROM tier_state WHERE owner = %s", (self._owner,)
                )
                state = cur.fetchone()
                if state is not None and bool(state[0]):
                    raise EmergencyStopActiveError(
                        "Cannot approve order while emergency stop is active."
                    )
            cur.execute(
                "UPDATE approval_queue SET status = %s, decided_at = %s, decided_by = 'owner' "
                "WHERE owner = %s AND order_id = %s AND status = 'pending'",
                (new_status, _now(), self._owner, order_id),
            )
            if cur.rowcount == 1:
                return
            cur.execute(
                "SELECT status FROM approval_queue WHERE owner = %s AND order_id = %s",
                (self._owner, order_id),
            )
            row = cur.fetchone()
        if row is None:
            raise OrderNotFoundError(f"Order '{order_id}' not found in approval queue.")
        raise OrderAlreadyDecidedError(
            f"Order '{order_id}' has already been decided with status '{row[0]}'."
        )

    def approve(self, order_id: str) -> None:
        self._resolve(order_id, "approved")

    def reject(self, order_id: str) -> None:
        self._resolve(order_id, "rejected")


# ---------------------------------------------------------------------------------------------
# Audit chain
# ---------------------------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------------------------
class PgIntentStore:
    """Insert-only signing intents, written in their own transaction before the signer runs."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    def begin_signing(self, order_id: str, body_hash: str, now: str) -> None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "INSERT INTO order_intents (owner, order_id, body_hash, state, created_at) "
                "VALUES (%s, %s, %s, 'signing', %s)",
                (self._owner, order_id, body_hash, _parse_ts(now)),
            )

    def unfinished(self, order_id: str) -> bool:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM order_intents WHERE owner = %(o)s "
                "AND order_id = %(id)s) AND NOT EXISTS (SELECT 1 FROM audit_records "
                "WHERE owner = %(o)s AND order_id = %(id)s "
                "AND outcome IN ('executed', 'failed'))",
                {"o": self._owner, "id": order_id},
            )
            row = cur.fetchone()
        return bool(row and row[0])


# ---------------------------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------------------------
# Gateway lock
# ---------------------------------------------------------------------------------------------
class PgGatewayLockProvider:
    """One holder per owner: a transaction-scoped advisory lock polled every 100 ms.

    A dedicated connection (outside the pool) holds the transaction for as long as the lock is
    held; commit, rollback or a dropped connection releases it. Timeout raises GatewayBusy before
    the body runs; an unreachable database raises StorageUnavailable.
    """

    def __init__(self, pool: PgPool, owner: str, default_timeout_s: float = 15.0) -> None:
        self._pool = pool
        self._owner = owner
        self._default_timeout_s = float(default_timeout_s)

    def held(self, timeout_s: float | None = None) -> AbstractContextManager[None]:
        timeout = self._default_timeout_s if timeout_s is None else float(timeout_s)
        return self._held(timeout)

    @contextmanager
    def _held(self, timeout: float) -> Iterator[None]:
        conn = self._pool.connect()
        try:
            deadline = time.monotonic() + timeout
            while True:
                row = conn.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("aw:gateway:" + self._owner,),
                ).fetchone()
                if row is not None and row[0]:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GatewayBusy(f"Gateway lock timeout ({timeout}s) for owner {self._owner}")
                time.sleep(min(LOCK_POLL_INTERVAL_S, remaining))
            yield
        finally:
            try:
                conn.rollback()
            except psycopg.Error:
                pass
            conn.close()


# ---------------------------------------------------------------------------------------------
# Keystore, owners, policy selection
# ---------------------------------------------------------------------------------------------
class PgKeystoreStore:
    """Encrypted keystore JSON in a row. Bound to one owner, or unbound for rotation."""

    def __init__(self, pool: PgPool, bound_owner: str | None = None) -> None:
        self._pool = pool
        self._bound = bound_owner

    def _scope(self, owner: str) -> str:
        validate_owner(owner)
        if self._bound is not None and owner != self._bound:
            raise ValueError("This keystore handle is bound to a different owner")
        return owner

    @contextmanager
    def _cursor(self, owner: str | None) -> Iterator[Any]:
        # Rotation (unbound) sees every keystore through the '*' setting the policy admits.
        with self._pool.tenant(owner if owner is not None else "*") as cur:
            yield cur

    def put(self, owner: str, blob: str, *, overwrite: bool = False) -> None:
        self._scope(owner)
        if not isinstance(blob, str) or not blob:
            raise ValueError("keystore blob must be a non-empty string")
        kdf = "unknown"
        try:
            parsed = json.loads(blob)
            crypto = parsed.get("crypto") or parsed.get("Crypto") or {}
            kdf = str(crypto.get("kdf") or parsed.get("kdf") or "unknown")
        except (ValueError, AttributeError):
            pass
        with self._cursor(owner) as cur:
            if overwrite:
                cur.execute(
                    "INSERT INTO keystores (owner, keystore, kdf, created_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (owner) DO UPDATE "
                    "SET keystore = EXCLUDED.keystore, kdf = EXCLUDED.kdf",
                    (owner, blob, kdf, _now()),
                )
                return
            cur.execute(
                "INSERT INTO keystores (owner, keystore, kdf, created_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (owner) DO NOTHING",
                (owner, blob, kdf, _now()),
            )
            if cur.rowcount == 0:
                raise FileExistsError(f"A keystore already exists for {owner}")

    def get(self, owner: str) -> str | None:
        self._scope(owner)
        with self._cursor(owner) as cur:
            cur.execute("SELECT keystore FROM keystores WHERE owner = %s", (owner,))
            row = cur.fetchone()
        return None if row is None else str(row[0])

    def exists(self, owner: str) -> bool:
        return self.get(owner) is not None

    def owners(self) -> Iterable[str]:
        with self._cursor(self._bound) as cur:
            if self._bound is None:
                cur.execute("SELECT owner FROM keystores ORDER BY owner")
            else:
                cur.execute("SELECT owner FROM keystores WHERE owner = %s", (self._bound,))
            return [str(r[0]) for r in cur.fetchall()]


class PgOwnerRegistry:
    """Registered owners (global table)."""

    def __init__(self, pool: PgPool) -> None:
        self._pool = pool

    def create(self, owner: str, agent_address: str) -> None:
        validate_owner(owner)
        try:
            with self._pool.cursor() as cur:
                cur.execute(
                    "INSERT INTO owners (address, agent_address, created_at) VALUES (%s, %s, %s)",
                    (owner, agent_address, _now()),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f"Owner already registered: {owner}") from exc

    def get(self, owner: str) -> dict[str, Any] | None:
        validate_owner(owner)
        with self._pool.cursor() as cur:
            cur.execute(
                "SELECT address, agent_address, created_at FROM owners WHERE address = %s",
                (owner,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {"address": str(row[0]), "agent_address": str(row[1]), "created_at": row[2]}

    def count_created_since(self, since: datetime) -> int:
        with self._pool.cursor() as cur:
            cur.execute("SELECT count(*) FROM owners WHERE created_at > %s", (_utc(since),))
            row = cur.fetchone()
        return int(row[0]) if row else 0


class PgPolicySelectionStore:
    """Append-only record of the preset an owner selected, per owner."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    def append(self, preset_id: str, ips_hash: str, signature_hash: str) -> None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("aw:policy:" + self._owner,),
            )
            cur.execute(
                "INSERT INTO policy_selection (owner, seq, preset_id, ips_hash, signature_hash, "
                "created_at) SELECT %(o)s, coalesce(max(seq) + 1, 0), %(p)s, %(i)s, %(s)s, %(t)s "
                "FROM policy_selection WHERE owner = %(o)s",
                {"o": self._owner, "p": preset_id, "i": ips_hash, "s": signature_hash, "t": _now()},
            )

    def latest(self) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT seq, preset_id, ips_hash, signature_hash, created_at "
                "FROM policy_selection WHERE owner = %s ORDER BY seq DESC LIMIT 1",
                (self._owner,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "seq": int(row[0]),
            "preset_id": str(row[1]),
            "ips_hash": str(row[2]),
            "signature_hash": str(row[3]),
            "created_at": _iso(row[4]),
        }


# ---------------------------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------------------------
_JOB_FIELDS = (
    "status",
    "planner_client",
    "order_ids",
    "error_code",
    "started_at",
    "finished_at",
)
_JOB_TIME_FIELDS = ("started_at", "finished_at")


class PgJobStore:
    """Run jobs; at most one queued or running job per owner (partial unique index)."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    def create(self, job_id: str, kind: str, created_at: datetime) -> None:
        try:
            with self._pool.tenant(self._owner) as cur:
                cur.execute(
                    "INSERT INTO jobs (owner, job_id, kind, status, created_at) "
                    "VALUES (%s, %s, %s, 'queued', %s)",
                    (self._owner, job_id, kind, _utc(created_at)),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise ActiveJobExists("The owner already has an active job") from exc

    def update(self, job_id: str, **fields: Any) -> None:
        unknown = set(fields) - set(_JOB_FIELDS)
        if unknown:
            raise ValueError(f"Unknown job fields: {sorted(unknown)}")
        values: dict[str, Any] = {"owner": self._owner, "job_id": job_id}
        for name in _JOB_FIELDS:
            value = fields.get(name)
            if name == "order_ids" and name in fields:
                ids = list(value or [])
                if not all(isinstance(i, str) for i in ids):
                    raise ValueError("order_ids holds order id strings only")
                value = json.dumps(ids)
            if name in _JOB_TIME_FIELDS and value is not None:
                value = _utc(value) if isinstance(value, datetime) else _parse_ts(str(value))
            values["set_" + name] = name in fields
            values[name] = value
        try:
            with self._pool.tenant(self._owner) as cur:
                cur.execute(
                    "UPDATE jobs SET "
                    "status = CASE WHEN %(set_status)s THEN %(status)s ELSE status END, "
                    "planner_client = CASE WHEN %(set_planner_client)s "
                    "THEN %(planner_client)s ELSE planner_client END, "
                    "order_ids = CASE WHEN %(set_order_ids)s "
                    "THEN %(order_ids)s::jsonb ELSE order_ids END, "
                    "error_code = CASE WHEN %(set_error_code)s "
                    "THEN %(error_code)s ELSE error_code END, "
                    "started_at = CASE WHEN %(set_started_at)s "
                    "THEN %(started_at)s::timestamptz ELSE started_at END, "
                    "finished_at = CASE WHEN %(set_finished_at)s "
                    "THEN %(finished_at)s::timestamptz ELSE finished_at END "
                    "WHERE owner = %(owner)s AND job_id = %(job_id)s",
                    values,
                )
                if cur.rowcount == 0:
                    raise KeyError(job_id)
        except psycopg.errors.UniqueViolation as exc:
            raise ActiveJobExists("The owner already has an active job") from exc

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "job_id": str(row[0]),
            "kind": str(row[1]),
            "status": str(row[2]),
            "planner_client": row[3],
            "order_ids": list(row[4]),
            "error_code": row[5],
            "created_at": _iso(row[6]),
            "started_at": _iso(row[7]),
            "finished_at": _iso(row[8]),
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT job_id, kind, status, planner_client, order_ids, error_code, created_at, "
                "started_at, finished_at FROM jobs WHERE owner = %s AND job_id = %s",
                (self._owner, job_id),
            )
            row = cur.fetchone()
        return None if row is None else self._row(row)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT job_id, kind, status, planner_client, order_ids, error_code, created_at, "
                "started_at, finished_at FROM jobs WHERE owner = %s "
                "ORDER BY created_at DESC, job_id DESC LIMIT %s",
                (self._owner, max(int(limit), 0)),
            )
            return [self._row(r) for r in cur.fetchall()]

    def mark_interrupted(self) -> int:
        """Mark this owner's queued and running jobs interrupted (startup recovery)."""
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "UPDATE jobs SET status = 'interrupted', finished_at = %s "
                "WHERE owner = %s AND status IN ('queued', 'running')",
                (_now(), self._owner),
            )
            return int(cur.rowcount)


# ---------------------------------------------------------------------------------------------
# Auth and settings (global tables)
# ---------------------------------------------------------------------------------------------
class PgAuthStore:
    """Sign-in nonces, sessions and step-up challenges, shared between tenants."""

    def __init__(self, pool: PgPool) -> None:
        self._pool = pool

    def put_nonce(self, address: str, nonce: str, expires_at: datetime) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO nonces (address, nonce, expires_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (address, nonce) DO UPDATE SET expires_at = EXCLUDED.expires_at",
                (address, nonce, _utc(expires_at)),
            )

    def consume_nonce(self, address: str, nonce: str, now: datetime) -> bool:
        """Single use: the row is removed whether or not it was still valid."""
        with self._pool.cursor() as cur:
            cur.execute(
                "DELETE FROM nonces WHERE address = %s AND nonce = %s RETURNING expires_at",
                (address, nonce),
            )
            row = cur.fetchone()
        return row is not None and _utc(row[0]) > _utc(now)

    def put_session(self, jti: str, address: str, expires_at: datetime) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (jti, address, expires_at, revoked) "
                "VALUES (%s, %s, %s, false) ON CONFLICT (jti) DO UPDATE SET "
                "address = EXCLUDED.address, expires_at = EXCLUDED.expires_at, revoked = false",
                (jti, address, _utc(expires_at)),
            )

    def get_session(self, jti: str, now: datetime) -> dict[str, Any] | None:
        with self._pool.cursor() as cur:
            cur.execute(
                "SELECT jti, address, expires_at FROM sessions "
                "WHERE jti = %s AND NOT revoked AND expires_at > %s",
                (jti, _utc(now)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {"jti": str(row[0]), "address": str(row[1]), "expires_at": _utc(row[2])}

    def revoke(self, jti: str) -> None:
        with self._pool.cursor() as cur:
            cur.execute("UPDATE sessions SET revoked = true WHERE jti = %s", (jti,))

    def put_stepup(
        self, challenge_id: str, payload: Mapping[str, Any], expires_at: datetime
    ) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO stepup_challenges (challenge_id, payload, expires_at) "
                "VALUES (%s, %s::jsonb, %s) ON CONFLICT (challenge_id) DO UPDATE SET "
                "payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at",
                (challenge_id, json.dumps(dict(payload), sort_keys=True), _utc(expires_at)),
            )

    def consume_stepup(self, challenge_id: str, now: datetime) -> dict[str, Any] | None:
        """Single use, even when expired: the row is removed on every attempt."""
        with self._pool.cursor() as cur:
            cur.execute(
                "DELETE FROM stepup_challenges WHERE challenge_id = %s "
                "RETURNING payload, expires_at",
                (challenge_id,),
            )
            row = cur.fetchone()
        if row is None or _utc(now) >= _utc(row[1]):
            return None
        payload = row[0]
        return dict(payload) if isinstance(payload, dict) else json.loads(payload)

    def purge_expired(self, now: datetime) -> int:
        total = 0
        with self._pool.cursor() as cur:
            cur.execute("DELETE FROM nonces WHERE expires_at <= %s", (_utc(now),))
            total += int(cur.rowcount)
            cur.execute("DELETE FROM sessions WHERE expires_at <= %s", (_utc(now),))
            total += int(cur.rowcount)
            cur.execute("DELETE FROM stepup_challenges WHERE expires_at <= %s", (_utc(now),))
            total += int(cur.rowcount)
        return total


class PgSettings:
    """Operator settings. ``global_stop`` fails closed: any error reads as stopped."""

    def __init__(self, pool: PgPool, env_stop: Callable[[], bool] | None = None) -> None:
        self._pool = pool
        self._env_stop = env_stop

    def global_stop(self) -> bool:
        try:
            if self._env_stop is not None and self._env_stop():
                return True
            with self._pool.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE key = 'global_stop'")
                row = cur.fetchone()
            return row is not None and str(row[0]).strip().lower() in {"1", "true", "yes", "on"}
        except Exception:
            return True

    def set_global_stop(self, stopped: bool) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('global_stop', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                ("true" if stopped else "false",),
            )
