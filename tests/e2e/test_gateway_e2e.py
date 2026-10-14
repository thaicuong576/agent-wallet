"""End-to-end integration tests for Gateway, EnginePolicy, Tiers, and Audit Sink.

Verifies end-to-end order execution, policy denials, tier transitions,
emergency stop semantics, owner approvals, and cryptographic hash chain integrity.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from core.gateway import (
    EnginePolicy,
    Gateway,
    InMemoryAuditSink,
    record_hash,
)
from core.policy.ips import LoadedIps, load_ips
from core.signer import FakeSigner
from core.simulate import SimulationResult
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from core.tiers.view import TierView

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"


@pytest.fixture
def decision_record_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / "decision-record.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture
def base_ips() -> LoadedIps:
    return load_ips(EXAMPLES_DIR / "ips.valid.json")


@pytest.fixture
def approval_band_ips() -> LoadedIps:
    return load_ips(EXAMPLES_DIR / "ips.valid.approval-band.json")


@pytest.fixture
def valid_state() -> dict[str, Any]:
    with open(EXAMPLES_DIR / "portfolio-state.valid.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def raw_ips() -> dict[str, Any]:
    with open(EXAMPLES_DIR / "ips.valid.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def valid_risk_inputs() -> dict[str, Any]:
    with open(EXAMPLES_DIR / "risk-inputs.valid.json", encoding="utf-8") as f:
        return json.load(f)


class MockE2ESimulator:
    """Mock EVM simulator tracking simulation calls."""

    def __init__(self, ok: bool = True, gas_used: int = 65000, error: str | None = None) -> None:
        self.ok = ok
        self.gas_used = gas_used
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        self.calls.append(dict(tx))
        return SimulationResult(ok=self.ok, error=self.error, gas_used=self.gas_used)

    def simulate_bundle(self, txs: list[dict[str, Any]]) -> SimulationResult:
        for t in txs:
            self.calls.append(dict(t))
        return SimulationResult(ok=self.ok, error=self.error, gas_used=self.gas_used)


def stub_builder(order: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "to": "0x2222222222222222222222222222222222222222",
            "data": "0x123456",
            "chainId": 84532,
        }
    ]


def make_order(**overrides: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {
            "value": "80000000",
            "decimals": 6,
        },
        "recipient": "self",
    }
    base.update(overrides)
    return base


def test_deny_token_not_in_universe(
    raw_ips: dict[str, Any],
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 1: Token not in universe is denied with ACCESS.TOKEN_NOT_ALLOWED;
    signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    # IPS universe allows only USDC, not WETH
    ips_dict = copy.deepcopy(raw_ips)
    ips_dict["universe"]["tokens"] = ["USDC"]

    policy = EnginePolicy(
        ips=ips_dict,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    # WETH is a valid contract token, but NOT in this IPS universe
    order = make_order(
        token="WETH",
        amount={"value": "10000000000000000", "decimals": 18},
    )
    record = gw.propose(order)

    assert record["outcome"] == "denied"
    assert record["decision"]["decision"] == "deny"
    codes = [h["code"] for h in record["decision"]["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" in codes
    assert record["execution"] is None
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(record))


def test_deny_function_not_allowed(
    raw_ips: dict[str, Any],
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 2: Function not in universe is denied with ACCESS.FUNCTION_NOT_ALLOWED;
    signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    # IPS universe allows only supply and withdraw, not approve
    ips_dict = copy.deepcopy(raw_ips)
    ips_dict["universe"]["functions"] = ["supply", "withdraw"]

    policy = EnginePolicy(
        ips=ips_dict,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    # approve is a valid contract function, but NOT in this IPS universe
    order = make_order(function="approve", recipient="aave_pool")
    record = gw.propose(order)

    assert record["outcome"] == "denied"
    codes = [h["code"] for h in record["decision"]["rules_hit"]]
    assert "ACCESS.FUNCTION_NOT_ALLOWED" in codes
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(record))


def test_deny_recipient_not_allowed(
    raw_ips: dict[str, Any],
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 3: Recipient not in universe is denied with ACCESS.RECIPIENT_NOT_ALLOWED;
    signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    # IPS universe allows only 'self', not 'aave_pool'
    ips_dict = copy.deepcopy(raw_ips)
    ips_dict["universe"]["recipients"] = ["self"]

    policy = EnginePolicy(
        ips=ips_dict,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    # aave_pool is a valid contract recipient, but NOT in this IPS universe
    order = make_order(recipient="aave_pool")
    record = gw.propose(order)

    assert record["outcome"] == "denied"
    codes = [h["code"] for h in record["decision"]["rules_hit"]]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in codes
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(record))


def test_deny_per_tx_limit(
    base_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 4: Order exceeding per_tx_usd is denied with LIMIT.PER_TX;
    signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=base_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    # 150 USDC > 100 USD limit
    order = make_order(amount={"value": "150000000", "decimals": 6})
    record = gw.propose(order)

    assert record["outcome"] == "denied"
    codes = [h["code"] for h in record["decision"]["rules_hit"]]
    assert "LIMIT.PER_TX" in codes
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(record))


def test_deny_daily_spend_limit(
    base_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 5: Order pushing 24h spend over per_day_usd is denied with LIMIT.PER_DAY;
    signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    # spent_24h is 250 USD; order is 80 USD -> 330 USD > 300 USD per_day_usd
    state = copy.deepcopy(valid_state)
    state["spent_24h_usd"] = "250.00"

    policy = EnginePolicy(
        ips=base_ips,
        get_state=lambda: state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    order = make_order(amount={"value": "80000000", "decimals": 6})
    record = gw.propose(order)

    assert record["outcome"] == "denied"
    codes = [h["code"] for h in record["decision"]["rules_hit"]]
    assert "LIMIT.PER_DAY" in codes
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(record))


def test_deny_max_weight_per_asset(
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 6: Order pushing an asset weight above max_weight_per_asset_bps is denied with
    ALLOC.MAX_WEIGHT_PER_ASSET; signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    # Load custom ips where per_tx limit is generous (500)
    # but max_weight_per_asset_bps is 4000 (40%)
    with open(EXAMPLES_DIR / "ips.valid.json", encoding="utf-8") as f:
        ips_data = json.load(f)
    ips_data["limits"]["per_tx_usd"] = "500"
    ips_data["limits"]["per_day_usd"] = "1000"

    # State: 700 USD USDC (70%), 300 USD WETH (30%), Total: 1000 USD
    state = {
        "schema_version": 1,
        "wallet": "0x1111111111111111111111111111111111111111",
        "updated_at": "2026-10-16T09:00:00Z",
        "total_value_usd": "1000.00",
        "spent_24h_usd": "0",
        "tx_count_1h": 0,
        "positions": [
            {
                "token": "USDC",
                "location": "wallet",
                "balance": {"value": "700000000", "decimals": 6},
                "price_usd": "1.00",
                "value_usd": "700.00",
                "weight_bps": 7000,
            },
            {
                "token": "WETH",
                "location": "wallet",
                "balance": {"value": "120000000000000000", "decimals": 18},
                "price_usd": "2500.00",
                "value_usd": "300.00",
                "weight_bps": 3000,
            },
        ],
    }

    policy = EnginePolicy(
        ips=ips_data,
        get_state=lambda: state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    # Swap 150 USDC for WETH -> WETH becomes 450 USD (45% > 40% max_weight_per_asset_bps)
    with open(EXAMPLES_DIR / "order.valid.swap.json", encoding="utf-8") as f:
        swap_order = json.load(f)
    swap_order["order_id"] = str(uuid.uuid4())
    swap_order["amount"] = {"value": "150000000", "decimals": 6}

    record = gw.propose(swap_order)

    assert record["outcome"] == "denied"
    codes = [h["code"] for h in record["decision"]["rules_hit"]]
    assert "ALLOC.MAX_WEIGHT_PER_ASSET" in codes
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(record))


def test_deny_stale_and_missing_risk_inputs(
    base_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 7 & 8: Stale risk data and missing risk inputs both deny with RISK.DATA_STALE;
    signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    # Part A: Stale risk inputs (data_age_seconds: 99999 > 7200 max)
    stale_inputs = copy.deepcopy(valid_risk_inputs)
    stale_inputs["data_age_seconds"] = 99999

    policy_stale = EnginePolicy(
        ips=base_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: stale_inputs,
    )
    gw_stale = Gateway(
        policy=policy_stale,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    order_a = make_order()
    rec_a = gw_stale.propose(order_a)
    assert rec_a["outcome"] == "denied"
    codes_a = [h["code"] for h in rec_a["decision"]["rules_hit"]]
    assert "RISK.DATA_STALE" in codes_a
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(rec_a))

    # Part B: Missing risk inputs (None returned)
    policy_missing = EnginePolicy(
        ips=base_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: None,
    )
    gw_missing = Gateway(
        policy=policy_missing,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    order_b = make_order()
    rec_b = gw_missing.propose(order_b)
    assert rec_b["outcome"] == "denied"
    codes_b = [h["code"] for h in rec_b["decision"]["rules_hit"]]
    assert "RISK.DATA_STALE" in codes_b
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(rec_b))


def test_valid_order_in_t2_executes_and_validates(
    base_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 9: Valid order in tier T2 simulates and signs; audit record validates."""
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator(gas_used=82000)
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=base_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    order = make_order()
    record = gw.propose(order)

    assert record["outcome"] == "executed"
    assert record["decision"]["decision"] == "allow"
    assert record["tier"] == "T2"
    assert record["emergency_stop"] is False
    assert record["execution"] is not None
    assert record["execution"]["status"] == "executed"
    assert record["execution"]["gas_used"] == 82000
    assert record["execution"]["tx_hash"].startswith("0x")
    assert len(sim.calls) == 1
    assert len(signer.calls) == 1
    assert not list(decision_record_validator.iter_errors(record))


