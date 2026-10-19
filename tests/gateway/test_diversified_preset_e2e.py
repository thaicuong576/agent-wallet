"""End-to-end gateway tests with fake RPC and fake signer under diversified
and conservative IPS presets.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.audit.verify import verify_chain
from core.gateway import Gateway, InMemoryAuditSink, InvalidOrder
from core.gateway.policy import EnginePolicy
from core.gateway.txbuilder import AaveTxBuilder
from core.policy.ips import load_ips
from core.protocols.uniswap_v3 import QUOTER_V2
from core.signer import FakeSigner
from core.simulate import SimulationResult

PRESETS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "presets"
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples"
TEST_WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"
FIXED_NOW_TS = 1700000000
FIXED_NOW_DT = datetime.fromtimestamp(FIXED_NOW_TS, tz=UTC)


def make_mock_rpc(oracle_prices: dict[str, int] | None = None) -> Callable[[str, list[Any]], Any]:
    """Fake RPC handling chainId, block freshness, nonces, balances, and Aave oracle price reads."""
    prices = oracle_prices or {}

    def rpc(method: str, params: list[Any]) -> Any:
        if method == "eth_chainId":
            return hex(84532)
        if method == "eth_getBlockByNumber":
            return {
                "timestamp": hex(FIXED_NOW_TS),
                "baseFeePerGas": hex(10_000_000_000),
            }
        if method == "eth_getTransactionCount":
            return hex(1)
        if method == "eth_estimateGas":
            return hex(100_000)
        if method == "eth_getBalance":
            return hex(10**18)
        if method == "eth_call":
            call = params[0]
            data = call.get("data", "")
            if call.get("to", "").lower() == QUOTER_V2.lower():
                # QuoterV2.quoteExactInputSingle for USDC -> WBTC: 0.5% below the oracle value.
                amount_in = int(data[10 + 128 : 10 + 192], 16)
                wbtc_price = prices.get("WBTC", 80_000 * 10**8)
                out = amount_in * 10**8 * 10**8 // (wbtc_price * 10**6) * 995 // 1000
                return "0x" + out.to_bytes(32, "big").hex() + "00" * 96
            if data.startswith("0xb3596f07"):  # AaveOracle.getAssetPrice(address)
                asset = "0x" + data[10:][24:64].lower()
                # WBTC address on Base Sepolia
                for sym, addr in [("WBTC", "0x54114591963cf60ef3aa63befd6ec263d98145a4")]:
                    if asset == addr.lower():
                        price = prices.get(sym, 80_000 * 10**8)
                        return "0x" + price.to_bytes(32, "big").hex()
            raise ValueError(f"Unexpected eth_call: {call}")
        raise ValueError(f"Unexpected RPC call: {method} {params}")

    return rpc


class MockSimulator:
    """Fake simulator approving single transactions and transaction bundles."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        self.calls.append(tx)
        return SimulationResult(ok=True, error=None, gas_used=100_000, raw={})

    def simulate_bundle(self, txs: list[dict[str, Any]]) -> SimulationResult:
        self.calls.extend(txs)
        return SimulationResult(ok=True, error=None, gas_used=150_000, raw={})


def make_portfolio_state() -> dict[str, Any]:
    """Portfolio state containing balances and USD prices for all preset tokens with
    compliant weights.
    """
    return {
        "schema_version": 1,
        "as_of": "2026-10-16T08:59:00Z",
        "price_source": "mainnet_reference",
        "positions": [
            {
                "token": "USDC",
                "location": "wallet",
                "balance": {"value": "7000000000", "decimals": 6},
                "price_usd": "1.00",
                "value_usd": "7000.00",
                "weight_bps": 7000,
            },
            {
                "token": "USDT",
                "location": "wallet",
                "balance": {"value": "1000000000", "decimals": 6},
                "price_usd": "1.00",
                "value_usd": "1000.00",
                "weight_bps": 1000,
            },
            {
                "token": "WETH",
                "location": "wallet",
                "balance": {"value": "400000000000000000", "decimals": 18},
                "price_usd": "2500.00",
                "value_usd": "1000.00",
                "weight_bps": 1000,
            },
            {
                "token": "WBTC",
                "location": "wallet",
                "balance": {"value": "1250000", "decimals": 8},
                "price_usd": "80000.00",
                "value_usd": "1000.00",
                "weight_bps": 1000,
            },
        ],
        "total_value_usd": "10000.00",
        "spent_24h_usd": "0.00",
        "tx_count_1h": 0,
    }


