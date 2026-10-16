"""Tests for Morpho Blue protocol adapter (ticket D3)."""

from __future__ import annotations

import uuid

import eth_abi
import pytest
from web3 import Web3

from core.gateway.txbuilder import build_calls
from core.protocols import registry
from core.protocols.morpho_blue import (
    AAVE_CBETH,
    AAVE_V3_USDC,
    AAVE_WBTC,
    ADAPTIVE_CURVE_IRM,
    CBETH_LLTV,
    CBETH_ORACLE,
    MORPHO_BLUE,
    WBTC_LLTV,
    WBTC_ORACLE,
    WHITELISTED_MARKETS,
    MorphoOrderValidationError,
    build_supply_calldata,
    build_withdraw_calldata,
    compute_market_id,
    describe,
    positions,
)

SAMPLE_WALLET = "0xc18afDC2dF55772bA3c031Df98305F02a3a36493"

# Fixed expected calldata hex independently computed in the test
EXPECTED_SUPPLY_WBTC_10USDC_HEX = (
    "0xa99aad89"
    "000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f"
    "00000000000000000000000054114591963cf60ef3aa63befd6ec263d98145a4"
    "000000000000000000000000b22092390d44ef7271a71a371097abf44abb6c27"
    "00000000000000000000000046415998764c29ab2a25cbea6254146d50d22687"
    "0000000000000000000000000000000000000000000000000bef55718ad60000"
    "0000000000000000000000000000000000000000000000000000000000989680"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a36493"
    "0000000000000000000000000000000000000000000000000000000000000120"
    "0000000000000000000000000000000000000000000000000000000000000000"
)

EXPECTED_WITHDRAW_WBTC_10USDC_HEX = (
    "0x5c2bea49"
    "000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f"
    "00000000000000000000000054114591963cf60ef3aa63befd6ec263d98145a4"
    "000000000000000000000000b22092390d44ef7271a71a371097abf44abb6c27"
    "00000000000000000000000046415998764c29ab2a25cbea6254146d50d22687"
    "0000000000000000000000000000000000000000000000000bef55718ad60000"
    "0000000000000000000000000000000000000000000000000000000000989680"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a36493"
    "000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a36493"
)

EXPECTED_SUPPLY_CBETH_25USDC_HEX = (
    "0xa99aad89"
    "000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f"
    "000000000000000000000000d171b9694f7a2597ed006d41f7509aad4b485c4b"
    "000000000000000000000000f4fbcfdda39202549ca4f1230e694c32b5a2f80d"
    "00000000000000000000000046415998764c29ab2a25cbea6254146d50d22687"
    "0000000000000000000000000000000000000000000000000cb2bba6f17b8000"
    "00000000000000000000000000000000000000000000000000000000017d7840"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a36493"
    "0000000000000000000000000000000000000000000000000000000000000120"
    "0000000000000000000000000000000000000000000000000000000000000000"
)

EXPECTED_WITHDRAW_CBETH_25USDC_HEX = (
    "0x5c2bea49"
    "000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f"
    "000000000000000000000000d171b9694f7a2597ed006d41f7509aad4b485c4b"
    "000000000000000000000000f4fbcfdda39202549ca4f1230e694c32b5a2f80d"
    "00000000000000000000000046415998764c29ab2a25cbea6254146d50d22687"
    "0000000000000000000000000000000000000000000000000cb2bba6f17b8000"
    "00000000000000000000000000000000000000000000000000000000017d7840"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a36493"
    "000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a36493"
)


def make_order(**over):
    base = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "morpho-blue",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "10000000", "decimals": 6},
        "market": "usdc-wbtc",
        "recipient": "self",
    }
    base.update(over)
    return base


def test_market_id_hash_computation():
    """Verify market IDs match keccak256 hash of ABI-encoded MarketParams."""
    # Compute independently for usdc-wbtc
    wbtc_id = compute_market_id(
        loan_token=AAVE_V3_USDC,
        collateral_token=AAVE_WBTC,
        oracle=WBTC_ORACLE,
        irm=ADAPTIVE_CURVE_IRM,
        lltv=WBTC_LLTV,
    )
    assert wbtc_id == "0xa7cc9d96371f4c4b5be131281cd24a410cf6b76dcfb4319004d5bd66045adb97"
    assert WHITELISTED_MARKETS["usdc-wbtc"].id == wbtc_id

    # Compute independently for usdc-cbeth
    cbeth_id = compute_market_id(
        loan_token=AAVE_V3_USDC,
        collateral_token=AAVE_CBETH,
        oracle=CBETH_ORACLE,
        irm=ADAPTIVE_CURVE_IRM,
        lltv=CBETH_LLTV,
    )
    assert cbeth_id == "0x532165f57c1077690d6b2f413e5555eba34611df4e625820ca6aa187f3f89b65"
    assert WHITELISTED_MARKETS["usdc-cbeth"].id == cbeth_id


