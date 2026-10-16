"""Tests for Aave Stata (ERC-4626) protocol adapter and Aave v3 USDT integration (ticket D1)."""

from __future__ import annotations

import uuid
from typing import Any

import eth_abi
import pytest

from core.gateway import Gateway
from core.gateway.audit import InMemoryAuditSink
from core.gateway.txbuilder import build_calls as gateway_build_calls
from core.policy.engine import evaluate
from core.protocols import registry
from core.protocols.aave import (
    AAVE_V3_A_USDC,
    AAVE_V3_A_USDT,
    AAVE_V3_FAUCET,
    AAVE_V3_POOL,
    AAVE_V3_USDC,
    AAVE_V3_USDT,
)
from core.protocols.aave_stata import (
    AAVE_STATA_USDC,
    AAVE_STATA_USDT,
    APPROVE_SELECTOR,
    CONVERT_TO_ASSETS_SELECTOR,
    DEPOSIT_SELECTOR,
    REDEEM_SELECTOR,
    WITHDRAW_SELECTOR,
    OrderValidationError,
    build_approve_calldata,
    build_convert_to_assets_calldata,
    build_deposit_calldata,
    build_redeem_calldata,
    validate_order,
)
from core.protocols.aave_stata import (
    build_withdraw_calldata as stata_build_withdraw_calldata,
)
from core.protocols.registry import BuildContext
from core.signer import FakeSigner

WALLET = "0xc18afDC2dF55772bA3c031Df98305F02a3a36493"


def make_order(**over: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-stata",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "self",
    }
    base.update(over)
    return base


# =============================================================================
# 1. Calldata Encoder Vectors
# =============================================================================


def test_approve_calldata_vector():
    calldata = build_approve_calldata(AAVE_STATA_USDC, 25_000_000)
    assert calldata.startswith(APPROVE_SELECTOR)
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(calldata[10:]))
    assert spender.lower() == AAVE_STATA_USDC.lower()
    assert amount == 25_000_000


def test_deposit_calldata_vector():
    calldata = build_deposit_calldata(50_000_000, WALLET)
    assert calldata.startswith(DEPOSIT_SELECTOR)
    assets, receiver = eth_abi.decode(["uint256", "address"], bytes.fromhex(calldata[10:]))
    assert assets == 50_000_000
    assert receiver.lower() == WALLET.lower()


def test_withdraw_calldata_vector():
    calldata = stata_build_withdraw_calldata(30_000_000, WALLET, WALLET)
    assert calldata.startswith(WITHDRAW_SELECTOR)
    assets, receiver, owner = eth_abi.decode(
        ["uint256", "address", "address"], bytes.fromhex(calldata[10:])
    )
    assert assets == 30_000_000
    assert receiver.lower() == WALLET.lower()
    assert owner.lower() == WALLET.lower()


def test_redeem_calldata_vector():
    calldata = build_redeem_calldata(15_000_000, WALLET, WALLET)
    assert calldata.startswith(REDEEM_SELECTOR)
    shares, receiver, owner = eth_abi.decode(
        ["uint256", "address", "address"], bytes.fromhex(calldata[10:])
    )
    assert shares == 15_000_000
    assert receiver.lower() == WALLET.lower()
    assert owner.lower() == WALLET.lower()


def test_convert_to_assets_calldata_vector():
    calldata = build_convert_to_assets_calldata(1_000_000)
    assert calldata.startswith(CONVERT_TO_ASSETS_SELECTOR)
    (shares,) = eth_abi.decode(["uint256"], bytes.fromhex(calldata[10:]))
    assert shares == 1_000_000


def test_encoder_non_positive_amounts_raise():
    with pytest.raises(ValueError, match="positive"):
        build_approve_calldata(AAVE_STATA_USDC, 0)
    with pytest.raises(ValueError, match="positive"):
        build_approve_calldata(AAVE_STATA_USDC, -5)
    with pytest.raises(ValueError, match="positive"):
        build_deposit_calldata(0, WALLET)
    with pytest.raises(ValueError, match="positive"):
        stata_build_withdraw_calldata(0, WALLET, WALLET)
    with pytest.raises(ValueError, match="positive"):
        build_redeem_calldata(0, WALLET, WALLET)
    with pytest.raises(ValueError, match="non-negative"):
        build_convert_to_assets_calldata(-1)


# =============================================================================
# 2. Registry Registration & Spec Properties
# =============================================================================


