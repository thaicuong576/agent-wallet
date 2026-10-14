"""Tests for gateway transaction builder, builder error recording, and TierView.get_approval."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from core.gateway.audit import InMemoryAuditSink
from core.gateway.gateway import Gateway
from core.gateway.txbuilder import AaveTxBuilder, TxBuildError
from core.protocols.aave import Call
from core.simulate import SimulationResult
from core.tiers.store import TierStore
from core.tiers.view import TierView

TEST_WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"
FIXED_NOW_TS = 1700000000  # 2023-11-14T22:13:20Z
FIXED_NOW_DT = datetime.fromtimestamp(FIXED_NOW_TS, tz=UTC)


class AllowPolicy:
    ips_hash = "0" * 64

    def evaluate(self, order: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "decision": "allow",
            "rules_hit": [],
            "reason": "allowed by test policy",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-14T00:00:00Z",
        }


class MockSimulator:
    def __init__(self, ok: bool = True, gas_used: int = 100_000) -> None:
        self.ok = ok
        self.gas_used = gas_used
        self.calls: list[dict[str, Any]] = []

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        self.calls.append(tx)
        return SimulationResult(
            ok=self.ok,
            error=None if self.ok else "revert",
            gas_used=self.gas_used,
            raw={},
        )


class BundleSimulator:
    def __init__(self, ok: bool = True, gas_used: int = 150_000) -> None:
        self.ok = ok
        self.gas_used = gas_used
        self.bundles: list[list[dict[str, Any]]] = []
        self.calls: list[dict[str, Any]] = []

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        self.calls.append(tx)
        return SimulationResult(
            ok=self.ok,
            error=None if self.ok else "revert",
            gas_used=self.gas_used,
            raw={},
        )

    def simulate_bundle(self, txs: list[dict[str, Any]]) -> SimulationResult:
        self.bundles.append(list(txs))
        return SimulationResult(
            ok=self.ok,
            error=None if self.ok else "bundle revert",
            gas_used=self.gas_used,
            raw={},
        )


class FakeSigner:
    address = TEST_WALLET

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def sign_and_send(self, tx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tx)
        return {"tx_hash": "0x" + "ab" * 32}


def make_valid_order(function: str = "approve", recipient: str = "aave_pool") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-14T00:00:00Z",
        "proposed_by": "owner",
        "protocol": "aave-v3",
        "function": function,
        "token": "USDC",
        "amount": {"value": "1000000", "decimals": 6},
        "recipient": recipient,
    }


def make_mock_rpc(
    chain_id: int = 84532,
    latest_block_timestamp: int = FIXED_NOW_TS,
    base_fee_per_gas: int = 10_000_000_000,
    pending_nonce: int = 5,
    latest_nonce: int = 5,
    estimate_gas: int | Exception = 50_000,
    balance: int = 10**18,  # 1 ETH
) -> Callable[[str, list[Any]], Any]:
    def rpc(method: str, params: list[Any]) -> Any:
        if method == "eth_chainId":
            return hex(chain_id)
        if method == "eth_getBlockByNumber":
            return {
                "timestamp": hex(latest_block_timestamp),
                "baseFeePerGas": hex(base_fee_per_gas),
            }
        if method == "eth_getTransactionCount":
            tag = params[1]
            if tag == "pending":
                return hex(pending_nonce)
            if tag == "latest":
                return hex(latest_nonce)
            return hex(0)
        if method == "eth_estimateGas":
            if isinstance(estimate_gas, Exception):
                raise estimate_gas
            return hex(estimate_gas)
        if method == "eth_getBalance":
            return hex(balance)
        raise ValueError(f"Unexpected RPC call: {method} {params}")

    return rpc


# ==============================================================================
# Acceptance: stale head gives failed record with detail starting "tx build:"
# ==============================================================================
def test_stale_head_recorded_as_failed_with_detail() -> None:
    # Block timestamp 120s in the past relative to clock (max_head_age_s is 60s)
    rpc = make_mock_rpc(latest_block_timestamp=FIXED_NOW_TS - 120)
    builder = AaveTxBuilder(rpc, TEST_WALLET, clock=lambda: FIXED_NOW_DT)
    signer = FakeSigner()
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=builder,
    )

    order = make_valid_order()
    record = gw.propose(order)

    assert record["outcome"] == "failed"
    assert record["execution"] is not None
    assert record["execution"]["status"] == "failed"
    assert record["execution"]["error_code"] == "SIMULATION_FAILED"
    assert str(record["execution"]["detail"]).startswith("tx build:")
    assert "stale head" in str(record["execution"]["detail"])
    assert signer.calls == []
    assert len(sink.records) == 1


# ==============================================================================
# Acceptance: pending < latest gives failed record with detail starting "tx build:"
# ==============================================================================
def test_pending_less_than_latest_recorded_as_failed_with_detail() -> None:
    # pending nonce is behind latest nonce -> node inconsistent
    rpc = make_mock_rpc(pending_nonce=2, latest_nonce=3)
    builder = AaveTxBuilder(rpc, TEST_WALLET, clock=lambda: FIXED_NOW_DT)
    signer = FakeSigner()
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=builder,
    )

    order = make_valid_order()
    record = gw.propose(order)

    assert record["outcome"] == "failed"
    assert record["execution"] is not None
    assert record["execution"]["status"] == "failed"
    assert record["execution"]["error_code"] == "SIMULATION_FAILED"
    assert str(record["execution"]["detail"]).startswith("tx build:")
    assert "pending < latest" in str(record["execution"]["detail"])
    assert signer.calls == []
    assert len(sink.records) == 1


# ==============================================================================
# Acceptance: estimate failure gives failed record with detail starting "tx build:"
# ==============================================================================
def test_estimate_failure_recorded_as_failed_with_detail() -> None:
    rpc = make_mock_rpc(estimate_gas=RuntimeError("execution reverted: out of gas"))
    builder = AaveTxBuilder(
        rpc,
        TEST_WALLET,
        allow_estimate_fallback=False,
        clock=lambda: FIXED_NOW_DT,
    )
    signer = FakeSigner()
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=builder,
    )

    order = make_valid_order()
    record = gw.propose(order)

    assert record["outcome"] == "failed"
    assert record["execution"] is not None
    assert record["execution"]["status"] == "failed"
    assert record["execution"]["error_code"] == "SIMULATION_FAILED"
    assert str(record["execution"]["detail"]).startswith("tx build:")
    assert "gas estimate failed" in str(record["execution"]["detail"])
    assert signer.calls == []
    assert len(sink.records) == 1


# ==============================================================================
# Design Section 10 Named Test: test_builder_error_recorded_failed_with_detail
# ==============================================================================
def test_builder_error_recorded_failed_with_detail() -> None:
    def failing_builder(order: dict[str, Any]) -> list[dict[str, Any]]:
        raise TxBuildError("custom builder failure reason")

    signer = FakeSigner()
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=failing_builder,
    )

    order = make_valid_order()
    record = gw.propose(order)

    assert record["outcome"] == "failed"
    assert record["execution"] is not None
    assert record["execution"]["status"] == "failed"
    assert record["execution"]["error_code"] == "SIMULATION_FAILED"
    assert str(record["execution"]["detail"]).startswith("tx build:")
    assert "custom builder failure reason" in str(record["execution"]["detail"])
    assert signer.calls == []
    assert len(sink.records) == 1
    assert sink.records[0]["order_id"] == order["order_id"]


# ==============================================================================
# Acceptance: two-call builder yields nonces n and n+1 and passes simulate_bundle
# ==============================================================================
def test_two_call_builder_yields_nonces_and_passes_simulate_bundle() -> None:
    # Pending nonce is 42. Builder outputs 2 calls, nonces must be 42 and 43.
    rpc = make_mock_rpc(pending_nonce=42, latest_nonce=42)

    def custom_two_calls(order: dict[str, Any], wallet: str) -> list[Call]:
        return [
            Call(to="0x" + "11" * 20, data="0x01", value=0),
            Call(to="0x" + "22" * 20, data="0x02", value=0),
        ]

    builder = AaveTxBuilder(
        rpc,
        TEST_WALLET,
        clock=lambda: FIXED_NOW_DT,
        build_calls_fn=custom_two_calls,
    )
    sim = BundleSimulator(ok=True)
    signer = FakeSigner()
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=sim,
        signer=signer,
        audit_sink=sink,
        tx_builder=builder,
    )

    order = make_valid_order()
    record = gw.propose(order)

    assert record["outcome"] == "executed"
    assert len(sim.bundles) == 1
    bundle_calls = sim.bundles[0]
    assert len(bundle_calls) == 2
    assert bundle_calls[0]["nonce"] == 42
    assert bundle_calls[1]["nonce"] == 43

    assert len(signer.calls) == 2
    assert signer.calls[0]["nonce"] == 42
    assert signer.calls[1]["nonce"] == 43


# ==============================================================================
# Acceptance: no gas fallback in live mode
# ==============================================================================
def test_no_gas_fallback_in_live_mode() -> None:
    rpc = make_mock_rpc(estimate_gas=RuntimeError("estimate failed on live node"))

    # Live mode: allow_estimate_fallback=False (default)
    live_builder = AaveTxBuilder(
        rpc,
        TEST_WALLET,
        allow_estimate_fallback=False,
        clock=lambda: FIXED_NOW_DT,
    )
    with pytest.raises(TxBuildError, match="gas estimate failed"):
        live_builder(make_valid_order())

    # Dry-run mode: allow_estimate_fallback=True
    dry_builder = AaveTxBuilder(
        rpc,
        TEST_WALLET,
        allow_estimate_fallback=True,
        clock=lambda: FIXED_NOW_DT,
    )
    txs = dry_builder(make_valid_order())
    assert len(txs) == 1
    # Fallback estimate 400_000 * 13 // 10 = 520_000
    assert txs[0]["gas"] == 520_000


# ==============================================================================
# Additional verification: insufficient gas funds
# ==============================================================================
def test_insufficient_gas_funds_recorded_as_failed() -> None:
    # balance is 0 wei, insufficient for gas
    rpc = make_mock_rpc(balance=0)
    builder = AaveTxBuilder(rpc, TEST_WALLET, clock=lambda: FIXED_NOW_DT)
    signer = FakeSigner()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=builder,
    )

    order = make_valid_order()
    record = gw.propose(order)

    assert record["outcome"] == "failed"
    assert "insufficient gas funds" in str(record["execution"]["detail"])
    assert signer.calls == []


# ==============================================================================
# Additional verification: chain ID mismatch
# ==============================================================================
def test_chain_id_mismatch_recorded_as_failed() -> None:
    # Node reports chain_id 1 (mainnet) instead of 84532 (Base Sepolia)
    rpc = make_mock_rpc(chain_id=1)
    builder = AaveTxBuilder(rpc, TEST_WALLET, clock=lambda: FIXED_NOW_DT)
    signer = FakeSigner()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=builder,
    )

    order = make_valid_order()
    record = gw.propose(order)

    assert record["outcome"] == "failed"
    assert "chain_id mismatch" in str(record["execution"]["detail"])
    assert signer.calls == []


# ==============================================================================
# Acceptance: the gateway no longer touches tier_view._store
# ==============================================================================
def test_gateway_no_longer_touches_tier_view_store() -> None:
    class StoreLessTierView:
        """A TierView implementation without any _store attribute."""

        def current(self) -> str:
            return "T2"

        def is_stopped(self) -> bool:
            return False

        def approval_status(self, order_id: str) -> str | None:
            return "approved"

        def get_approval(self, order_id: str) -> dict[str, Any] | None:
            return {
                "order_id": order_id,
                "status": "approved",
                "requested_at": "2026-10-14T00:00:00Z",
                "decided_at": "2026-10-14T00:01:00Z",
                "decided_by": "owner",
            }

        def __getattr__(self, name: str) -> Any:
            if name == "_store":
                raise AttributeError("Gateway must NEVER access _store!")
            raise AttributeError(name)

    view = StoreLessTierView()
    rpc = make_mock_rpc()
    builder = AaveTxBuilder(rpc, TEST_WALLET, clock=lambda: FIXED_NOW_DT)
    signer = FakeSigner()
    gw = Gateway(
        policy=AllowPolicy(),
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=builder,
        tier_view=view,
    )

    order_id = str(uuid.uuid4())
    status, decided_at, decided_by = gw._get_approval_info(order_id)
    assert status == "approved"
    assert decided_at == "2026-10-14T00:01:00Z"
    assert decided_by == "owner"


# ==============================================================================
# TierView.get_approval public method test
# ==============================================================================
def test_tier_view_get_approval() -> None:
    store = TierStore(":memory:")
    view = TierView(store)

    assert view.get_approval("nonexistent") is None

    order_id = str(uuid.uuid4())
    store.insert_request(order_id, "2026-10-14T00:00:00Z")
    rec = view.get_approval(order_id)
    assert rec is not None
    assert rec["order_id"] == order_id
    assert rec["status"] == "pending"
    assert rec["requested_at"] == "2026-10-14T00:00:00Z"
    assert rec["decided_at"] is None
    assert rec["decided_by"] is None

    store.resolve_request(order_id, "approved", "2026-10-14T00:05:00Z", decided_by="owner")
    resolved = view.get_approval(order_id)
    assert resolved is not None
    assert resolved["status"] == "approved"
    assert resolved["decided_at"] == "2026-10-14T00:05:00Z"
    assert resolved["decided_by"] == "owner"
    assert view.approval_status(order_id) == "approved"
