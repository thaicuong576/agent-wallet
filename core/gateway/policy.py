"""Policy protocols and default deny-all policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

# Canonical SHA-256 hex of contracts/examples/ips.valid.json
DEFAULT_IPS_HASH = "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1"


@runtime_checkable
class Policy(Protocol):
    """Protocol for policy engine evaluators."""

    def evaluate(self, order: dict[str, Any]) -> dict[str, Any]:
        """Evaluate an order against IPS rules and return a Decision dict."""
        ...


class DenyAllPolicy:
    """Default policy that unconditionally denies all proposed orders."""

    DEFAULT_REASON = "No policy configured; default deny all orders."

    def __init__(
        self,
        ips_hash: str = DEFAULT_IPS_HASH,
        reason: str = DEFAULT_REASON,
    ) -> None:
        self.ips_hash = ips_hash
        self.reason = reason

    def evaluate(self, order: dict[str, Any]) -> dict[str, Any]:
        token = order.get("token", "")
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "schema_version": 1,
            "order_id": order.get("order_id"),
            "decision": "deny",
            "rules_hit": [
                {
                    "code": "ACCESS.TOKEN_NOT_ALLOWED",
                    "evidence": {
                        "token": str(token),
                    },
                }
            ],
            "reason": self.reason,
            "ips_hash": self.ips_hash,
            "decided_at": now_iso,
        }