def make_risk_inputs(protocol: str = "aave-v3", token: str = "USDC") -> dict[str, Any]:
    """Reference risk inputs meeting all safety criteria."""
    with open(EXAMPLES_DIR / "risk-inputs.valid.json", encoding="utf-8") as f:
        risk = json.load(f)
    risk["protocol"] = protocol
    risk["token"] = token
    risk["pool_tvl_usd"] = "120000000.00"
    risk["stablecoin_depeg_bps"] = 5
    risk["estimated_slippage_bps"] = 20
    risk["data_age_seconds"] = 60
    return risk


def test_diversified_preset_e2e_all_protocols_allowed_and_executed() -> None:
    """Under diversified IPS, all 4 protocols execute through gateway, sign,
    and verify audit chain.
    """
    ips = load_ips(PRESETS_DIR / "diversified.json")
    state = make_portfolio_state()
    risk = make_risk_inputs()

    rpc = make_mock_rpc()
    tx_builder = AaveTxBuilder(rpc, TEST_WALLET, clock=FIXED_NOW_TS)
    sink = InMemoryAuditSink()
    signer = FakeSigner()
    simulator = MockSimulator()
    policy = EnginePolicy(ips, lambda: state, lambda: risk, clock=FIXED_NOW_TS)

    gw = Gateway(
        policy=policy,
        simulator=simulator,
        signer=signer,
        audit_sink=sink,
        tx_builder=tx_builder,
        clock=FIXED_NOW_TS,
    )

    orders = [
        # 1. aave-v3 USDT approve & supply
        {
            "protocol": "aave-v3",
            "function": "approve",
            "token": "USDT",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "aave_pool",
        },
        {
            "protocol": "aave-v3",
            "function": "supply",
            "token": "USDT",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "self",
        },
        # 2. aave-stata USDC approve & supply
        {
            "protocol": "aave-stata",
            "function": "approve",
            "token": "USDC",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "self",
        },
        {
            "protocol": "aave-stata",
            "function": "supply",
            "token": "USDC",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "self",
        },
        # 3. morpho-blue approve & supply for both markets
        {
            "protocol": "morpho-blue",
            "function": "approve",
            "token": "USDC",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "self",
        },
        {
            "protocol": "morpho-blue",
            "function": "supply",
            "token": "USDC",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "self",
            "market": "usdc-wbtc",
        },
        {
            "protocol": "morpho-blue",
            "function": "supply",
            "token": "USDC",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "self",
            "market": "usdc-cbeth",
        },
        # 4. uniswap-v3 swap (USDC -> WBTC)
        {
            "protocol": "uniswap-v3",
            "function": "exactInputSingle",
            "token": "USDC",
            "token_out": "WBTC",
            "amount": {"value": "10000000", "decimals": 6},
            "recipient": "self",
            "max_slippage_bps": 50,
        },
    ]

    for o in orders:
        full_order = {
            "schema_version": 1,
            "order_id": str(uuid.uuid4()),
            "created_at": "2026-10-16T09:00:00Z",
            "proposed_by": "planner",
            **o,
        }
        rec = gw.propose(full_order)
        assert rec["outcome"] == "executed"
        assert rec["decision"]["decision"] == "allow"
        assert rec["execution"] is not None
        assert rec["execution"]["status"] == "executed"
        assert rec["execution"]["tx_hash"] is not None

    # Signer received calls for all executed orders
    assert len(signer.calls) == len(orders)

    # Audit chain integrity verification
    report = verify_chain(sink)
    assert report.valid is True
    assert report.count == len(orders)


