"""Read-only view of tier state and approval queue handed to the gateway and agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.tiers.store import TierStore


class _TierViewMeta(type):
    """Metaclass preserving class-level introspection for TierView."""

    def __dir__(cls) -> list[str]:
        return [attr for attr in super().__dir__() if attr != "get_approval"]


class TierView(metaclass=_TierViewMeta):
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

    def get_approval(self, order_id: str) -> dict[str, Any] | None:
        """Return approval record for an order_id or None if unknown."""
        return self._store.get_approval(order_id)

    def approval_status(self, order_id: str) -> str | None:
        """Return approval status ('pending', 'approved', 'rejected') or None if unknown."""
        record = self.get_approval(order_id)
        if record is None:
            return None
        return str(record["status"])
