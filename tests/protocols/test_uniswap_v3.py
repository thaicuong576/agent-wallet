"""Unit tests for Uniswap V3 protocol adapter."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import eth_abi
import jsonschema
import pytest

from core.protocols import registry
from core.protocols.registry import BuildContext
from core.protocols.tokens import PriceUnavailable
from core.protocols.uniswap_v3 import (
    APPROVE_SELECTOR,
    EXACT_INPUT_SINGLE_SELECTOR,
    PROTOCOL_ID,
    QUOTE_EXACT_INPUT_SINGLE_SELECTOR,
    SWAP_ROUTER_02,
    USDC_ADDRESS,
    WBTC_ADDRESS,
    WETH_ADDRESS,
    OrderValidationError,
    build_approve_calldata,
    build_exact_input_single_calldata,
    build_quote_exact_input_single_calldata,
    check_pool_price_guard,
    compute_amount_out_minimum,
    quote_exact_input_single,
    validate_order,
)

WALLET = "0xc18afDC2dF55772bA3c031Df98305F02a3a36493"
SCHEMA_PATH = Path("contracts/schemas/order.schema.json")
ORDER_VALIDATOR = jsonschema.Draft202012Validator(
    json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
)


def swap_order(**over: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T12:00:00Z",
        "proposed_by": "planner",
        "protocol": "uniswap-v3",
        "function": "exactInputSingle",
        "token": "USDC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "self",
        "token_out": "WBTC",
        "max_slippage_bps": 50,
    }
    base.update(over)
    return base


def approve_order(**over: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T12:00:00Z",
        "proposed_by": "planner",
        "protocol": "uniswap-v3",
        "function": "approve",
        "token": "USDC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "uniswap_router",
    }
    base.update(over)
    return base


def mock_oracle_rpc(
    prices: dict[str, int],
    quotes: dict[str, int] | int | None = None,
) -> Any:
    """Mock RPC returning 8-decimal asset prices for AaveOracle and QuoterV2 quotes."""

    def rpc(method: str, params: list[Any]) -> Any:
        if method == "eth_call":
            call = params[0]
            data = call.get("data", "")
            # AaveOracle getAssetPrice
            if data.startswith("0xb3596f07"):
                asset = "0x" + data[10:][24:64].lower()
                for sym, addr in [("WETH", WETH_ADDRESS), ("WBTC", WBTC_ADDRESS)]:
                    if asset == addr.lower():
                        price_int = prices.get(sym, 0)
                        return "0x" + price_int.to_bytes(32, "big").hex()
            # QuoterV2 quoteExactInputSingle
            if data.startswith(QUOTE_EXACT_INPUT_SINGLE_SELECTOR):
                if isinstance(quotes, int):
                    quote_val = quotes
                elif isinstance(quotes, dict):
                    quote_val = 0
                    for sym, _addr in [
                        ("WETH", WETH_ADDRESS),
                        ("WBTC", WBTC_ADDRESS),
                        ("USDC", USDC_ADDRESS),
                    ]:
                        if sym in quotes:
                            quote_val = quotes[sym]
                else:
                    quote_val = 0
                encoded = eth_abi.encode(
                    ["uint256", "uint160", "uint32", "uint256"],
                    [quote_val, 0, 0, 100_000],
                )
                return "0x" + encoded.hex()
        raise RuntimeError(f"Unhandled mock RPC call: {method} {params}")

    return rpc


# ----------------------------------------------------------------- Encoder vectors


def test_build_approve_calldata_vector():
    spender = "0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4"
    amount = 10_000_000
    calldata = build_approve_calldata(spender, amount)

    assert calldata.startswith(APPROVE_SELECTOR)
    decoded_spender, decoded_amount = eth_abi.decode(
        ["address", "uint256"], bytes.fromhex(calldata[10:])
    )
    assert decoded_spender.lower() == spender.lower()
    assert decoded_amount == amount

    with pytest.raises(ValueError, match="positive"):
        build_approve_calldata(spender, 0)


def test_build_exact_input_single_calldata_vector():
    token_in = USDC_ADDRESS
    token_out = WBTC_ADDRESS
    fee = 500
    recipient = WALLET
    amount_in = 10_000_000
    amount_out_min = 12_104
    sqrt_price_limit = 0

    calldata = build_exact_input_single_calldata(
        token_in=token_in,
        token_out=token_out,
        fee=fee,
        recipient=recipient,
        amount_in=amount_in,
        amount_out_minimum=amount_out_min,
        sqrt_price_limit_x96=sqrt_price_limit,
    )

    assert calldata.startswith(EXACT_INPUT_SINGLE_SELECTOR)
    (decoded_tuple,) = eth_abi.decode(
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        bytes.fromhex(calldata[10:]),
    )
    assert decoded_tuple[0].lower() == token_in.lower()
    assert decoded_tuple[1].lower() == token_out.lower()
    assert decoded_tuple[2] == fee
    assert decoded_tuple[3].lower() == recipient.lower()
    assert decoded_tuple[4] == amount_in
    assert decoded_tuple[5] == amount_out_min
    assert decoded_tuple[6] == sqrt_price_limit

    with pytest.raises(ValueError, match="positive"):
        build_exact_input_single_calldata(token_in, token_out, fee, recipient, 0, amount_out_min)
    with pytest.raises(ValueError, match="non-negative"):
        build_exact_input_single_calldata(token_in, token_out, fee, recipient, amount_in, -1)


def test_build_quote_exact_input_single_calldata_vector():
    token_in = USDC_ADDRESS
    token_out = WETH_ADDRESS
    fee = 3000
    amount_in = 20_000_000

    calldata = build_quote_exact_input_single_calldata(
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        fee=fee,
    )

    assert calldata.startswith(QUOTE_EXACT_INPUT_SINGLE_SELECTOR)
    (decoded_tuple,) = eth_abi.decode(
        ["(address,address,uint256,uint24,uint160)"],
        bytes.fromhex(calldata[10:]),
    )
    assert decoded_tuple[0].lower() == token_in.lower()
    assert decoded_tuple[1].lower() == token_out.lower()
    assert decoded_tuple[2] == amount_in
    assert decoded_tuple[3] == fee
    assert decoded_tuple[4] == 0


# ----------------------------------------------------------------- Registry integration


def test_uniswap_v3_registers_itself():
    spec = registry.get(PROTOCOL_ID)
    assert spec is not None
    assert spec.id == "uniswap-v3"
    assert spec.name == "Uniswap v3"
    assert spec.category == "dex"
    assert not spec.paper
    assert spec.chain_id == 84532
    assert spec.spenders == frozenset({SWAP_ROUTER_02})
    assert spec.tokens == frozenset({"USDC", "WETH", "WBTC"})
    assert spec.functions == frozenset({"approve", "exactInputSingle"})
    assert spec.recipients == frozenset({"self", "uniswap_router"})
    assert registry.by_category("dex") == [spec]


def test_build_calls_approve_via_registry():
    spec = registry.get(PROTOCOL_ID)
    o = approve_order(token="USDC", amount={"value": "5000000", "decimals": 6})
    calls = spec.build_calls(o, BuildContext(WALLET))
    assert len(calls) == 1
    call = calls[0]
    assert call.to.lower() == USDC_ADDRESS.lower()
    assert call.value == 0
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(call.data[10:]))
    assert spender.lower() == SWAP_ROUTER_02.lower()
    assert amount == 5_000_000


def test_build_calls_exact_input_single_via_registry():
    prices = {"WBTC": 82_204 * 10**8}  # $82,204 per WBTC
    quote_out = 12_000  # Pool quote (~1.35% below fair value 12,164, within 3% deviation guard)
    rpc = mock_oracle_rpc(prices, quotes=quote_out)
    o = swap_order(
        token="USDC",
        token_out="WBTC",
        amount={"value": "10000000", "decimals": 6},
        max_slippage_bps=50,
    )
    calls = _uni_build(o, BuildContext(WALLET), rpc=rpc)
    assert len(calls) == 1
    call = calls[0]
    assert call.to.lower() == SWAP_ROUTER_02.lower()
    assert call.value == 0

    (decoded,) = eth_abi.decode(
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        bytes.fromhex(call.data[10:]),
    )
    assert decoded[0].lower() == USDC_ADDRESS.lower()
    assert decoded[1].lower() == WBTC_ADDRESS.lower()
    assert decoded[2] == 500  # USDC/WBTC fee tier
    assert decoded[3].lower() == WALLET.lower()  # Recipient is wallet
    assert decoded[4] == 10_000_000
    # Slippage 50 bps on executable pool quote: 12000 * 9950 // 10000 = 11940 satoshis.
    assert decoded[5] == 11940
    assert decoded[6] == 0


def test_build_calls_usdc_to_weth_fee_tier_3000():
    prices = {"WETH": 2_500 * 10**8}  # $2,500 per WETH
    quote_out = 4_000_000_000_000_000  # 0.004 WETH executable pool quote (matches fair value)
    rpc = mock_oracle_rpc(prices, quotes=quote_out)
    o = swap_order(
        token="USDC",
        token_out="WETH",
        amount={"value": "10000000", "decimals": 6},
        max_slippage_bps=100,
    )
    calls = _uni_build(o, BuildContext(WALLET), rpc=rpc)
    (decoded,) = eth_abi.decode(
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        bytes.fromhex(calls[0].data[10:]),
    )
    assert decoded[2] == 3000  # USDC/WETH fee tier
    # 4e15 * (10000 - 100) // 10000 = 3.96e15 wei.
    assert decoded[5] == 3_960_000_000_000_000


def test_quote_exact_input_single_helper():
    rpc = mock_oracle_rpc({}, quotes=6013)
    quote = quote_exact_input_single("USDC", "WBTC", 5_000_000, rpc=rpc)
    assert quote == 6013


def test_build_calls_min_out_equals_floor_quote_minus_slippage():
    # Demonstrates floor division behavior: 6013 * 9950 / 10000 = 5982.935 -> floor 5982
    # Oracle price set so fair value is ~6096, so 6013 is ~1.36% below oracle (< 3% deviation)
    prices = {"WBTC": 82_021 * 10**8}
    quote_out = 6013
    rpc = mock_oracle_rpc(prices, quotes=quote_out)
    o = swap_order(
        token="USDC",
        token_out="WBTC",
        amount={"value": "5000000", "decimals": 6},
        max_slippage_bps=50,
    )
    calls = _uni_build(o, BuildContext(WALLET), rpc=rpc)
    (decoded,) = eth_abi.decode(
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        bytes.fromhex(calls[0].data[10:]),
    )
    expected_min_out = quote_out * (10000 - 50) // 10000
    assert expected_min_out == 5982
    assert decoded[5] == expected_min_out


def test_build_calls_raises_when_quote_4_percent_below_oracle():
    # Oracle fair value: 10 USDC / 80,000 = 12,500 satoshis.
    # Quote 4% below fair value: 12,500 * 0.96 = 12,000 satoshis (deviation 400 bps > 300 bps cap)
    prices = {"WBTC": 80_000 * 10**8}
    quote_out = 12_000
    rpc = mock_oracle_rpc(prices, quotes=quote_out)
    o = swap_order(
        token="USDC",
        token_out="WBTC",
        amount={"value": "10000000", "decimals": 6},
        max_slippage_bps=50,
    )
    with pytest.raises(
        OrderValidationError,
        match=r"^RISK\.POOL_PRICE_DEVIATION: pool quote is 400 bps below oracle fair value",
    ):
        _uni_build(o, BuildContext(WALLET), rpc=rpc)


# ----------------------------------------------------------------- Math & decimals


def test_amount_out_minimum_usdc_to_wbtc_math():
    prices = {"WBTC": 82_204 * 10**8}
    rpc = mock_oracle_rpc(prices)

    # 10 USDC (6 dec) -> WBTC (8 dec), 0 slippage
    out_0 = compute_amount_out_minimum("USDC", "WBTC", 10_000_000, 0, rpc=rpc)
    # 10 / 82204 * 10^8 = 12164.858... -> 12164
    assert out_0 == 12164

    # 50 bps (0.5%) slippage
    out_50 = compute_amount_out_minimum("USDC", "WBTC", 10_000_000, 50, rpc=rpc)
    assert out_50 == 12104

    # 100 bps (1%) slippage
    out_100 = compute_amount_out_minimum("USDC", "WBTC", 10_000_000, 100, rpc=rpc)
    assert out_100 == 12043


def test_amount_out_minimum_wbtc_to_usdc_math():
    prices = {"WBTC": 80_000 * 10**8}
    rpc = mock_oracle_rpc(prices)

    # 0.01 WBTC (1,000,000 satoshis, 8 dec) -> USDC (6 dec)
    # Fair: 0.01 * 80,000 = 800 USD = 800_000_000 USDC units
    out_0 = compute_amount_out_minimum("WBTC", "USDC", 1_000_000, 0, rpc=rpc)
    assert out_0 == 800_000_000

    out_50 = compute_amount_out_minimum("WBTC", "USDC", 1_000_000, 50, rpc=rpc)
    assert out_50 == 796_000_000


def test_amount_out_minimum_oracle_unavailable_fails_closed():
    def broken_rpc(method, params):
        raise RuntimeError("oracle timeout")

    with pytest.raises(PriceUnavailable):
        compute_amount_out_minimum("USDC", "WBTC", 10_000_000, 50, rpc=broken_rpc)


# ----------------------------------------------------------------- Slippage & pair constraints


def test_slippage_cap_enforced():
    prices = {"WBTC": 80_000 * 10**8}
    rpc = mock_oracle_rpc(prices)

    # <= 100 bps allowed
    compute_amount_out_minimum("USDC", "WBTC", 10_000_000, 100, rpc=rpc)

    # > 100 bps rejected
    with pytest.raises(OrderValidationError, match="ceiling"):
        compute_amount_out_minimum("USDC", "WBTC", 10_000_000, 101, rpc=rpc)

    with pytest.raises(OrderValidationError, match="non-negative"):
        compute_amount_out_minimum("USDC", "WBTC", 10_000_000, -1, rpc=rpc)


def test_unsupported_pair_rejected():
    with pytest.raises(OrderValidationError, match="Unsupported or missing token_out"):
        validate_order(swap_order(token="USDC", token_out="USDT"))

    with pytest.raises(OrderValidationError, match="Pair.*not allowed"):
        validate_order(swap_order(token="WETH", token_out="WBTC"))

    with pytest.raises(OrderValidationError, match="same"):
        validate_order(swap_order(token="USDC", token_out="USDC"))


def test_recipient_must_be_self_for_swap():
    with pytest.raises(OrderValidationError, match="Recipient.*must be 'self'"):
        validate_order(swap_order(recipient="owner"))

    with pytest.raises(OrderValidationError, match="Recipient.*must be 'self'"):
        validate_order(swap_order(recipient="uniswap_router"))


# ----------------------------------------------------------------- Positions & Describe


def test_uniswap_positions_query():
    spec = registry.get(PROTOCOL_ID)

    class FakeReader:
        def address_for(self, key: str, default: str) -> str:
            return default

        def balance_of(self, token_address: str) -> int:
            if token_address == WETH_ADDRESS:
                return 150_000_000_000_000_000  # 0.15 WETH
            if token_address == WBTC_ADDRESS:
                return 250_000  # 0.0025 WBTC
            return 0

    legs = spec.positions(WALLET, FakeReader())
    assert len(legs) == 2
    assert legs[0].token == "WETH"
    assert legs[0].location == "wallet"
    assert legs[0].balance == 150_000_000_000_000_000
    assert legs[0].decimals == 18

    assert legs[1].token == "WBTC"
    assert legs[1].location == "wallet"
    assert legs[1].balance == 250_000
    assert legs[1].decimals == 8


def test_describe_orders():
    spec = registry.get(PROTOCOL_ID)
    desc_swap = spec.describe(swap_order(token="USDC", token_out="WBTC"))
    assert "Swap USDC for WBTC on Uniswap v3" in desc_swap

    desc_appr = spec.describe(approve_order(token="USDC"))
    assert "Approve Uniswap v3 SwapRouter02 to spend USDC" in desc_appr


# ----------------------------------------------------------------- Pool price guard


def test_pool_price_guard_clean_when_fair():
    def fair_rpc(method: str, params: list[Any]) -> Any:
        data = params[0].get("data", "")
        # Oracle quote for WBTC: $80,000
        if data.startswith("0xb3596f07"):
            return "0x" + (80_000 * 10**8).to_bytes(32, "big").hex()
        # Quoter quote for 10 USDC: 12,400 satoshis (close to fair 12,500 satoshis, < 1% deviation)
        if data.startswith(QUOTE_EXACT_INPUT_SINGLE_SELECTOR):
            encoded = eth_abi.encode(
                ["uint256", "uint160", "uint32", "uint256"],
                [12450, 0, 0, 100_000],
            )
            return "0x" + encoded.hex()
        raise RuntimeError(method)

    res = check_pool_price_guard("USDC", "WBTC", 10_000_000, rpc=fair_rpc)
    assert res == {}


def test_pool_price_guard_triggers_on_mispriced_pool():
    # Mispriced pool: Quoter returns only 8,000 satoshis when oracle fair value
    # is 12,500 satoshis (>3% worse)
    def mispriced_rpc(method: str, params: list[Any]) -> Any:
        data = params[0].get("data", "")
        if data.startswith("0xb3596f07"):
            return "0x" + (80_000 * 10**8).to_bytes(32, "big").hex()
        if data.startswith(QUOTE_EXACT_INPUT_SINGLE_SELECTOR):
            encoded = eth_abi.encode(
                ["uint256", "uint160", "uint32", "uint256"],
                [8000, 0, 0, 100_000],
            )
            return "0x" + encoded.hex()
        raise RuntimeError(method)

    res = check_pool_price_guard("USDC", "WBTC", 10_000_000, rpc=mispriced_rpc)
    assert res["code"] == "RISK.POOL_PRICE_DEVIATION"
    assert res["evidence"]["deviation_bps"] > 300


# ----------------------------------------------------------------- Schema examples


def test_schema_valid_swap_examples():
    valid_order = swap_order(token="USDC", token_out="WBTC", max_slippage_bps=50)
    assert not list(ORDER_VALIDATOR.iter_errors(valid_order))

    # Also verify repository example files
    p_valid = Path("contracts/examples/order.valid.swap.json")
    if p_valid.exists():
        data = json.loads(p_valid.read_text(encoding="utf-8"))
        assert not list(ORDER_VALIDATOR.iter_errors(data))


def test_schema_invalid_swap_missing_token_out():
    invalid_order = swap_order()
    del invalid_order["token_out"]
    assert list(ORDER_VALIDATOR.iter_errors(invalid_order))

    p_invalid = Path("contracts/examples/order.invalid.swap-missing-token-out.json")
    if p_invalid.exists():
        data = json.loads(p_invalid.read_text(encoding="utf-8"))
        assert list(ORDER_VALIDATOR.iter_errors(data))


from core.protocols.uniswap_v3 import build_calls as _uni_build  # noqa: E402
