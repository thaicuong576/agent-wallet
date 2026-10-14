"""Policy protocols and default deny-all policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from core.policy.engine import _calculate_order_usd
from core.policy.engine import evaluate as engine_evaluate
from core.policy.ips import LoadedIps, compute_ips_hash

# Canonical SHA-256 hex of contracts/examples/ips.valid.json
DEFAULT_IPS_HASH = "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1"


@runtime_checkable
class Policy(Protocol):
    """Protocol for policy engine evaluators."""

    def evaluate(self, order: dict[str, Any]) -> dict[str, Any]:
        """Evaluate an order against IPS rules and return a Decision dict."""
        ...


class EnginePolicy:
    """Policy adapter implementing the Policy protocol via core.policy.engine.evaluate.

    Built from a LoadedIps, a callable returning the current portfolio state,
    and an optional callable returning risk inputs.
    """

    def __init__(
        self,
        ips: LoadedIps | Mapping[str, Any],
        get_state: Callable[[], Mapping[str, Any]],
        get_risk_inputs: Callable[[], Mapping[str, Any] | None] | None = None,
        clock: Any = None,
    ) -> None:
        self.ips = ips
        self.get_state = get_state
        self.get_risk_inputs = get_risk_inputs
        self.clock = clock
        if isinstance(ips, LoadedIps):
            self.ips_hash = ips.hash
        elif hasattr(ips, "hash") and isinstance(ips.hash, str):
            self.ips_hash = ips.hash
        else:
            self.ips_hash = compute_ips_hash(ips)

    def evaluate(
        self,
        order: dict[str, Any],
        *,
        origin: str = "planner",
        tier: str = "T2",
        emergency_stop: bool = False,
        global_stop: bool = False,
    ) -> dict[str, Any]:
        """Evaluate an order against IPS rules, portfolio state, and risk inputs.

        `origin`, `tier` and the stops are set by the gateway (code), never read from the order.
        """
        state = self.get_state()
        risk_inputs = self.get_risk_inputs() if self.get_risk_inputs is not None else None
        now: datetime | None = None
        if self.clock is not None:
            c = self.clock() if callable(self.clock) else self.clock
            if isinstance(c, datetime):
                now = c
            elif isinstance(c, str):
                now = datetime.fromisoformat(c.replace("Z", "+00:00"))

        decision = engine_evaluate(
            ips=self.ips,
            state=state,
            order=order,
            risk_inputs=risk_inputs,
            now=now,
            origin=origin,
            tier=tier,
            emergency_stop=emergency_stop,
            global_stop=global_stop,
        )

        # Check approval threshold. Owner functions (mint, transfer) are owner-origin actions, not
        # agent spend: they never escalate on the IPS approval threshold (design 7.3).
        if order.get("function") in ("mint", "transfer"):
            return decision

        # Check approval threshold: if allowed by policy engine, check approval.escalate_above_usd
        if decision.get("decision") == "allow":
            ips_data = self.ips.ips if isinstance(self.ips, LoadedIps) else self.ips
            approval_cfg = ips_data.get("approval", {})
            escalate_above = approval_cfg.get("escalate_above_usd")
            if escalate_above is not None:
                order_usd, _ = _calculate_order_usd(order, state)
                if order_usd is not None and order_usd > Decimal(str(escalate_above)):
                    order_usd_str = f"{order_usd:.2f}"
                    now_str = decision.get("decided_at") or (
                        now.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if now
                        else datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    )
                    return {
                        "schema_version": 1,
                        "order_id": order.get("order_id"),
                        "decision": "escalate",
                        "rules_hit": [
                            {
                                "code": "TIER.APPROVAL_REQUIRED",
                                "evidence": {
                                    "order_usd": order_usd_str,
                                    "escalate_above_usd": str(escalate_above),
                                },
                            }
                        ],
                        "reason": (
                            f"Order value {order_usd_str} USD is above the approval threshold "
                            f"of {escalate_above} USD; the owner must approve."
                        ),
                        "ips_hash": self.ips_hash,
                        "decided_at": now_str,
                    }

        return decision


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