def test_emergency_stop_denies_and_release_allows(
    base_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 10: Emergency stop denies before policy or signer; releasing it allows again."""
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    owner.engage_stop()
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=base_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    # 1. While stopped: order is denied with TIER.EMERGENCY_STOP
    order1 = make_order()
    rec1 = gw.propose(order1)

    assert rec1["outcome"] == "denied"
    assert rec1["emergency_stop"] is True
    assert rec1["decision"]["rules_hit"][0]["code"] == "TIER.EMERGENCY_STOP"
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(rec1))

    # 2. Release stop: next order succeeds
    owner.release_stop()
    order2 = make_order()
    rec2 = gw.propose(order2)

    assert rec2["outcome"] == "executed"
    assert rec2["emergency_stop"] is False
    assert len(sim.calls) == 1
    assert len(signer.calls) == 1
    assert not list(decision_record_validator.iter_errors(rec2))


def test_t0_and_t1_tiers_never_execute(
    base_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 11: Tier T0 denies with TIER.READ_ONLY; Tier T1 denies with TIER.PROPOSE_ONLY;
    signer & simulator never called.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=base_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        tx_builder=stub_builder,
    )

    # T0 (default)
    assert view.current() == "T0"
    rec_t0 = gw.propose(make_order())
    assert rec_t0["outcome"] == "denied"
    assert rec_t0["decision"]["rules_hit"][0]["code"] == "TIER.READ_ONLY"
    assert len(signer.calls) == 0

    # T1
    owner.set_tier("T1")
    assert view.current() == "T1"
    rec_t1 = gw.propose(make_order())
    assert rec_t1["outcome"] == "denied"
    assert rec_t1["decision"]["rules_hit"][0]["code"] == "TIER.PROPOSE_ONLY"
    assert len(signer.calls) == 0


def test_escalation_lifecycle_pending_to_approved_executes_once(
    approval_band_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 12: Escalated order stays pending_approval; after owner approves and re-proposes,
    it executes exactly once; same order ID never executes twice.
    """
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=approval_band_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        request_approval=owner.request_approval,
        tx_builder=stub_builder,
    )

    # Order value is 80 USDC = 80.00 USD.
    # limits.per_tx_usd is 100 USD (passes).
    # approval.escalate_above_usd is 50 USD (escalates).
    order = make_order(amount={"value": "80000000", "decimals": 6})
    order_id = order["order_id"]

    # 1. Propose order: escalates to pending_approval
    rec1 = gw.propose(order)
    assert rec1["outcome"] == "pending_approval"
    assert rec1["decision"]["decision"] == "escalate"
    assert rec1["decision"]["rules_hit"][0]["code"] == "TIER.APPROVAL_REQUIRED"
    assert rec1["execution"] is None
    assert rec1["approval"] is None
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(rec1))

    # Verify request was enqueued in SQLite approval queue
    assert order_id in view.pending_orders()
    assert view.approval_status(order_id) == "pending"

    # 2. Re-propose while still pending: stays pending_approval, signer never called
    rec_pending_retry = gw.propose(order)
    assert rec_pending_retry["outcome"] == "pending_approval"
    assert len(signer.calls) == 0

    # 3. Owner approves order
    owner.approve(order_id)
    assert view.approval_status(order_id) == "approved"

    # 4. Re-propose approved order: executes
    rec_approved = gw.propose(order)
    assert rec_approved["outcome"] == "executed"
    assert rec_approved["decision"]["decision"] == "escalate"
    assert rec_approved["execution"] is not None
    assert rec_approved["execution"]["status"] == "executed"
    assert rec_approved["approval"] is not None
    assert rec_approved["approval"]["status"] == "approved"
    assert rec_approved["approval"]["by"] == "owner"
    assert len(sim.calls) == 1
    assert len(signer.calls) == 1
    assert not list(decision_record_validator.iter_errors(rec_approved))

    # 5. Re-propose 3rd time: idempotency ensures it executes only once
    rec_retry = gw.propose(order)
    assert rec_retry["outcome"] == "executed"
    assert rec_retry == rec_approved
    assert len(signer.calls) == 1  # Never signed a second time!
    assert len(sim.calls) == 1


