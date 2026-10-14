"""Read-only view of tier state and approval queue handed to the gateway and agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.tiers.store import TierStore


class TierView:
    """Read-only interface for checking current permission tier, stop status, and approvals.

    This object exposes no mutators and is safe to provide to agent-facing modules and gateway.
    """

    def __init__(self, store: TierStore) -> None:
        self._store = store

    def current(self) -> str:
        """Return the current permission tier ('T0', 'T1', or 'T2')."""
        return str(self._store.get_tier_state()["tier"])

    def is_stopped(self) -> bool:
        """Return True if the emergency stop is currently engaged."""
        return bool(self._store.get_tier_state()["emergency_stop"])

    def pending_orders(self) -> list[str]:
        """Return list of order IDs currently awaiting owner approval."""
        return self._store.list_pending_orders()

    def approval_status(self, order_id: str) -> str | None:
        """Return approval status ('pending', 'approved', 'rejected') or None if unknown."""
        record = self._store.get_approval(order_id)
        if record is None:
            return None
        return str(record["status"])
