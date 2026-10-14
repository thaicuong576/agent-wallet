"""Exceptions for the tiers and approval queue subsystem."""

from __future__ import annotations


class TierError(Exception):
    """Base exception for all tier and approval queue errors."""


class InvalidTierError(TierError, ValueError):
    """Raised when an invalid tier value is provided (outside T0, T1, T2)."""


class EmergencyStopActiveError(TierError, ValueError):
    """Raised when an operation (like approve) is attempted while emergency stop is active."""


class OrderNotFoundError(TierError, KeyError, ValueError):
    """Raised when an order_id is not found in the approval queue."""


class OrderAlreadyDecidedError(TierError, ValueError):
    """Raised when an order in the approval queue has already been approved or rejected."""


class OrderAlreadyExistsError(TierError, ValueError):
    """Raised when requesting approval for an order_id that already exists in the queue."""
