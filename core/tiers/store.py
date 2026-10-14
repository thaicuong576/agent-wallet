"""SQLite-backed persistent store for tier state and approval queue."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

from core.tiers.errors import (
    EmergencyStopActiveError,
    InvalidTierError,
    OrderAlreadyDecidedError,
    OrderAlreadyExistsError,
    OrderNotFoundError,
)

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "tier-state.schema.json"
)
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)

ALLOWED_TIERS = frozenset({"T0", "T1", "T2"})
RFC3339_UTC_REGEX = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$"
)


def utc_now_iso() -> str:
    """Return the current UTC timestamp formatted as RFC 3339 with a trailing Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class TierStore:
    """Persistent SQLite store for tier permissions, emergency stop, and approval queue."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = db_path
        self._db_path_str = str(db_path)
        if self._db_path_str != ":memory:" and not self._db_path_str.startswith("file:"):
            Path(self._db_path_str).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path_str, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_db()

    def _init_db(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tier_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                schema_version INTEGER NOT NULL DEFAULT 1,
                tier TEXT NOT NULL CHECK (tier IN ('T0', 'T1', 'T2')),
                emergency_stop INTEGER NOT NULL CHECK (emergency_stop IN (0, 1)),
                updated_at TEXT NOT NULL,
                changed_by TEXT NOT NULL CHECK (changed_by = 'owner')
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_queue (
                order_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
                requested_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by TEXT CHECK (decided_by IS NULL OR decided_by = 'owner')
            );
            """
        )
        cur.execute("SELECT COUNT(*) FROM tier_state WHERE id = 1")
        if cur.fetchone()[0] == 0:
            now_ts = utc_now_iso()
            default_state = {
                "schema_version": 1,
                "tier": "T0",
                "emergency_stop": False,
                "updated_at": now_ts,
                "changed_by": "owner",
            }
            self.validate_tier_state(default_state)
            cur.execute(
                """
                INSERT INTO tier_state (
                    id, schema_version, tier, emergency_stop, updated_at, changed_by
                ) VALUES (1, 1, 'T0', 0, ?, 'owner')
                """,
                (now_ts,),
            )
        self._conn.commit()

    def validate_tier_state(self, state: dict[str, Any]) -> None:
        """Validate state dictionary against the contracts/schemas/tier-state.schema.json."""
        if state.get("tier") not in ALLOWED_TIERS:
            raise InvalidTierError(f"Invalid tier: '{state.get('tier')}'. Must be T0, T1, or T2.")
        _VALIDATOR.validate(state)

    def get_tier_state(self) -> dict[str, Any]:
        """Read the singleton tier state object, strictly validating against the schema."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT schema_version, tier, emergency_stop, updated_at, changed_by
            FROM tier_state
            WHERE id = 1
            """
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Corrupt state: singleton tier_state row missing.")
        state: dict[str, Any] = {
            "schema_version": int(row[0]),
            "tier": str(row[1]),
            "emergency_stop": bool(row[2]),
            "updated_at": str(row[3]),
            "changed_by": str(row[4]),
        }
        self.validate_tier_state(state)
        return state

    def update_tier_value(self, tier: str, updated_at: str) -> None:
        """Update permission tier (T0 | T1 | T2) by the owner."""
        if tier not in ALLOWED_TIERS:
            raise InvalidTierError(
                f"Invalid tier: '{tier}'. Allowed tiers are {sorted(ALLOWED_TIERS)}."
            )
        if not RFC3339_UTC_REGEX.match(updated_at):
            raise ValueError(f"Invalid timestamp: '{updated_at}'. Must match RFC 3339 UTC format.")

        cur = self._conn.cursor()
        cur.execute("SELECT emergency_stop FROM tier_state WHERE id = 1")
        row = cur.fetchone()
        current_stop = bool(row[0]) if row else False

        candidate: dict[str, Any] = {
            "schema_version": 1,
            "tier": tier,
            "emergency_stop": current_stop,
            "updated_at": updated_at,
            "changed_by": "owner",
        }
        self.validate_tier_state(candidate)

        cur.execute(
            "UPDATE tier_state SET tier = ?, updated_at = ? WHERE id = 1",
            (tier, updated_at),
        )
        self._conn.commit()

    def update_stop_flag(self, stopped: bool, updated_at: str) -> None:
        """Engage or release emergency stop flag."""
        if not isinstance(stopped, bool):
            raise ValueError("emergency_stop flag must be a boolean.")
        if not RFC3339_UTC_REGEX.match(updated_at):
            raise ValueError(f"Invalid timestamp: '{updated_at}'. Must match RFC 3339 UTC format.")

        cur = self._conn.cursor()
        cur.execute("SELECT tier FROM tier_state WHERE id = 1")
        row = cur.fetchone()
        current_tier = str(row[0]) if row else "T0"

        candidate: dict[str, Any] = {
            "schema_version": 1,
            "tier": current_tier,
            "emergency_stop": stopped,
            "updated_at": updated_at,
            "changed_by": "owner",
        }
        self.validate_tier_state(candidate)

        cur.execute(
            "UPDATE tier_state SET emergency_stop = ?, updated_at = ? WHERE id = 1",
            (1 if stopped else 0, updated_at),
        )
        self._conn.commit()

    def insert_request(self, order_id: str, requested_at: str) -> None:
        """Enqueue an approval request for an escalated order."""
        if not order_id or not isinstance(order_id, str):
            raise ValueError("order_id must be a non-empty string.")
        if not RFC3339_UTC_REGEX.match(requested_at):
            raise ValueError(
                f"Invalid timestamp: '{requested_at}'. Must match RFC 3339 UTC format."
            )

        cur = self._conn.cursor()
        cur.execute("SELECT status FROM approval_queue WHERE order_id = ?", (order_id,))
        if cur.fetchone() is not None:
            raise OrderAlreadyExistsError(
                f"Order '{order_id}' already exists in the approval queue."
            )

        cur.execute(
            """
            INSERT INTO approval_queue (order_id, status, requested_at, decided_at, decided_by)
            VALUES (?, 'pending', ?, NULL, NULL)
            """,
            (order_id, requested_at),
        )
        self._conn.commit()

    def resolve_request(
        self,
        order_id: str,
        new_status: str,
        decided_at: str,
        decided_by: str = "owner",
    ) -> None:
        """Resolve a pending order with approved or rejected."""
        if new_status not in ("approved", "rejected"):
            raise ValueError(
                f"Invalid decision status: '{new_status}'. Must be 'approved' or 'rejected'."
            )
        if decided_by != "owner":
            raise ValueError(f"Invalid decided_by: '{decided_by}'. Only 'owner' may decide orders.")
        if not RFC3339_UTC_REGEX.match(decided_at):
            raise ValueError(f"Invalid timestamp: '{decided_at}'. Must match RFC 3339 UTC format.")

        if new_status == "approved":
            current_state = self.get_tier_state()
            if current_state["emergency_stop"]:
                raise EmergencyStopActiveError(
                    "Cannot approve order while emergency stop is active."
                )

        cur = self._conn.cursor()
        cur.execute("SELECT status FROM approval_queue WHERE order_id = ?", (order_id,))
        row = cur.fetchone()
        if row is None:
            raise OrderNotFoundError(f"Order '{order_id}' not found in approval queue.")

        existing_status = row[0]
        if existing_status != "pending":
            raise OrderAlreadyDecidedError(
                f"Order '{order_id}' has already been decided with status '{existing_status}'."
            )

        cur.execute(
            """
            UPDATE approval_queue
            SET status = ?, decided_at = ?, decided_by = ?
            WHERE order_id = ?
            """,
            (new_status, decided_at, decided_by, order_id),
        )
        self._conn.commit()

    def get_approval(self, order_id: str) -> dict[str, Any] | None:
        """Get the stored approval record for an order_id, or None if unknown."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT order_id, status, requested_at, decided_at, decided_by
            FROM approval_queue
            WHERE order_id = ?
            """,
            (order_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "order_id": str(row[0]),
            "status": str(row[1]),
            "requested_at": str(row[2]),
            "decided_at": row[3],
            "decided_by": row[4],
        }

    def list_pending_orders(self) -> list[str]:
        """List all order IDs currently waiting in pending status, ordered by requested_at."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT order_id
            FROM approval_queue
            WHERE status = 'pending'
            ORDER BY requested_at ASC
            """
        )
        return [str(row[0]) for row in cur.fetchall()]

    def close(self) -> None:
        """Close database connection."""
        self._conn.close()

    def __enter__(self) -> TierStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
