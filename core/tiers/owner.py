"""Owner-only controls for managing permission tier, emergency stop, and approval queue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.tiers.store import utc_now_iso

if TYPE_CHECKING:
    from core.tiers.store import TierStore


class OwnerControls:
    """The only mutators for tier state, emergency stop, and approval decisions.

    All mutating actions record 'owner' as changed_by or decided_by.
    """

    def __init__(self, store: TierStore) -> None:
        self._store = store

    def set_tier(self, tier: str) -> None:
        """Set permission tier (T0 | T1 | T2). Rejects values outside the enum."""
        self._store.update_tier_value(tier, updated_at=utc_now_iso())

    def engage_stop(self) -> None:
        """Engage emergency stop. While engaged, all orders are halted and approvals raise."""
        self._store.update_stop_flag(True, updated_at=utc_now_iso())

    def release_stop(self) -> None:
        """Release emergency stop and resume normal policy and approval operations."""
        self._store.update_stop_flag(False, updated_at=utc_now_iso())

    def request_approval(self, order_id: str) -> None:
        """Enqueue an approval request for an escalated order (used by the gateway)."""
        self._store.insert_request(order_id, requested_at=utc_now_iso())

    def approve(self, order_id: str) -> None:
        """Approve a pending order.

        Raises EmergencyStopActiveError if stop is engaged.
        Raises OrderNotFoundError if order is unknown.
        Raises OrderAlreadyDecidedError if order is not in pending status.
        """
        self._store.resolve_request(
            order_id=order_id,
            new_status="approved",
            decided_at=utc_now_iso(),
            decided_by="owner",
        )

    def reject(self, order_id: str) -> None:
        """Reject a pending order.

        Raises OrderNotFoundError if order is unknown.
        Raises OrderAlreadyDecidedError if order is not in pending status.
        """
        self._store.resolve_request(
            order_id=order_id,
            new_status="rejected",
            decided_at=utc_now_iso(),
            decided_by="owner",
        )