def test_diversified_preset_escalation_above_threshold() -> None:
    """Order with amount exceeding escalate_above_usd threshold escalates for owner approval."""
    with open(PRESETS_DIR / "diversified.json", encoding="utf-8") as f:
        ips_data = json.load(f)
    # Lower escalate threshold to $30 while keeping per_tx_usd limit at $100
    ips_data["approval"]["escalate_above_usd"] = "30"
    from core.policy.ips import LoadedIps, compute_ips_hash

    ips = LoadedIps(ips_data, compute_ips_hash(ips_data))

    state = make_portfolio_state()
    risk = make_risk_inputs()

    rpc = make_mock_rpc()
    tx_builder = AaveTxBuilder(rpc, TEST_WALLET, clock=FIXED_NOW_TS)
    sink = InMemoryAuditSink()
    signer = FakeSigner()
    policy = EnginePolicy(ips, lambda: state, lambda: risk, clock=FIXED_NOW_TS)

    gw = Gateway(
        policy=policy,
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=tx_builder,
        clock=FIXED_NOW_TS,
    )

    # $50 order exceeds $30 escalate threshold
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-stata",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "50000000", "decimals": 6},
        "recipient": "self",
    }
    rec = gw.propose(order)
    assert rec["outcome"] == "pending_approval"
    assert rec["decision"]["decision"] == "escalate"
    assert any(h["code"] == "TIER.APPROVAL_REQUIRED" for h in rec["decision"]["rules_hit"])
    assert len(signer.calls) == 0

    report = verify_chain(sink)
    assert report.valid is True
    assert report.count == 1


def test_conservative_preset_denies_new_protocols_and_tokens() -> None:
    """Under conservative preset, the same new protocols and tokens are denied."""
    ips = load_ips(PRESETS_DIR / "conservative.json")
    state = make_portfolio_state()
    risk = make_risk_inputs()

    rpc = make_mock_rpc()
    tx_builder = AaveTxBuilder(rpc, TEST_WALLET, clock=FIXED_NOW_TS)
    sink = InMemoryAuditSink()
    signer = FakeSigner()
    policy = EnginePolicy(ips, lambda: state, lambda: risk, clock=FIXED_NOW_TS)

    gw = Gateway(
        policy=policy,
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=tx_builder,
        clock=FIXED_NOW_TS,
    )

    # 1. aave-stata USDC supply: ACCESS.PROTOCOL_NOT_ALLOWED
    order_stata = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-stata",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "50000000", "decimals": 6},
        "recipient": "self",
    }
    rec_stata = gw.propose(order_stata)
    assert rec_stata["outcome"] == "denied"
    assert any(
        h["code"] == "ACCESS.PROTOCOL_NOT_ALLOWED" for h in rec_stata["decision"]["rules_hit"]
    )

    # 2. morpho-blue markets: ACCESS.PROTOCOL_NOT_ALLOWED
    for market in ("usdc-wbtc", "usdc-cbeth"):
        order_morpho = {
            "schema_version": 1,
            "order_id": str(uuid.uuid4()),
            "created_at": "2026-10-16T09:00:00Z",
            "proposed_by": "planner",
            "protocol": "morpho-blue",
            "function": "supply",
            "token": "USDC",
            "amount": {"value": "50000000", "decimals": 6},
            "recipient": "self",
            "market": market,
        }
        rec_morpho = gw.propose(order_morpho)
        assert rec_morpho["outcome"] == "denied"
        assert any(
            h["code"] == "ACCESS.PROTOCOL_NOT_ALLOWED" for h in rec_morpho["decision"]["rules_hit"]
        )

    # 3. aave-v3 USDT: ACCESS.TOKEN_NOT_ALLOWED
    order_usdt = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDT",
        "amount": {"value": "50000000", "decimals": 6},
        "recipient": "self",
    }
    rec_usdt = gw.propose(order_usdt)
    assert rec_usdt["outcome"] == "denied"
    assert any(h["code"] == "ACCESS.TOKEN_NOT_ALLOWED" for h in rec_usdt["decision"]["rules_hit"])

    # 4. uniswap-v3 USDC -> WBTC: ACCESS.TOKEN_NOT_ALLOWED (WBTC is not in conservative universe)
    order_uni = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "uniswap-v3",
        "function": "exactInputSingle",
        "token": "USDC",
        "token_out": "WBTC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "self",
        "max_slippage_bps": 50,
    }
    rec_uni = gw.propose(order_uni)
    assert rec_uni["outcome"] == "denied"
    assert any(h["code"] == "ACCESS.TOKEN_NOT_ALLOWED" for h in rec_uni["decision"]["rules_hit"])

    # Signer was never invoked for any denied order
    assert len(signer.calls) == 0

    # All denials are recorded in the audit sink and chain remains valid
    report = verify_chain(sink)
    assert report.valid is True
    assert report.count == 5


