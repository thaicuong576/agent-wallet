"""Exceptions for core.gateway."""

from __future__ import annotations


class GatewayError(Exception):
    """Base exception for gateway failures."""


class InvalidOrder(GatewayError, ValueError):
    """Raised when an order fails contract schema validation."""


class OrderIdConflict(GatewayError, ValueError):
    """Raised when an order ID is reused with a different order body."""