def test_aave_stata_registered():
    spec = registry.get("aave-stata")
    assert spec is not None
    assert spec.id == "aave-stata"
    assert spec.name == "Aave Stata"
    assert spec.category == "vault"
    assert spec.chain_id == 84532
    assert not spec.paper
    assert spec.tokens == frozenset({"USDC", "USDT"})
    assert spec.functions == frozenset({"approve", "supply", "withdraw"})
    assert spec.spenders == frozenset({AAVE_STATA_USDC, AAVE_STATA_USDT})
    assert spec.recipients == frozenset({"self"})
    assert spec.owner_functions == frozenset()

    # Category query finds aave-stata
    vault_specs = registry.by_category("vault")
    assert any(s.id == "aave-stata" for s in vault_specs)


def test_aave_stata_describe():
    spec = registry.get("aave-stata")
    assert "Deposit USDC" in spec.describe(make_order(function="supply", token="USDC"))
    assert "Withdraw USDT" in spec.describe(make_order(function="withdraw", token="USDT"))
    assert "Approve USDC" in spec.describe(make_order(function="approve", token="USDC"))


# =============================================================================
# 3. Build Calls for Aave v3 with USDT
# =============================================================================


def test_aave_v3_usdt_approve():
    spec = registry.get("aave-v3")
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "approve",
        "token": "USDT",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "aave_pool",
    }
    calls = spec.build_calls(order, BuildContext(WALLET))
    assert len(calls) == 1
    call = calls[0]
    assert call.to == AAVE_V3_USDT
    assert call.value == 0
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(call.data[10:]))
    assert spender.lower() == AAVE_V3_POOL.lower()
    assert amount == 10_000_000


def test_aave_v3_usdt_supply():
    spec = registry.get("aave-v3")
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDT",
        "amount": {"value": "20000000", "decimals": 6},
        "recipient": "self",
    }
    calls = spec.build_calls(order, BuildContext(WALLET))
    assert len(calls) == 1
    call = calls[0]
    assert call.to == AAVE_V3_POOL
    asset, amount, on_behalf_of, ref = eth_abi.decode(
        ["address", "uint256", "address", "uint16"], bytes.fromhex(call.data[10:])
    )
    assert asset.lower() == AAVE_V3_USDT.lower()
    assert amount == 20_000_000
    assert on_behalf_of.lower() == WALLET.lower()
    assert ref == 0


def test_aave_v3_usdt_withdraw():
    spec = registry.get("aave-v3")
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "withdraw",
        "token": "USDT",
        "amount": {"value": "5000000", "decimals": 6},
        "recipient": "self",
    }
    calls = spec.build_calls(order, BuildContext(WALLET))
    assert len(calls) == 1
    call = calls[0]
    assert call.to == AAVE_V3_POOL
    asset, amount, to = eth_abi.decode(
        ["address", "uint256", "address"], bytes.fromhex(call.data[10:])
    )
    assert asset.lower() == AAVE_V3_USDT.lower()
    assert amount == 5_000_000
    assert to.lower() == WALLET.lower()


def test_aave_v3_usdt_mint():
    spec = registry.get("aave-v3")
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "owner",
        "protocol": "aave-v3",
        "function": "mint",
        "token": "USDT",
        "amount": {"value": "100000000", "decimals": 6},
        "recipient": "self",
    }
    calls = spec.build_calls(order, BuildContext(WALLET))
    assert len(calls) == 1
    call = calls[0]
    assert call.to == AAVE_V3_FAUCET
    token, to, amount = eth_abi.decode(
        ["address", "address", "uint256"], bytes.fromhex(call.data[10:])
    )
    assert token.lower() == AAVE_V3_USDT.lower()
    assert to.lower() == WALLET.lower()
    assert amount == 100_000_000


# =============================================================================
# 4. Build Calls for Aave Stata (USDC and USDT)
# =============================================================================


def test_aave_stata_usdc_calls_through_gateway():
    # Approve
    order_app = make_order(function="approve", token="USDC")
    calls_app = gateway_build_calls(order_app, WALLET)
    assert len(calls_app) == 1
    assert calls_app[0].to == AAVE_V3_USDC
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(calls_app[0].data[10:]))
    assert spender.lower() == AAVE_STATA_USDC.lower()
    assert amount == 10_000_000

    # Supply (calls deposit)
    order_sup = make_order(function="supply", token="USDC")
    calls_sup = gateway_build_calls(order_sup, WALLET)
    assert len(calls_sup) == 1
    assert calls_sup[0].to == AAVE_STATA_USDC
    assets, receiver = eth_abi.decode(["uint256", "address"], bytes.fromhex(calls_sup[0].data[10:]))
    assert assets == 10_000_000
    assert receiver.lower() == WALLET.lower()

    # Withdraw
    order_wd = make_order(function="withdraw", token="USDC")
    calls_wd = gateway_build_calls(order_wd, WALLET)
    assert len(calls_wd) == 1
    assert calls_wd[0].to == AAVE_STATA_USDC
    assets, receiver, owner = eth_abi.decode(
        ["uint256", "address", "address"], bytes.fromhex(calls_wd[0].data[10:])
    )
    assert assets == 10_000_000
    assert receiver.lower() == WALLET.lower()
    assert owner.lower() == WALLET.lower()


