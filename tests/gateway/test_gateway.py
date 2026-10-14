"""Tests for core.gateway: propose, policy, simulate, sign, record (default deny)."""

from __future__ import annotations

import copy
import uuid

import pytest

from core.gateway import (
    DEFAULT_IPS_HASH,
    DenyAllPolicy,
    Gateway,
    InMemoryAuditSink,
    InvalidOrder,
    OrderIdConflict,
    record_hash,
)
from core.gateway.gateway import _get_validator
from core.signer import FakeSigner, SignerError
from core.simulate import SimulationResult


def make_valid_order(**overrides) -> dict:
    """Helper creating a contract-valid Order dictionary."""
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


class AllowPolicy:
    """Test policy that approves any order."""

    def __init__(self, ips_hash: str = DEFAULT_IPS_HASH) -> None:
        self.ips_hash = ips_hash

    def evaluate(self, order: dict) -> dict:
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "decision": "allow",
            "rules_hit": [],
            "reason": "Order satisfies all IPS rules.",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T09:00:01Z",
        }


class EscalatePolicy:
    """Test policy that escalates orders for owner approval."""

    def __init__(self, ips_hash: str = DEFAULT_IPS_HASH) -> None:
        self.ips_hash = ips_hash

    def evaluate(self, order: dict) -> dict:
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "decision": "escalate",
            "rules_hit": [
                {
                    "code": "TIER.APPROVAL_REQUIRED",
                    "evidence": {
                        "order_usd": "150.00",
                        "escalate_above_usd": "100",
                    },
                }
            ],
            "reason": "Order exceeds 100 USD threshold; owner approval required.",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T09:00:01Z",
        }


class MockSimulator:
    """Test simulator tracking simulation calls."""

    def __init__(
        self,
        ok: bool = True,
        error: str | None = None,
        gas_used: int = 50000,
    ) -> None:
        self.ok = ok
        self.error = error
        self.gas_used = gas_used
        self.calls: list[dict] = []

    def simulate(self, tx: dict) -> SimulationResult:
        self.calls.append(dict(tx))
        return SimulationResult(ok=self.ok, error=self.error, gas_used=self.gas_used)


def test_default_deny_all_policy():
    """Default DenyAllPolicy denies orders; simulator and signer are never invoked."""
    sim = MockSimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    gw = Gateway(
        policy=None,  # defaults to DenyAllPolicy
        simulator=sim,
        signer=signer,
        audit_sink=sink,
    )

    order = make_valid_order()
    rec = gw.propose(order)

    assert rec["outcome"] == "denied"
    assert rec["decision"]["decision"] == "deny"
    assert rec["decision"]["rules_hit"][0]["code"] == "ACCESS.TOKEN_NOT_ALLOWED"
    assert rec["execution"] is None
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert len(sink) == 1


def test_injected_allow_policy_executes_and_signs():
    """Allowed order simulates and signs with tx_hash recorded in ExecutionResult."""
    sim = MockSimulator(gas_used=75000)
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    def stub_builder(order: dict) -> list[dict]:
        return [
            {
                "to": "0x2222222222222222222222222222222222222222",
                "data": "0x123456",
                "chainId": 84532,
            }
        ]

    gw = Gateway(
        policy=AllowPolicy(),
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tx_builder=stub_builder,
    )

    order = make_valid_order()
    rec = gw.propose(order)

    assert rec["outcome"] == "executed"
    assert rec["decision"]["decision"] == "allow"
    assert rec["execution"] is not None
    assert rec["execution"]["status"] == "executed"
    assert rec["execution"]["error_code"] is None
    assert rec["execution"]["tx_hash"].startswith("0x")
    assert len(rec["execution"]["tx_hash"]) == 66
    assert rec["execution"]["gas_used"] == 75000
    assert len(sim.calls) == 1
    assert len(signer.calls) == 1