def test_abi_encoding_vectors_supply_and_withdraw():
    """Verify calldata matches independently precomputed fixed hex vectors."""
    wbtc_market = WHITELISTED_MARKETS["usdc-wbtc"]
    amount = 10_000_000

    # Supply calldata check
    supply_calldata = build_supply_calldata(
        market_params=wbtc_market.params_tuple,
        assets=amount,
        shares=0,
        on_behalf=SAMPLE_WALLET,
        data=b"",
    )
    assert supply_calldata == EXPECTED_SUPPLY_WBTC_10USDC_HEX

    # Withdraw calldata check
    withdraw_calldata = build_withdraw_calldata(
        market_params=wbtc_market.params_tuple,
        assets=amount,
        shares=0,
        on_behalf=SAMPLE_WALLET,
        receiver=SAMPLE_WALLET,
    )
    assert withdraw_calldata == EXPECTED_WITHDRAW_WBTC_10USDC_HEX

    # cbETH market vectors check (25 USDC)
    cbeth_market = WHITELISTED_MARKETS["usdc-cbeth"]
    cbeth_amount = 25_000_000
    supply_cbeth_calldata = build_supply_calldata(
        market_params=cbeth_market.params_tuple,
        assets=cbeth_amount,
        shares=0,
        on_behalf=SAMPLE_WALLET,
        data=b"",
    )
    assert supply_cbeth_calldata == EXPECTED_SUPPLY_CBETH_25USDC_HEX

    withdraw_cbeth_calldata = build_withdraw_calldata(
        market_params=cbeth_market.params_tuple,
        assets=cbeth_amount,
        shares=0,
        on_behalf=SAMPLE_WALLET,
        receiver=SAMPLE_WALLET,
    )
    assert withdraw_cbeth_calldata == EXPECTED_WITHDRAW_CBETH_25USDC_HEX


def test_spec_registration_and_properties():
    """Verify Morpho Blue spec is registered with correct metadata."""
    spec = registry.get("morpho-blue")
    assert spec is not None
    assert spec.id == "morpho-blue"
    assert spec.name == "Morpho Blue"
    assert spec.category == "lending"
    assert spec.chain_id == 84532
    assert spec.tokens == frozenset({"USDC"})
    assert spec.markets == frozenset({"usdc-wbtc", "usdc-cbeth"})
    assert spec.spenders == frozenset({MORPHO_BLUE})
    assert spec.recipients == frozenset({"self"})
    assert not spec.paper


def test_build_calls_through_registry():
    """Verify gateway build_calls translates orders to expected calls."""
    # 1. Supply order
    supply_order = make_order(
        function="supply",
        amount={"value": "10000000", "decimals": 6},
        market="usdc-wbtc",
    )
    (supply_call,) = build_calls(supply_order, SAMPLE_WALLET)
    assert supply_call.to == MORPHO_BLUE
    assert supply_call.data == EXPECTED_SUPPLY_WBTC_10USDC_HEX
    assert supply_call.value == 0

    # 2. Withdraw order
    withdraw_order = make_order(
        function="withdraw",
        amount={"value": "10000000", "decimals": 6},
        market="usdc-wbtc",
    )
    (withdraw_call,) = build_calls(withdraw_order, SAMPLE_WALLET)
    assert withdraw_call.to == MORPHO_BLUE
    assert withdraw_call.data == EXPECTED_WITHDRAW_WBTC_10USDC_HEX
    assert withdraw_call.value == 0

    # 3. Approve order
    approve_order = make_order(
        function="approve",
        amount={"value": "10000000", "decimals": 6},
    )
    (approve_call,) = build_calls(approve_order, SAMPLE_WALLET)
    assert approve_call.to == AAVE_V3_USDC
    assert approve_call.value == 0
    # Decode approve call
    spender, amt = eth_abi.decode(["address", "uint256"], bytes.fromhex(approve_call.data[10:]))
    assert spender.lower() == MORPHO_BLUE.lower()
    assert amt == 10_000_000