def test_aave_stata_usdt_calls_through_gateway():
    # Approve
    order_app = make_order(function="approve", token="USDT")
    calls_app = gateway_build_calls(order_app, WALLET)
    assert len(calls_app) == 1
    assert calls_app[0].to == AAVE_V3_USDT
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(calls_app[0].data[10:]))
    assert spender.lower() == AAVE_STATA_USDT.lower()
    assert amount == 10_000_000

    # Supply (calls deposit)
    order_sup = make_order(function="supply", token="USDT")
    calls_sup = gateway_build_calls(order_sup, WALLET)
    assert len(calls_sup) == 1
    assert calls_sup[0].to == AAVE_STATA_USDT
    assets, receiver = eth_abi.decode(["uint256", "address"], bytes.fromhex(calls_sup[0].data[10:]))
    assert assets == 10_000_000
    assert receiver.lower() == WALLET.lower()

    # Withdraw
    order_wd = make_order(function="withdraw", token="USDT")
    calls_wd = gateway_build_calls(order_wd, WALLET)
    assert len(calls_wd) == 1
    assert calls_wd[0].to == AAVE_STATA_USDT
    assets, receiver, owner = eth_abi.decode(
        ["uint256", "address", "address"], bytes.fromhex(calls_wd[0].data[10:])
    )
    assert assets == 10_000_000
    assert receiver.lower() == WALLET.lower()
    assert owner.lower() == WALLET.lower()


def test_approvals_name_only_declared_spenders_and_exact_amount():
    spec = registry.get("aave-stata")
    assert spec is not None
    order = make_order(function="approve", token="USDC", amount={"value": "7777777", "decimals": 6})
    (call,) = spec.build_calls(order, BuildContext(WALLET))
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(call.data[10:]))
    assert spender.lower() in {s.lower() for s in spec.spenders}
    assert amount == 7_777_777


# =============================================================================
# 5. Disabled Protocol Denial Tests
# =============================================================================


class MockDenyPolicy:
    ips_hash = "0" * 64

    def evaluate(self, o):
        return {
            "schema_version": 1,
            "order_id": o["order_id"],
            "decision": "deny",
            "rules_hit": [
                {
                    "code": "ACCESS.PROTOCOL_NOT_ALLOWED",
                    "evidence": {"protocol": o["protocol"]},
                }
            ],
            "reason": "protocol not allowed by IPS",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T09:00:00Z",
        }


