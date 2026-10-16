"""Tests for core.risk.router.RiskRouter (protocol routing, math, safety, fail-closed)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import eth_abi
import jsonschema
import pytest

from api.main import reference_risk_inputs
from core.gateway.policy import EnginePolicy
from core.protocols.morpho_blue import MARKET_SELECTOR, MORPHO_BLUE
from core.protocols.tokens import AAVE_ORACLE, GET_ASSET_PRICE_SELECTOR
from core.protocols.uniswap_v3 import FACTORY_ADDRESS, TOKEN_ADDRESSES
from core.risk import (
    SOURCES_MAPPING,
    RiskDataUnavailableError,
    RiskRouter,
    RpcCallError,
    UnsupportedProtocolError,
)
from core.risk.aave import GET_RESERVE_DATA_SELECTOR
from core.risk.router import GET_POOL_SELECTOR
from tests.risk.conftest import encode_aave_reserve_data

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)

SAMPLE_IPS: dict[str, Any] = {
    "schema_version": 1,
    "universe": {
        "tokens": ["USDC", "USDT", "WETH", "WBTC"],
        "protocols": ["aave-v3", "aave-stata", "morpho-blue", "uniswap-v3"],
        "functions": ["supply", "withdraw", "exactInputSingle"],
        "recipients": ["self"],
    },
    "limits": {
        "per_tx_usd": "1000000.00",
        "per_day_usd": "5000000.00",
        "max_tx_per_hour": 100,
    },
    "allocation": {
        "max_weight_per_asset_bps": 5000,
        "min_stable_weight_bps": 5000,
        "rebalance_band_bps": 0,
    },
    "risk": {
        "min_pool_tvl_usd": "10000.00",
        "max_reward_apy_share_bps": 2000,
        "max_apy_deviation_bps": 500,
        "max_depeg_bps": 200,
        "max_utilization_bps": 9000,  # 90.00% max utilization
        "max_slippage_bps": 100,
        "min_benefit_cost_ratio_bps": 1000,
        "max_data_age_seconds": 86400,
    },
    "approval": {
        "escalate_above_usd": "500000.00",
    },
}

EMPTY_STATE: dict[str, Any] = {
    "total_value_usd": "100000.00",
    "spent_24h_usd": "0.00",
    "tx_count_1h": 0,
    "positions": [
        {"token": "USDC", "value_usd": "100000.00", "price_usd": "1.00"},
    ],
}


def test_router_picks_right_provider_aave_v3(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """RiskRouter routes aave-v3 orders to Aave reserve data provider."""
    fake_hex = encode_aave_reserve_data(
        total_atoken=100_000_000_000,  # 100,000 USDC
        total_stable_debt=20_000_000_000,
        total_variable_debt=50_000_000_000,
        last_update_timestamp=int(NOW.timestamp()) - 100,
    )

    def fake_rpc(payload: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": fake_hex}

    router = RiskRouter(rpc_callable=fake_rpc)
    order = {
        "order_id": "ord-aave-1",
        "protocol": "aave-v3",
        "token": "USDC",
        "function": "supply",
        "amount": {"value": "1000000000", "decimals": 6},
        "recipient": "self",
    }

    res = router.get_risk_inputs(order, now=NOW)
    assert res["schema_version"] == 1
    assert res["protocol"] == "aave-v3"
    assert res["token"] == "USDC"
    assert res["utilization_bps"] == 7000
    assert res["pool_tvl_usd"] == "100000.00"
    assert res["sources"] == SOURCES_MAPPING
    risk_inputs_validator.validate(res)


def test_router_picks_right_provider_aave_v3_usdt(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """RiskRouter routes aave-v3 USDT orders using USDT reserve address from token registry."""
    fake_hex = encode_aave_reserve_data(
        total_atoken=250_000_000_000,  # 250,000 USDT
        total_stable_debt=0,
        total_variable_debt=125_000_000_000,  # 50% utilization
        last_update_timestamp=int(NOW.timestamp()) - 60,
    )

    def fake_rpc(payload: dict[str, Any]) -> dict[str, Any]:
        params = payload.get("params", [{}])[0]
        calldata = params.get("data", "")
        assert calldata.startswith(GET_RESERVE_DATA_SELECTOR)
        return {"result": fake_hex}

    router = RiskRouter(rpc_callable=fake_rpc)
    order = {
        "order_id": "ord-aave-usdt",
        "protocol": "aave-v3",
        "token": "USDT",
        "function": "supply",
        "amount": {"value": "500000000", "decimals": 6},
        "recipient": "self",
    }

    res = router.get_risk_inputs(order, now=NOW)
    assert res["protocol"] == "aave-v3"
    assert res["token"] == "USDT"
    assert res["utilization_bps"] == 5000
    assert res["pool_tvl_usd"] == "250000.00"
    risk_inputs_validator.validate(res)


def test_router_picks_right_provider_aave_stata(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """RiskRouter routes aave-stata orders to underlying token reserve on Aave V3."""
    fake_hex = encode_aave_reserve_data(
        total_atoken=500_000_000_000,
        total_stable_debt=50_000_000_000,
        total_variable_debt=150_000_000_000,  # 40% utilization
        last_update_timestamp=int(NOW.timestamp()) - 30,
    )

    def fake_rpc(payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    router = RiskRouter(rpc_callable=fake_rpc)
    order = {
        "order_id": "ord-stata-1",
        "protocol": "aave-stata",
        "token": "USDC",
        "function": "supply",
        "amount": {"value": "100000000", "decimals": 6},
        "recipient": "self",
    }

    res = router.get_risk_inputs(order, now=NOW)
    assert res["protocol"] == "aave-stata"
    assert res["token"] == "USDC"
    assert res["utilization_bps"] == 4000
    assert res["pool_tvl_usd"] == "500000.00"
    risk_inputs_validator.validate(res)


def test_morpho_utilization_math_94_percent_denies_and_70_percent_passes(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """Morpho cbETH market at ~94% utilization denies; WBTC at 70% passes."""
    # 1. cbETH market: 75,003 USDC supply, 70,180 USDC borrow = 9356 bps (~94%)
    cbeth_market_bytes = eth_abi.encode(
        ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
        [
            75_003_000_000,
            70_000_000_000,
            70_180_000_000,
            65_000_000_000,
            int(NOW.timestamp()) - 10,
            0,
        ],
    )

    def fake_rpc_cbeth(payload: dict[str, Any]) -> dict[str, Any]:
        params = payload.get("params", [{}])[0]
        assert params.get("to", "").lower() == MORPHO_BLUE.lower()
        calldata = params.get("data", "")
        assert calldata.startswith(MARKET_SELECTOR)
        return {"result": "0x" + cbeth_market_bytes.hex()}

    router_cbeth = RiskRouter(rpc_callable=fake_rpc_cbeth)
    order_cbeth = {
        "order_id": "ord-morpho-cbeth",
        "protocol": "morpho-blue",
        "token": "USDC",
        "market": "usdc-cbeth",
        "function": "supply",
        "amount": {"value": "1000000000", "decimals": 6},
        "recipient": "self",
    }

    res_cbeth = router_cbeth.get_risk_inputs(order_cbeth, now=NOW)
    assert res_cbeth["protocol"] == "morpho-blue"
    assert res_cbeth["utilization_bps"] == 9356  # ~94%
    assert res_cbeth["pool_tvl_usd"] == "75003.00"
    risk_inputs_validator.validate(res_cbeth)

    # Evaluate cbETH order under IPS with max_utilization_bps 9000 -> must deny
    policy_cbeth = EnginePolicy(
        ips=SAMPLE_IPS,
        get_state=lambda: EMPTY_STATE,
        get_risk_inputs=router_cbeth,
        clock=NOW,
    )
    decision_cbeth = policy_cbeth.evaluate(order_cbeth)
    assert decision_cbeth["decision"] == "deny"
    rule_codes_cbeth = [r["code"] for r in decision_cbeth["rules_hit"]]
    assert "RISK.UTILIZATION_TOO_HIGH" in rule_codes_cbeth

    # 2. WBTC market: 100,000 USDC supply, 70,000 USDC borrow = 7000 bps (70%)
    wbtc_market_bytes = eth_abi.encode(
        ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
        [
            100_000_000_000,
            90_000_000_000,
            70_000_000_000,
            60_000_000_000,
            int(NOW.timestamp()) - 20,
            0,
        ],
    )

    def fake_rpc_wbtc(payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": "0x" + wbtc_market_bytes.hex()}

    router_wbtc = RiskRouter(rpc_callable=fake_rpc_wbtc)
    order_wbtc = {
        "order_id": "ord-morpho-wbtc",
        "protocol": "morpho-blue",
        "token": "USDC",
        "market": "usdc-wbtc",
        "function": "supply",
        "amount": {"value": "1000000000", "decimals": 6},
        "recipient": "self",
    }

    res_wbtc = router_wbtc.get_risk_inputs(order_wbtc, now=NOW)
    assert res_wbtc["utilization_bps"] == 7000
    risk_inputs_validator.validate(res_wbtc)

    policy_wbtc = EnginePolicy(
        ips=SAMPLE_IPS,
        get_state=lambda: EMPTY_STATE,
        get_risk_inputs=router_wbtc,
        clock=NOW,
    )
    decision_wbtc = policy_wbtc.evaluate(order_wbtc)
    assert decision_wbtc["decision"] == "allow"


def test_uniswap_pool_tvl_with_fake_balances_and_prices(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """Uniswap V3 pool TVL is computed from token balances valued through usd_value."""
    pool_addr = "0x92266210facf124f190fbad79aa230dfb85926ce"
    usdc_addr = TOKEN_ADDRESSES["USDC"]
    weth_addr = TOKEN_ADDRESSES["WETH"]

    # Balances: 500,000 USDC (6 dec), 100 WETH (18 dec)
    usdc_bal = 500_000_000_000
    weth_bal = 100 * 10**18

    # Aave oracle price for WETH: 2,500.00 USD (8 dec = 250,000,000,000)
    weth_price_oracle = 250_000_000_000

    def fake_rpc(payload: dict[str, Any]) -> dict[str, Any]:
        params = payload.get("params", [{}])[0]
        to = params.get("to", "").lower()
        data = params.get("data", "")

        if to == FACTORY_ADDRESS.lower() and data.startswith(GET_POOL_SELECTOR):
            return {"result": eth_abi.encode(["address"], [pool_addr]).hex()}
        elif to == usdc_addr.lower() and data.startswith("0x70a08231"):
            return {"result": eth_abi.encode(["uint256"], [usdc_bal]).hex()}
        elif to == weth_addr.lower() and data.startswith("0x70a08231"):
            return {"result": eth_abi.encode(["uint256"], [weth_bal]).hex()}
        elif to == AAVE_ORACLE.lower() and data.startswith(GET_ASSET_PRICE_SELECTOR):
            return {"result": eth_abi.encode(["uint256"], [weth_price_oracle]).hex()}

        return {"result": "0x"}

    router = RiskRouter(rpc_callable=fake_rpc)
    order = {
        "order_id": "ord-uni-1",
        "protocol": "uniswap-v3",
        "token": "USDC",
        "token_out": "WETH",
        "function": "exactInputSingle",
        "amount": {"value": "1000000000", "decimals": 6},
        "max_slippage_bps": 50,
        "recipient": "self",
    }

    res = router.get_risk_inputs(order, now=NOW)
    assert res["protocol"] == "uniswap-v3"
    assert res["token"] == "USDC"
    assert res["utilization_bps"] == 0
    # Expected TVL: $500,000 (USDC) + $250,000 (100 WETH * $2500) = $750,000.00
    assert res["pool_tvl_usd"] == "750000.00"
    assert res["estimated_slippage_bps"] == 50
    assert res["sources"] == SOURCES_MAPPING
    risk_inputs_validator.validate(res)


def test_sources_labelling() -> None:
    """Sources mapping explicitly labels which fields are onchain and which are reference."""
    assert SOURCES_MAPPING["pool_tvl_usd"] == "onchain"
    assert SOURCES_MAPPING["utilization_bps"] == "onchain"
    assert SOURCES_MAPPING["data_age_seconds"] == "onchain"
    assert SOURCES_MAPPING["reward_apy_share_bps"] == "reference"
    assert SOURCES_MAPPING["apy_deviation_bps"] == "reference"
    assert SOURCES_MAPPING["stablecoin_depeg_bps"] == "reference"
    assert SOURCES_MAPPING["estimated_slippage_bps"] == "reference"
    assert SOURCES_MAPPING["estimated_cost_usd"] == "reference"
    assert SOURCES_MAPPING["estimated_benefit_usd"] == "reference"


def test_rpc_failure_denies_with_data_stale() -> None:
    """RPC failure in RiskRouter causes PolicyEngine evaluation to fail closed."""

    def broken_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"error": {"code": -32000, "message": "RPC timeout / network unreachable"}}

    router = RiskRouter(rpc_callable=broken_rpc)
    order = {
        "order_id": "ord-rpc-fail",
        "protocol": "aave-v3",
        "token": "USDC",
        "function": "supply",
        "amount": {"value": "1000000000", "decimals": 6},
        "recipient": "self",
    }

    # Calling router directly raises RpcCallError
    with pytest.raises(RpcCallError):
        router.get_risk_inputs(order)

    # Calling policy.evaluate with router attached denies with RISK.DATA_STALE
    policy = EnginePolicy(
        ips=SAMPLE_IPS,
        get_state=lambda: EMPTY_STATE,
        get_risk_inputs=router,
        clock=NOW,
    )
    decision = policy.evaluate(order)
    assert decision["decision"] == "deny"
    codes = [r["code"] for r in decision["rules_hit"]]
    assert "RISK.DATA_STALE" in codes


def test_unknown_protocol_denies_with_data_stale() -> None:
    """Unknown protocol raises UnsupportedProtocolError and denies with RISK.DATA_STALE."""
    router = RiskRouter(rpc_callable=lambda p: {"result": "0x"})
    order = {
        "order_id": "ord-unknown-prot",
        "protocol": "moonwell",
        "token": "USDC",
        "function": "supply",
        "amount": {"value": "1000000000", "decimals": 6},
        "recipient": "self",
    }

    with pytest.raises(UnsupportedProtocolError):
        router.get_risk_inputs(order)

    # In engine evaluation, unknown protocol in risk router denies
    policy = EnginePolicy(
        ips=SAMPLE_IPS,
        get_state=lambda: EMPTY_STATE,
        get_risk_inputs=router,
        clock=NOW,
    )
    decision = policy.evaluate(order)
    assert decision["decision"] == "deny"
    codes = [r["code"] for r in decision["rules_hit"]]
    # Both protocol not allowed in universe / data stale are checked
    assert "RISK.DATA_STALE" in codes or "ACCESS.PROTOCOL_NOT_ALLOWED" in codes


def test_zero_argument_callable_still_works() -> None:
    """EnginePolicy backwards compatibility: a zero-argument callable still works unchanged.

    Also tests that RiskRouter called without order returns reference baseline inputs.
    """
    router = RiskRouter()
    baseline = router()
    assert baseline["data_age_seconds"] == 0
    assert baseline["as_of"].endswith("Z")

    policy = EnginePolicy(
        ips=SAMPLE_IPS,
        get_state=lambda: EMPTY_STATE,
        get_risk_inputs=reference_risk_inputs,
        clock=NOW,
    )
    order = {
        "order_id": "ord-zero-arg",
        "protocol": "aave-v3",
        "token": "USDC",
        "function": "supply",
        "amount": {"value": "1000000000", "decimals": 6},
        "recipient": "self",
    }
    decision = policy.evaluate(order)
    assert decision["decision"] == "allow"


def test_zero_liquidity_uniswap_pool_fails_closed() -> None:
    """Uniswap V3 pool returning zero liquidity raises RiskDataUnavailableError and denies."""
    pool_addr = "0x92266210facf124f190fbad79aa230dfb85926ce"
    usdc_addr = TOKEN_ADDRESSES["USDC"]
    weth_addr = TOKEN_ADDRESSES["WETH"]

    def fake_rpc(payload: dict[str, Any]) -> dict[str, Any]:
        params = payload.get("params", [{}])[0]
        to = params.get("to", "").lower()
        data = params.get("data", "")
        if to == FACTORY_ADDRESS.lower() and data.startswith(GET_POOL_SELECTOR):
            return {"result": eth_abi.encode(["address"], [pool_addr]).hex()}
        elif (to == usdc_addr.lower() or to == weth_addr.lower()) and data.startswith("0x70a08231"):
            # Zero balance
            return {"result": eth_abi.encode(["uint256"], [0]).hex()}
        return {"result": "0x"}

    router = RiskRouter(rpc_callable=fake_rpc)
    order = {
        "order_id": "ord-uni-zero",
        "protocol": "uniswap-v3",
        "token": "USDC",
        "token_out": "WETH",
        "function": "exactInputSingle",
        "amount": {"value": "1000000000", "decimals": 6},
        "max_slippage_bps": 50,
        "recipient": "self",
    }

    with pytest.raises(RiskDataUnavailableError):
        router.get_risk_inputs(order)

    policy = EnginePolicy(
        ips=SAMPLE_IPS,
        get_state=lambda: EMPTY_STATE,
        get_risk_inputs=router,
        clock=NOW,
    )
    decision = policy.evaluate(order)
    assert decision["decision"] == "deny"
    codes = [r["code"] for r in decision["rules_hit"]]
    assert "RISK.DATA_STALE" in codes
