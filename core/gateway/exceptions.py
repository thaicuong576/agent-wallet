"""Exceptions for core.gateway."""

from __future__ import annotations


class GatewayError(Exception):
    """Base exception for gateway failures."""


class InvalidOrder(GatewayError, ValueError):
    """Raised when an order fails contract schema validation."""


class OrderIdConflict(GatewayError, ValueError):
    """Raised when an order ID is reused with a different order body."""


class GatewayBusy(GatewayError):
    """Raised when the gateway lock cannot be acquired within the timeout."""


class ReconcileRequired(GatewayError):
    """Raised when an intent exists without an executed/failed audit record."""