def test_multi_call_signs_each_and_records_last_tx_hash():
    """Multi-call order simulates each call and signs each; last tx_hash is recorded."""
    sim = MockSimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    def stub_multi_builder(order: dict) -> list[dict]:
        return [
            {"to": "0x1111111111111111111111111111111111111111", "data": "0x01", "chainId": 84532},
            {"to": "0x2222222222222222222222222222222222222222", "data": "0x02", "chainId": 84532},
        ]

    gw = Gateway(
        policy=AllowPolicy(),
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tx_builder=stub_multi_builder,
    )

    order = make_valid_order()
    rec = gw.propose(order)

    assert rec["outcome"] == "executed"
    assert len(sim.calls) == 2
    assert len(signer.calls) == 2
    assert rec["execution"]["tx_hash"] is not None


def test_failed_simulation_stops_pipeline():
    """Failed simulation sets outcome failed with error_code SIMULATION_FAILED;
    signer is never called.
    """
    sim = MockSimulator(ok=False, error="execution reverted: pool paused")
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    gw = Gateway(
        policy=AllowPolicy(),
        simulator=sim,
        signer=signer,
        audit_sink=sink,
    )

    order = make_valid_order()
    rec = gw.propose(order)

    assert rec["outcome"] == "failed"
    assert rec["execution"] is not None
    assert rec["execution"]["status"] == "failed"
    assert rec["execution"]["error_code"] == "SIMULATION_FAILED"
    assert rec["execution"]["tx_hash"] is None
    assert "pool paused" in rec["execution"]["detail"]
    assert len(sim.calls) == 1
    assert len(signer.calls) == 0
    assert len(sink) == 1


def test_simulation_exception_handled():
    """Exception during simulation results in outcome failed, signer never called."""

    class CrashingSimulator:
        def simulate(self, tx: dict) -> SimulationResult:
            raise ConnectionError("RPC network failure")

    signer = FakeSigner()
    sink = InMemoryAuditSink()

    gw = Gateway(
        policy=AllowPolicy(),
        simulator=CrashingSimulator(),
        signer=signer,
        audit_sink=sink,
    )

    order = make_valid_order()
    rec = gw.propose(order)

    assert rec["outcome"] == "failed"
    assert rec["execution"]["error_code"] == "SIMULATION_FAILED"
    assert "RPC network failure" in rec["execution"]["detail"]
    assert len(signer.calls) == 0


def test_escalate_policy_produces_pending_approval():
    """Policy returning escalate produces outcome pending_approval; signer never called."""
    sim = MockSimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    gw = Gateway(
        policy=EscalatePolicy(),
        simulator=sim,
        signer=signer,
        audit_sink=sink,
    )

    order = make_valid_order()
    rec = gw.propose(order)

    assert rec["outcome"] == "pending_approval"
    assert rec["decision"]["decision"] == "escalate"
    assert rec["execution"] is None
    assert len(sim.calls) == 0
    assert len(signer.calls) == 0
    assert len(sink) == 1


def test_idempotency_same_order_returns_stored_record():
    """Repeated order returns previously stored record; executes and records only once."""
    sim = MockSimulator()
    signer = FakeSigner()
    sink = InMemoryAuditSink()

    gw = Gateway(
        policy=AllowPolicy(),
        simulator=sim,
        signer=signer,
        audit_sink=sink,
    )

    order = make_valid_order()
    first = gw.propose(order)
    second = gw.propose(order)

    assert first == second
    assert len(sink) == 1
    assert len(signer.calls) == 1
    assert len(sim.calls) == 1


def test_idempotency_different_body_raises_order_id_conflict():
    """Same order_id with different body raises OrderIdConflict."""
    gw = Gateway(policy=AllowPolicy(), simulator=MockSimulator(), signer=FakeSigner())

    order1 = make_valid_order(token="USDC")
    order2 = copy.deepcopy(order1)
    order2["token"] = "WETH"
    order2["amount"] = {"value": "1000000000000000000", "decimals": 18}

    gw.propose(order1)

    with pytest.raises(OrderIdConflict):
        gw.propose(order2)


