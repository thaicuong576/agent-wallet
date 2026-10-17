"""PgTiers: implements TierRead and TierWrite protocols (core.storage.interfaces)."""

from __future__ import annotations

from typing import Any

import psycopg

from core.storage.postgres.common import ALLOWED_TIERS, _iso, _now
from core.storage.postgres.pool import PgPool
from core.tiers.errors import (
    EmergencyStopActiveError,
    InvalidTierError,
    OrderAlreadyDecidedError,
    OrderAlreadyExistsError,
    OrderNotFoundError,
)


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