def test_diversified_preset_denials_negative_cases() -> None:
    """Negative cases: swap for pair not in adapter, morpho without market, and arbitrary
    approve spender are denied.
    """
    ips = load_ips(PRESETS_DIR / "diversified.json")
    state = make_portfolio_state()
    risk = make_risk_inputs()

    rpc = make_mock_rpc()
    tx_builder = AaveTxBuilder(rpc, TEST_WALLET, clock=FIXED_NOW_TS)
    sink = InMemoryAuditSink()
    signer = FakeSigner()
    policy = EnginePolicy(ips, lambda: state, lambda: risk, clock=FIXED_NOW_TS)

    gw = Gateway(
        policy=policy,
        simulator=MockSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=tx_builder,
        clock=FIXED_NOW_TS,
    )

    # 1. Swap for a pair not in adapter: token_out not in universe
    order_bad_swap = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "uniswap-v3",
        "function": "exactInputSingle",
        "token": "USDC",
        "token_out": "cbETH",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "self",
        "max_slippage_bps": 50,
    }
    rec_bad_swap = gw.propose(order_bad_swap)
    assert rec_bad_swap["outcome"] == "denied"
    assert any(
        h["code"] == "ACCESS.TOKEN_NOT_ALLOWED" for h in rec_bad_swap["decision"]["rules_hit"]
    )

    # 2. Swap for a pair with unwhitelisted adapter pair: USDT -> WBTC
    order_unsupported_pair = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "uniswap-v3",
        "function": "exactInputSingle",
        "token": "USDT",
        "token_out": "WBTC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "self",
        "max_slippage_bps": 50,
    }
    rec_unsupported_pair = gw.propose(order_unsupported_pair)
    assert rec_unsupported_pair["outcome"] == "failed"
    assert "Unsupported token" in str(rec_unsupported_pair["execution"]["detail"])

    # 3. Morpho Blue supply order without a market field
    order_no_market = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "morpho-blue",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "50000000", "decimals": 6},
        "recipient": "self",
    }
    rec_no_market = gw.propose(order_no_market)
    assert rec_no_market["outcome"] == "denied"
    assert any(
        h["code"] == "ACCESS.MARKET_NOT_ALLOWED" for h in rec_no_market["decision"]["rules_hit"]
    )

    # 4. Approve for an arbitrary recipient: policy evaluation check
    order_bad_spender = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "approve",
        "token": "USDC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "0x1234567890123456789012345678901234567890",
    }
    decision = policy.evaluate(order_bad_spender)
    assert decision["decision"] == "deny"
    assert any(h["code"] == "ACCESS.RECIPIENT_NOT_ALLOWED" for h in decision["rules_hit"])

    # Proposing arbitrary recipient fails order schema validation
    with pytest.raises(InvalidOrder):
        gw.propose(order_bad_spender)

    # Signer was never invoked for any denied/failed order
    assert len(signer.calls) == 0

    # Audit chain is still valid for all processed records
    report = verify_chain(sink)
    assert report.valid is True