def test_invalid_order_raises_and_appends_nothing():
    """Order violating schema raises InvalidOrder; nothing appended to audit log or signed."""
    sink = InMemoryAuditSink()
    signer = FakeSigner()
    gw = Gateway(audit_sink=sink, signer=signer)

    # Missing required amount field
    invalid_order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "recipient": "self",
    }

    with pytest.raises(InvalidOrder):
        gw.propose(invalid_order)

    assert len(sink) == 0
    assert len(signer.calls) == 0


def test_non_dict_order_raises_invalid_order():
    """Non-dict order input raises InvalidOrder immediately."""
    gw = Gateway()
    with pytest.raises(InvalidOrder):
        gw.propose(None)
    with pytest.raises(InvalidOrder):
        gw.propose("not a dict")


def test_every_decision_record_validates_against_contract_schema():
    """Records across all outcomes (denied, pending_approval, executed, failed) validate."""
    validator = _get_validator("decision-record")

    # 1. Denied
    gw_deny = Gateway(policy=DenyAllPolicy(), audit_sink=InMemoryAuditSink())
    rec_deny = gw_deny.propose(make_valid_order())
    assert not list(validator.iter_errors(rec_deny))

    # 2. Pending approval
    gw_esc = Gateway(policy=EscalatePolicy(), audit_sink=InMemoryAuditSink())
    rec_esc = gw_esc.propose(make_valid_order())
    assert not list(validator.iter_errors(rec_esc))

    # 3. Executed
    gw_exec = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=FakeSigner(),
        audit_sink=InMemoryAuditSink(),
    )
    rec_exec = gw_exec.propose(make_valid_order())
    assert not list(validator.iter_errors(rec_exec))

    # 4. Failed
    gw_fail = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(ok=False, error="simulation failure"),
        signer=FakeSigner(),
        audit_sink=InMemoryAuditSink(),
    )
    rec_fail = gw_fail.propose(make_valid_order())
    assert not list(validator.iter_errors(rec_fail))


def test_audit_hash_chain_recomputes_and_verifies():
    """Audit log chain correctly links seq, prev_hash, and recomputes SHA-256 canonical hash."""
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=FakeSigner(),
        audit_sink=sink,
    )

    records = [gw.propose(make_valid_order()) for _ in range(3)]

    assert len(records) == 3
    assert sink.verify() is True

    # Manual verification against contracts/README.md specification
    prev = "0" * 64
    for i, r in enumerate(sink.records):
        assert r["seq"] == i
        assert r["prev_hash"] == prev
        assert r["hash"] == record_hash(r)
        prev = r["hash"]


def test_signer_exception_handled_with_safe_error_code():
    """Signer exception produces outcome failed and redacts sensitive key material."""

    class FailingSigner:
        def sign_and_send(self, tx: dict):
            # Simulate an error with a planted private key to test redaction
            fake_key = "112233445566778899001122334455667788990011223344556677889900aabb"
            raise SignerError(f"Signing failed with key {fake_key}")

    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=FailingSigner(),
        audit_sink=InMemoryAuditSink(),
    )

    rec = gw.propose(make_valid_order())

    assert rec["outcome"] == "failed"
    assert rec["execution"]["status"] == "failed"
    assert rec["execution"]["error_code"] == "SIGNER_UNAVAILABLE"
    # Verify the private key was redacted
    assert "11223344" not in rec["execution"]["detail"]
    assert "[REDACTED_KEY]" in rec["execution"]["detail"]


def test_custom_clock_and_ips_hash():
    """Gateway accurately threads custom clock and custom ips_hash through decision and record."""
    fixed_time = "2026-10-18T14:30:00Z"
    custom_ips = "aa" * 32

    gw = Gateway(
        policy=AllowPolicy(ips_hash=custom_ips),
        simulator=MockSimulator(),
        signer=FakeSigner(),
        clock=lambda: fixed_time,
        ips_hash=custom_ips,
    )

    rec = gw.propose(make_valid_order())

    assert rec["created_at"] == fixed_time
    assert rec["ips_hash"] == custom_ips
    assert rec["decision"]["ips_hash"] == custom_ips
    assert rec["execution"]["executed_at"] == fixed_time