def test_escalation_lifecycle_rejected_never_executes(
    approval_band_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 13: Escalated order rejected by owner never executes on re-propose."""
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=approval_band_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        request_approval=owner.request_approval,
        tx_builder=stub_builder,
    )

    order = make_order(amount={"value": "80000000", "decimals": 6})
    order_id = order["order_id"]

    rec_esc = gw.propose(order)
    assert rec_esc["outcome"] == "pending_approval"

    # Owner rejects order
    owner.reject(order_id)
    assert view.approval_status(order_id) == "rejected"

    # Re-propose rejected order: outcome is denied with TIER.APPROVAL_REJECTED
    rec_rej = gw.propose(order)
    assert rec_rej["outcome"] == "denied"
    assert rec_rej["decision"]["decision"] == "deny"
    codes = [h["code"] for h in rec_rej["decision"]["rules_hit"]]
    assert "TIER.APPROVAL_REJECTED" in codes
    assert rec_rej["approval"] is not None
    assert rec_rej["approval"]["status"] == "rejected"
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(rec_rej))

    # Re-propose again: returns stored denied record, never signs
    rec_rej_again = gw.propose(order)
    assert rec_rej_again["outcome"] == "denied"
    assert len(signer.calls) == 0


def test_emergency_stop_denies_even_approved_order(
    approval_band_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 14: Emergency stop denies even an approved order; releasing it allows it."""
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=approval_band_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        request_approval=owner.request_approval,
        tx_builder=stub_builder,
    )

    order = make_order(amount={"value": "80000000", "decimals": 6})
    order_id = order["order_id"]

    gw.propose(order)
    owner.approve(order_id)

    # Engage emergency stop after approval
    owner.engage_stop()

    rec_stopped = gw.propose(order)
    assert rec_stopped["outcome"] == "denied"
    assert rec_stopped["decision"]["rules_hit"][0]["code"] == "TIER.EMERGENCY_STOP"
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(rec_stopped))

    # Release stop -> order now executes
    owner.release_stop()
    rec_exec = gw.propose(order)
    assert rec_exec["outcome"] == "executed"
    assert len(signer.calls) == 1
    assert not list(decision_record_validator.iter_errors(rec_exec))