def test_denial_unsupported_market_and_token():
    """Verify orders with unwhitelisted markets or tokens are denied."""
    # 1. Preflight denies unlisted market
    hits = registry.preflight(make_order(market="usdc-weth"))
    assert [h["code"] for h in hits] == ["ACCESS.MARKET_NOT_ALLOWED"]

    # 2. Preflight denies missing market for morpho-blue order
    order_no_market = make_order()
    del order_no_market["market"]
    hits = registry.preflight(order_no_market)
    assert [h["code"] for h in hits] == ["ACCESS.MARKET_NOT_ALLOWED"]

    # 3. Adapter build_calls raises on unlisted market
    with pytest.raises(MorphoOrderValidationError, match="Unsupported market"):
        build_calls(make_order(market="usdc-weth"), SAMPLE_WALLET)

    # 4. Adapter build_calls raises on token other than USDC
    with pytest.raises(MorphoOrderValidationError, match="Unsupported token"):
        build_calls(make_order(token="WETH"), SAMPLE_WALLET)

    # 5. Non-positive amount raises
    with pytest.raises(MorphoOrderValidationError, match="positive"):
        build_calls(make_order(amount={"value": "0", "decimals": 6}), SAMPLE_WALLET)


class FakePositionReader:
    """Fake PositionReader for testing positions conversion logic."""

    def __init__(self, responses: dict[tuple[str, str], str]):
        self.responses = responses

    def balance_of(self, token_address: str) -> int:
        return 0

    def address_for(self, key: str, default: str) -> str:
        return default

    def eth_call(self, to: str, data: str) -> str:
        # Match by (to, selector)
        to_cs = Web3.to_checksum_address(to)
        for (target, sel), res in self.responses.items():
            if Web3.to_checksum_address(target) == to_cs and data.startswith(sel):
                return res
        return "0x"


def test_positions_math_with_fake_reader():
    """Verify positions calculates USDC assets using SharesMathLib virtual offsets."""
    wbtc_market = WHITELISTED_MARKETS["usdc-wbtc"]
    wbtc_bytes = bytes.fromhex(wbtc_market.id[2:])

    # Fake position return: supplyShares = 10_000_000_000_000 (1e13)
    pos_encoded = eth_abi.encode(
        ["uint256", "uint128", "uint128"],
        [10_000_000_000_000, 0, 0],
    ).hex()

    # Fake market return: 30,000 USDC assets, 3e16 shares
    market_encoded = eth_abi.encode(
        ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
        [30_000_000_000, 30_000_000_000_000_000, 20_000_000_000, 20_000_000_000_000_000, 0, 0],
    ).hex()

    pos_selector = "0x93c52062"
    market_selector = "0x5c60e39a"

    # Query for wbtc market position
    wbtc_pos_data = (
        pos_selector
        + eth_abi.encode(
            ["bytes32", "address"], [wbtc_bytes, Web3.to_checksum_address(SAMPLE_WALLET)]
        ).hex()
    )
    wbtc_market_data = market_selector + eth_abi.encode(["bytes32"], [wbtc_bytes]).hex()

    reader = FakePositionReader(
        {
            (MORPHO_BLUE, wbtc_pos_data): "0x" + pos_encoded,
            (MORPHO_BLUE, wbtc_market_data): "0x" + market_encoded,
        }
    )

    legs = positions(SAMPLE_WALLET, reader)
    assert len(legs) == 1  # only active market with non-zero assets returned

    # Virtual offsets: supply_shares * (assets + 1) // (shares + 1e6)
    expected_assets = (10_000_000_000_000 * 30_000_000_001) // 30_000_000_001_000_000
    assert expected_assets == 10_000_000

    wbtc_leg = next(leg for leg in legs if leg.balance > 0)
    assert wbtc_leg.token == "USDC"
    assert wbtc_leg.location == "morpho-blue"
    assert wbtc_leg.balance == expected_assets
    assert wbtc_leg.decimals == 6


def test_describe():
    """Verify plain English descriptions for orders."""
    # Supply
    s_order = make_order(
        function="supply",
        amount={"value": "10000000", "decimals": 6},
        market="usdc-wbtc",
    )
    assert describe(s_order) == "Lend 10 USDC to Morpho (USDC/WBTC market)"

    # Withdraw
    w_order = make_order(
        function="withdraw",
        amount={"value": "25000000", "decimals": 6},
        market="usdc-cbeth",
    )
    assert describe(w_order) == "Withdraw 25 USDC from Morpho (USDC/cbETH market)"

    # Approve
    a_order = make_order(
        function="approve",
        amount={"value": "10000000", "decimals": 6},
    )
    assert describe(a_order) == "Approve Morpho Blue to spend 10 USDC"


def test_preflight_approve_needs_no_market_but_supply_does() -> None:
    from core.protocols import registry

    base = {"protocol": "morpho-blue", "token": "USDC"}
    assert registry.preflight({**base, "function": "approve"}) == []
    assert registry.preflight({**base, "function": "supply"})[0]["code"] == (
        "ACCESS.MARKET_NOT_ALLOWED"
    )
    assert registry.preflight({**base, "function": "supply", "market": "usdc-wbtc"}) == []