def test_gateway_denies_when_protocol_not_allowed_by_policy():
    signer = FakeSigner()
    gw = Gateway(
        policy=MockDenyPolicy(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=gateway_build_calls,
    )
    order = make_order(protocol="aave-stata")
    rec = gw.propose(order)
    assert rec["outcome"] == "denied"
    assert rec["decision"]["decision"] == "deny"
    assert rec["decision"]["rules_hit"][0]["code"] == "ACCESS.PROTOCOL_NOT_ALLOWED"
    assert signer.calls == []


def test_policy_engine_denies_aave_stata_when_ips_disables_it():
    # IPS fixture enabling only aave-v3

    ips = {
        "schema_version": 1,
        "portfolio_id": "00000000-0000-0000-0000-000000000001",
        "created_at": "2026-10-16T09:00:00Z",
        "updated_at": "2026-10-16T09:00:00Z",
        "allowed_protocols": ["aave-v3"],
        "allowed_tokens": ["USDC", "USDT"],
        "allowed_functions": ["approve", "supply", "withdraw"],
        "allowed_recipients": ["self", "aave_pool"],
        "limits": {
            "per_tx_usd": "1000.00",
            "per_day_usd": "5000.00",
            "tx_per_hour": 10,
        },
        "target_allocation": {"USDC": 10000},
        "rebalance_threshold_bps": 500,
    }
    state = {
        "schema_version": 1,
        "as_of": "2026-10-16T09:00:00Z",
        "price_source": "mainnet_reference",
        "positions": [
            {
                "token": "USDC",
                "location": "wallet",
                "balance": {"value": "100000000", "decimals": 6},
                "price_usd": "1.00",
                "value_usd": "100.00",
                "weight_bps": 10000,
            }
        ],
        "total_value_usd": "100.00",
        "spent_24h_usd": "0.00",
        "tx_count_1h": 0,
    }
    order = make_order(protocol="aave-stata")
    decision = evaluate(ips, state, order=order)
    assert decision["decision"] == "deny"
    assert any(h["code"] == "ACCESS.PROTOCOL_NOT_ALLOWED" for h in decision["rules_hit"])


# =============================================================================
# 6. Positions with Fake Reader
# =============================================================================


class FakeReader:
    def __init__(
        self,
        balances: dict[str, int] | None = None,
        conversions: dict[tuple[str, int], int] | None = None,
    ):
        self.balances = {k.lower(): v for k, v in (balances or {}).items()}
        self.conversions = conversions or {}

    def balance_of(self, token_address: str) -> int:
        return self.balances.get(token_address.lower(), 0)

    def address_for(self, key: str, default: str) -> str:
        return default

    def call(self, to: str, data: str) -> str:
        if data.startswith(CONVERT_TO_ASSETS_SELECTOR):
            (shares,) = eth_abi.decode(["uint256"], bytes.fromhex(data[10:]))
            assets = self.conversions.get((to.lower(), shares), shares)
            return "0x" + eth_abi.encode(["uint256"], [assets]).hex()
        return "0x0"

    def convert_to_assets(self, vault_address: str, shares: int) -> int:
        return self.conversions.get((vault_address.lower(), shares), shares)


def test_aave_stata_positions_with_shares():
    spec = registry.get("aave-stata")
    assert spec is not None

    reader = FakeReader(
        balances={
            AAVE_STATA_USDC: 800_000,
            AAVE_STATA_USDT: 500_000,
        },
        conversions={
            (AAVE_STATA_USDC.lower(), 800_000): 1_000_000,
            (AAVE_STATA_USDT.lower(), 500_000): 600_000,
        },
    )
    legs = spec.positions(WALLET, reader)
    assert len(legs) == 2

    usdc_leg = next(leg for leg in legs if leg.token == "USDC")
    assert usdc_leg.location == "aave-stata"
    assert usdc_leg.balance == 1_000_000
    assert usdc_leg.decimals == 6

    usdt_leg = next(leg for leg in legs if leg.token == "USDT")
    assert usdt_leg.location == "aave-stata"
    assert usdt_leg.balance == 600_000
    assert usdt_leg.decimals == 6


def test_aave_stata_positions_empty_when_no_shares():
    spec = registry.get("aave-stata")
    assert spec is not None

    reader = FakeReader(balances={AAVE_STATA_USDC: 0, AAVE_STATA_USDT: 0})
    legs = spec.positions(WALLET, reader)
    assert legs == []


def test_aave_v3_positions_with_ausdt():
    spec = registry.get("aave-v3")
    assert spec is not None

    reader = FakeReader(
        balances={
            AAVE_V3_A_USDC: 500_000_000,
            AAVE_V3_A_USDT: 200_000_000,
        }
    )
    legs = spec.positions(WALLET, reader)
    assert len(legs) == 2
    ausdc_leg = next(leg for leg in legs if leg.token == "USDC")
    assert ausdc_leg.balance == 500_000_000
    assert ausdc_leg.location == "aave-v3"
    ausdt_leg = next(leg for leg in legs if leg.token == "USDT")
    assert ausdt_leg.balance == 200_000_000
    assert ausdt_leg.location == "aave-v3"


# =============================================================================
# 7. Order Validation Bounds & Edge Cases
# =============================================================================


def test_order_validation_invalid_token():
    with pytest.raises(OrderValidationError):
        validate_order(
            make_order(token="ETH", amount={"value": "1000000000000000000", "decimals": 18})
        )
    with pytest.raises(OrderValidationError):
        validate_order(
            make_order(token="WETH", amount={"value": "1000000000000000000", "decimals": 18})
        )


def test_order_validation_invalid_function():
    with pytest.raises(OrderValidationError):
        validate_order(make_order(function="mint"))
    with pytest.raises(OrderValidationError):
        validate_order(make_order(function="transfer"))


def test_order_validation_invalid_protocol():
    with pytest.raises(OrderValidationError):
        validate_order(make_order(protocol="aave-v3"))


def test_order_validation_invalid_recipient():
    with pytest.raises(OrderValidationError):
        validate_order(make_order(recipient="owner"))
    with pytest.raises(OrderValidationError):
        validate_order(make_order(recipient="aave_pool"))