def test_audit_log_hash_chain_in_memory(
    approval_band_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 15: Decision records written in sequence form a valid cryptographic hash chain."""
    store = TierStore(":memory:")
    owner = OwnerControls(store)
    owner.set_tier("T2")
    view = TierView(store)
    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=approval_band_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=view,
        request_approval=owner.request_approval,
        tx_builder=stub_builder,
    )

    # 1. Denied order (per-tx limit exceeded: 150 USD > 100 USD)
    gw.propose(make_order(amount={"value": "150000000", "decimals": 6}))

    # 2. Escalated order (pending_approval)
    order_esc = make_order(amount={"value": "80000000", "decimals": 6})
    gw.propose(order_esc)

    # 3. Approved order execution
    owner.approve(order_esc["order_id"])
    gw.propose(order_esc)

    # 4. Standard valid order (under 50 USD threshold: 40 USDC)
    order_small = make_order(amount={"value": "40000000", "decimals": 6})
    gw.propose(order_small)

    assert len(sink) == 4
    assert sink.verify() is True

    prev = "0" * 64
    for i, r in enumerate(sink.records):
        assert r["seq"] == i
        assert r["prev_hash"] == prev
        assert r["hash"] == record_hash(r)
        assert not list(decision_record_validator.iter_errors(r))
        prev = r["hash"]


def test_fail_closed_on_tierview_read_error(
    base_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
    decision_record_validator: jsonschema.Draft202012Validator,
):
    """Scenario 16: Fail-closed: any error reading TierView denies with TIER.EMERGENCY_STOP."""

    class BrokenTierView:
        def is_stopped(self) -> bool:
            raise RuntimeError("Database connection corrupted")

        def current(self) -> str:
            raise RuntimeError("Database connection corrupted")

        def approval_status(self, order_id: str) -> str | None:
            return None

    sim = MockE2ESimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    policy = EnginePolicy(
        ips=base_ips,
        get_state=lambda: valid_state,
        get_risk_inputs=lambda: valid_risk_inputs,
    )
    gw = Gateway(
        policy=policy,
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tier_view=BrokenTierView(),
        tx_builder=stub_builder,
    )

    record = gw.propose(make_order())
    assert record["outcome"] == "denied"
    assert record["emergency_stop"] is True
    assert record["decision"]["rules_hit"][0]["code"] == "TIER.EMERGENCY_STOP"
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert not list(decision_record_validator.iter_errors(record))


def test_simulation_runs_from_the_signer_address():
    """The simulator must see `from` = signer address, but the signed tx must not carry it."""
    from core.signer import FakeSigner

    seen: dict[str, list] = {"sim": [], "sign": []}

    class RecordingSimulator:
        def simulate(self, tx):
            seen["sim"].append(dict(tx))
            from core.simulate import SimulationResult

            return SimulationResult(ok=True, error=None, gas_used=1, raw={})

    class RecordingSigner(FakeSigner):
        def sign_and_send(self, tx):
            seen["sign"].append(dict(tx))
            return super().sign_and_send(tx)

    from core.gateway.gateway import Gateway

    class Allow:
        def evaluate(self, order):
            return {
                "schema_version": 1,
                "order_id": order["order_id"],
                "decision": "allow",
                "rules_hit": [],
                "reason": "ok",
                "ips_hash": "0" * 64,
                "decided_at": "2026-10-14T00:00:00Z",
            }

    signer = RecordingSigner()
    gw = Gateway(
        policy=Allow(),
        simulator=RecordingSimulator(),
        signer=signer,
        tx_builder=lambda o: [{"to": "0x" + "22" * 20, "data": "0x", "chainId": 84532}],
        tier="T2",
    )
    import json
    import pathlib

    order = json.loads(
        (
            pathlib.Path(__file__).resolve().parents[2] / "contracts/examples/order.valid.json"
        ).read_text()
    )
    gw.propose(order)
    assert seen["sim"][0]["from"] == signer.address
    assert "from" not in seen["sign"][0]
