"""`origin` is a Python argument set by code, never read from the order body."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from core.gateway import DEFAULT_IPS_HASH, Gateway, InMemoryAuditSink, InvalidOrder
from core.policy.engine import evaluate as engine_evaluate
from core.policy.ips import load_ips
from core.signer import FakeSigner
from core.simulate import SimulationResult

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"


class EnginePolicyWithOrigin:
    """The real engine, wired the way the gateway passes origin, tier and stops."""

    def __init__(self) -> None:
        self.ips = load_ips(EXAMPLES / "ips.valid.json")
        self.ips_hash = self.ips.hash

    def evaluate(
        self,
        order: dict[str, Any],
        *,
        origin: str = "planner",
        tier: str = "T2",
        emergency_stop: bool = False,
        global_stop: bool = False,
    ) -> dict[str, Any]:
        return engine_evaluate(
            self.ips,
            {},
            order,
            risk_inputs=None,
            origin=origin,
            tier=tier,
            emergency_stop=emergency_stop,
            global_stop=global_stop,
        )


class AlwaysAllowNoOrigin:
    """A policy that ignores origin entirely: the gateway must still hold the line."""

    ips_hash = DEFAULT_IPS_HASH

    def evaluate(self, order: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "decision": "allow",
            "rules_hit": [],
            "reason": "ok",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T09:00:01Z",
        }


class OkSimulator:
    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        return SimulationResult(ok=True, error=None, gas_used=1)


def _mint() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "mint",
        "token": "USDC",
        "amount": {"value": "1000000", "decimals": 6},
        "recipient": "self",
    }


def _builder(order: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
    return [{"to": "0x2222222222222222222222222222222222222222", "data": "0x", "chainId": 84532}]


def _gateway(policy: Any) -> tuple[Gateway, FakeSigner]:
    signer = FakeSigner()
    gw = Gateway(
        policy=policy,
        simulator=OkSimulator(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=_builder,
        global_stop=lambda: False,
    )
    return gw, signer


def _codes(record: dict[str, Any]) -> list[str]:
    return [h["code"] for h in record["decision"]["rules_hit"]]


def test_planner_origin_cannot_mint() -> None:
    gw, signer = _gateway(EnginePolicyWithOrigin())
    rec = gw.propose(_mint(), origin="planner")
    assert rec["outcome"] == "denied"
    assert "ACCESS.OWNER_ONLY_FUNCTION" in _codes(rec)
    assert signer.calls == []


def test_default_origin_is_planner() -> None:
    gw, signer = _gateway(EnginePolicyWithOrigin())
    rec = gw.propose(_mint())
    assert rec["outcome"] == "denied"
    assert "ACCESS.OWNER_ONLY_FUNCTION" in _codes(rec)
    assert signer.calls == []


def test_gateway_denies_owner_function_even_if_policy_ignores_origin() -> None:
    gw, signer = _gateway(AlwaysAllowNoOrigin())
    rec = gw.propose(_mint())
    assert rec["outcome"] == "denied"
    assert _codes(rec) == ["ACCESS.OWNER_ONLY_FUNCTION"]
    assert signer.calls == []


def test_origin_is_not_read_from_the_order_body() -> None:
    gw, signer = _gateway(EnginePolicyWithOrigin())
    order = _mint()
    order["origin"] = "owner"
    # Whether the schema rejects the unknown field or the gateway ignores it, it never grants
    # owner origin.
    try:
        rec = gw.propose(order)
    except InvalidOrder:
        rec = None
    if rec is not None:
        assert rec["outcome"] == "denied"
    # `proposed_by` is informational too.
    order2 = _mint()
    order2["proposed_by"] = "owner"
    rec2 = gw.propose(order2)
    assert rec2["outcome"] == "denied"
    assert "ACCESS.OWNER_ONLY_FUNCTION" in _codes(rec2)
    assert signer.calls == []


def test_unknown_origin_rejected() -> None:
    gw, signer = _gateway(EnginePolicyWithOrigin())
    with pytest.raises(InvalidOrder):
        gw.propose(_mint(), origin="admin")  # type: ignore[arg-type]
    assert signer.calls == []


def test_planner_origin_orders_behave_as_before() -> None:
    gw, signer = _gateway(AlwaysAllowNoOrigin())
    order = _mint()
    order["function"] = "supply"
    rec = gw.propose(order)
    assert rec["outcome"] == "executed"
    assert len(signer.calls) == 1
