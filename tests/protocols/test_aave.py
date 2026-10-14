"""Tests for Aave V3 protocol adapter on Base Sepolia."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest
from web3 import Web3

from core.protocols.aave import (
    APPROVE_SELECTOR,
    SUPPLY_SELECTOR,
    WITHDRAW_SELECTOR,
    Call,
    OrderValidationError,
    build_calls,
    order_to_calls,
)
from core.protocols.addresses import (
    AAVE_V3_POOL,
    AAVE_V3_USDC,
    BASE_SEPOLIA_CHAIN_ID,
    resolve_recipient,
)

SAMPLE_WALLET: Final[str] = "0x1111111111111111111111111111111111111111"
EXAMPLES_DIR: Final[Path] = Path(__file__).resolve().parent.parent.parent / "contracts" / "examples"

# Independent ABIs for test verification fixtures (pure local encoding)
ERC20_ABI: Final[list[dict]] = [
    {
        "type": "function",
        "name": "approve",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    }
]

AAVE_POOL_ABI: Final[list[dict]] = [
    {
        "type": "function",
        "name": "supply",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "onBehalfOf", "type": "address"},
            {"name": "referralCode", "type": "uint16"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "withdraw",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "to", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


@pytest.fixture
def w3() -> Web3:
    return Web3()


def test_function_selectors(w3: Web3) -> None:
    """Selectors match known values: approve 0x095ea7b3, supply 0x617ba037, withdraw 0x69328dec."""
    assert APPROVE_SELECTOR == "0x095ea7b3"
    assert SUPPLY_SELECTOR == "0x617ba037"
    assert WITHDRAW_SELECTOR == "0x69328dec"

    # Independently compute Keccak hashes of function signatures
    approve_sig = "approve(address,uint256)"
    supply_sig = "supply(address,uint256,address,uint16)"
    withdraw_sig = "withdraw(address,uint256,address)"

    assert "0x" + w3.keccak(text=approve_sig)[:4].hex() == APPROVE_SELECTOR
    assert "0x" + w3.keccak(text=supply_sig)[:4].hex() == SUPPLY_SELECTOR
    assert "0x" + w3.keccak(text=withdraw_sig)[:4].hex() == WITHDRAW_SELECTOR


def test_approve_calldata_matches_independent_fixture(w3: Web3) -> None:
    """Approve calldata equals a fixture built independently from Web3 ABI encoding."""
    amount = 80_000_000
    contract = w3.eth.contract(abi=ERC20_ABI)
    expected_calldata = contract.encode_abi("approve", [AAVE_V3_POOL, amount])

    order = {
        "schema_version": 1,
        "order_id": "0b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d60",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "approve",
        "token": "USDC",
        "amount": {
            "value": str(amount),
            "decimals": 6,
        },
        "recipient": "aave_pool",
    }

    calls = build_calls(order, SAMPLE_WALLET)
    assert len(calls) == 1
    call = calls[0]

    assert isinstance(call, Call)
    assert call.to == AAVE_V3_USDC
    assert call.value == 0
    assert call.data.lower() == expected_calldata.lower()
    assert order_to_calls(order, SAMPLE_WALLET) == calls


def test_supply_calldata_matches_independent_fixture(w3: Web3) -> None:
    """Supply calldata equals a fixture built independently from Web3 ABI encoding."""
    amount = 80_000_000
    contract = w3.eth.contract(abi=AAVE_POOL_ABI)
    expected_calldata = contract.encode_abi("supply", [AAVE_V3_USDC, amount, SAMPLE_WALLET, 0])

    order = {
        "schema_version": 1,
        "order_id": "0b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d60",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {
            "value": str(amount),
            "decimals": 6,
        },
        "recipient": "self",
    }

    calls = build_calls(order, SAMPLE_WALLET)
    assert len(calls) == 1
    call = calls[0]

    assert isinstance(call, Call)
    assert call.to == AAVE_V3_POOL
    assert call.value == 0
    assert call.data.lower() == expected_calldata.lower()


def test_withdraw_calldata_matches_independent_fixture(w3: Web3) -> None:
    """Withdraw calldata equals a fixture built independently from Web3 ABI encoding."""
    amount = 50_000_000
    contract = w3.eth.contract(abi=AAVE_POOL_ABI)
    expected_calldata = contract.encode_abi("withdraw", [AAVE_V3_USDC, amount, SAMPLE_WALLET])

    order = {
        "schema_version": 1,
        "order_id": "0b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d60",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "withdraw",
        "token": "USDC",
        "amount": {
            "value": str(amount),
            "decimals": 6,
        },
        "recipient": "self",
    }

    calls = build_calls(order, SAMPLE_WALLET)
    assert len(calls) == 1
    call = calls[0]

    assert isinstance(call, Call)
    assert call.to == AAVE_V3_POOL
    assert call.value == 0
    assert call.data.lower() == expected_calldata.lower()


def test_approve_amount_equals_order_amount_and_no_max_word() -> None:
    """Approve amount equals the exact order amount; calldata contains no max uint word."""
    amounts = [1, 100, 80_000_000, 999_999_999]
    max_word = "f" * 64

    for amt in amounts:
        order = {
            "schema_version": 1,
            "order_id": "0b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d60",
            "created_at": "2026-10-16T09:00:00Z",
            "proposed_by": "planner",
            "protocol": "aave-v3",
            "function": "approve",
            "token": "USDC",
            "amount": {
                "value": str(amt),
                "decimals": 6,
            },
            "recipient": "aave_pool",
        }
        calls = build_calls(order, SAMPLE_WALLET)
        call = calls[0]

        # Verify the calldata ends with the exact 32-byte encoded amount
        expected_amount_hex = hex(amt)[2:].zfill(64)
        assert call.data.endswith(expected_amount_hex)
        # Assert no calldata contains the max word (all 0xff)
        assert max_word not in call.data.lower()


def test_invalid_schema_orders_raise() -> None:
    """Invalid schema orders raise OrderValidationError before any calldata is built."""
    # 1. Unlimited approval (exceeds 30-digit pattern)
    with open(EXAMPLES_DIR / "order.invalid.unlimited-approval.json", encoding="utf-8") as f:
        unlimited_order = json.load(f)
    with pytest.raises(OrderValidationError):
        build_calls(unlimited_order, SAMPLE_WALLET)

    # 2. Float amount
    with open(EXAMPLES_DIR / "order.invalid.float-amount.json", encoding="utf-8") as f:
        float_order = json.load(f)
    with pytest.raises(OrderValidationError):
        build_calls(float_order, SAMPLE_WALLET)

    # 3. Wrong decimals for USDC (e.g. 18 instead of 6)
    with open(EXAMPLES_DIR / "order.invalid.wrong-decimals.json", encoding="utf-8") as f:
        wrong_decimals_order = json.load(f)
    with pytest.raises(OrderValidationError):
        build_calls(wrong_decimals_order, SAMPLE_WALLET)

    # 4. Raw address as recipient instead of enum
    with open(EXAMPLES_DIR / "order.invalid.address-recipient.json", encoding="utf-8") as f:
        raw_address_order = json.load(f)
    with pytest.raises(OrderValidationError):
        build_calls(raw_address_order, SAMPLE_WALLET)

    # 5. Missing required schema_version
    with open(EXAMPLES_DIR / "order.invalid.missing-schema-version.json", encoding="utf-8") as f:
        missing_ver_order = json.load(f)
    with pytest.raises(OrderValidationError):
        build_calls(missing_ver_order, SAMPLE_WALLET)


def test_out_of_enum_orders_raise() -> None:
    """Out-of-enum orders raise OrderValidationError before any calldata is built."""
    base_order = {
        "schema_version": 1,
        "order_id": "0b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d60",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "80000000", "decimals": 6},
        "recipient": "self",
    }

    # Unsupported protocol
    bad_protocol = dict(base_order, protocol="uniswap-v3")
    with pytest.raises(OrderValidationError, match="Unsupported protocol"):
        build_calls(bad_protocol, SAMPLE_WALLET)

    # Unsupported token (WETH is valid in schema but outside Aave adapter enum)
    bad_token = dict(
        base_order,
        token="WETH",
        amount={"value": "1000000000000000000", "decimals": 18},
    )
    with pytest.raises(OrderValidationError, match="Unsupported token"):
        build_calls(bad_token, SAMPLE_WALLET)

    # Unsupported function (exactInputSingle is valid in schema for swap, not Aave)
    bad_function = dict(
        base_order,
        function="exactInputSingle",
        token_out="WETH",
        max_slippage_bps=50,
    )
    with pytest.raises(OrderValidationError, match="Unsupported function"):
        build_calls(bad_function, SAMPLE_WALLET)

    # Unsupported recipient (uniswap_router is valid in schema, not Aave)
    bad_recipient = dict(base_order, recipient="uniswap_router")
    with pytest.raises(OrderValidationError, match="Unsupported recipient"):
        build_calls(bad_recipient, SAMPLE_WALLET)


def test_recipient_resolution_and_validation() -> None:
    """Recipient resolution correctly maps 'self' and 'aave_pool' and rejects invalid inputs."""
    assert resolve_recipient("self", SAMPLE_WALLET) == Web3.to_checksum_address(SAMPLE_WALLET)
    assert resolve_recipient("aave_pool", SAMPLE_WALLET) == AAVE_V3_POOL

    with pytest.raises(ValueError, match="Valid wallet address is required"):
        resolve_recipient("self", "")

    with pytest.raises(ValueError, match="Valid wallet address is required"):
        resolve_recipient("self", "0xinvalid")

    with pytest.raises(ValueError, match="Unsupported recipient"):
        resolve_recipient("uniswap_router", SAMPLE_WALLET)


def test_addresses_constants_and_sources() -> None:
    """Addresses constants match verified Base Sepolia testnet contracts."""
    assert BASE_SEPOLIA_CHAIN_ID == 84532
    assert AAVE_V3_POOL == "0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27"
    assert AAVE_V3_USDC == "0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f"
    assert Web3.is_checksum_address(AAVE_V3_POOL)
    assert Web3.is_checksum_address(AAVE_V3_USDC)


def test_no_network_access_in_calldata_building(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert that building calldata executes strictly offline without network calls."""
    import socket

    def guarded_connect(*args, **kwargs):
        raise RuntimeError("Network connection attempted during unit test!")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    order = {
        "schema_version": 1,
        "order_id": "0b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d60",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "80000000", "decimals": 6},
        "recipient": "self",
    }
    calls = build_calls(order, SAMPLE_WALLET)
    assert len(calls) == 1
