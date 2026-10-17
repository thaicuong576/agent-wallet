"""Aave V3 protocol adapter for Base Sepolia (Chain ID 84532).

Encodes calldata for ERC-20 approve (exact order amount only),
Aave V3 supply, and Aave V3 withdraw from a validated Order contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import eth_abi
import jsonschema
from web3 import Web3

from core.protocols.addresses import (
    AAVE_V3_A_USDC,
    AAVE_V3_FAUCET,
    AAVE_V3_POOL,
    AAVE_V3_USDC,
    BASE_SEPOLIA_CHAIN_ID,
    resolve_recipient,
)
from core.protocols.registry import (
    BuildContext,
    Call,
    PositionLeg,
    PositionReader,
    ProtocolSpec,
    register,
)

# Base Sepolia Aave USDT and aUSDT addresses
# Source: docs/research/testnet-facts-defi-survey.md
AAVE_V3_USDT: Final[str] = "0x0a215D8ba66387DCA84B284D18c3B4ec3de6E54a"
AAVE_V3_A_USDT: Final[str] = "0xce3caae5ed17a7aafceebc897de843fa6cc0c018"

# Allowed values for this adapter, as they were before the registry (zero behaviour change).
SUPPORTED_PROTOCOLS: Final[frozenset[str]] = frozenset({"aave-v3", "wallet"})
SUPPORTED_TOKENS: Final[frozenset[str]] = frozenset({"USDC", "USDT", "ETH"})
_AAVE_V3_TOKENS: Final[frozenset[str]] = frozenset({"USDC", "USDT"})
SUPPORTED_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {"approve", "supply", "withdraw", "mint", "transfer"}
)
SUPPORTED_RECIPIENTS: Final[frozenset[str]] = frozenset({"self", "aave_pool", "owner"})

# Standard function selectors
APPROVE_SELECTOR: Final[str] = "0x095ea7b3"
SUPPLY_SELECTOR: Final[str] = "0x617ba037"
WITHDRAW_SELECTOR: Final[str] = "0x69328dec"
FAUCET_MINT_SELECTOR: Final[str] = "0xc6c3bbe6"
MINT_SELECTOR: Final[str] = FAUCET_MINT_SELECTOR
TRANSFER_SELECTOR: Final[str] = "0xa9059cbb"

# Load Order JSON Schema
SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent / "contracts" / "schemas" / "order.schema.json"
)

with open(SCHEMA_PATH, encoding="utf-8") as _schema_file:
    ORDER_SCHEMA: Final[dict] = json.load(_schema_file)

ORDER_VALIDATOR: Final[jsonschema.Draft202012Validator] = jsonschema.Draft202012Validator(
    ORDER_SCHEMA
)


class OrderValidationError(ValueError):
    """Raised when an order fails schema validation or contains out-of-enum values."""

    pass


def validate_order(order: dict) -> None:
    """Validate order against JSON schema and protocol adapter enum bounds.

    Raises:
        OrderValidationError: If schema validation fails or any enum is out of bounds.
    """
    if not isinstance(order, dict):
        raise OrderValidationError("Order must be a dictionary")

    errors = list(ORDER_VALIDATOR.iter_errors(order))
    if errors:
        raise OrderValidationError(f"Order schema validation failed: {errors[0].message}")

    protocol = order.get("protocol")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise OrderValidationError(
            f"Unsupported protocol for Aave adapter: '{protocol}'. "
            f"Expected one of {sorted(SUPPORTED_PROTOCOLS)}"
        )

    token = order.get("token")
    if token not in SUPPORTED_TOKENS:
        raise OrderValidationError(
            f"Unsupported token for Aave adapter: '{token}'. "
            f"Expected one of {sorted(SUPPORTED_TOKENS)}"
        )
    if protocol == "aave-v3" and token not in _AAVE_V3_TOKENS:
        raise OrderValidationError(
            f"Unsupported token for Aave v3: '{token}'. Expected one of {sorted(_AAVE_V3_TOKENS)}"
        )

    func = order.get("function")
    if func not in SUPPORTED_FUNCTIONS:
        raise OrderValidationError(
            f"Unsupported function for Aave adapter: '{func}'. "
            f"Expected one of {sorted(SUPPORTED_FUNCTIONS)}"
        )

    recipient = order.get("recipient")
    if recipient not in SUPPORTED_RECIPIENTS:
        raise OrderValidationError(
            f"Unsupported recipient for Aave adapter: '{recipient}'. "
            f"Expected one of {sorted(SUPPORTED_RECIPIENTS)}"
        )


def build_approve_calldata(spender: str, amount: int) -> str:
    """Encode ERC-20 approve(address,uint256) calldata with exact amount."""
    if amount <= 0:
        raise ValueError(f"Approve amount must be positive, got {amount}")
    encoded = eth_abi.encode(
        ["address", "uint256"],
        [Web3.to_checksum_address(spender), amount],
    )
    return APPROVE_SELECTOR + encoded.hex()


def build_supply_calldata(
    asset: str, amount: int, on_behalf_of: str, referral_code: int = 0
) -> str:
    """Encode Aave V3 Pool supply(address,uint256,address,uint16) calldata."""
    if amount <= 0:
        raise ValueError(f"Supply amount must be positive, got {amount}")
    encoded = eth_abi.encode(
        ["address", "uint256", "address", "uint16"],
        [
            Web3.to_checksum_address(asset),
            amount,
            Web3.to_checksum_address(on_behalf_of),
            referral_code,
        ],
    )
    return SUPPLY_SELECTOR + encoded.hex()


def build_withdraw_calldata(asset: str, amount: int, to: str) -> str:
    """Encode Aave V3 Pool withdraw(address,uint256,address) calldata."""
    if amount <= 0:
        raise ValueError(f"Withdraw amount must be positive, got {amount}")
    encoded = eth_abi.encode(
        ["address", "uint256", "address"],
        [Web3.to_checksum_address(asset), amount, Web3.to_checksum_address(to)],
    )
    return WITHDRAW_SELECTOR + encoded.hex()


def build_mint_calldata(token: str, to: str, amount: int) -> str:
    """Encode Aave Faucet mint(address,address,uint256) calldata."""
    if amount <= 0:
        raise ValueError(f"Mint amount must be positive, got {amount}")
    encoded = eth_abi.encode(
        ["address", "address", "uint256"],
        [Web3.to_checksum_address(token), Web3.to_checksum_address(to), amount],
    )
    return FAUCET_MINT_SELECTOR + encoded.hex()


def build_transfer_calldata(to: str, amount: int) -> str:
    """Encode ERC-20 transfer(address,uint256) calldata."""
    if amount <= 0:
        raise ValueError(f"Transfer amount must be positive, got {amount}")
    encoded = eth_abi.encode(
        ["address", "uint256"],
        [Web3.to_checksum_address(to), amount],
    )
    return TRANSFER_SELECTOR + encoded.hex()


def build_calls(
    order: dict, wallet_address: str, *, owner_address: str | None = None
) -> list[Call]:
    """Turn a validated Order into a list of Call(to, data, value) objects.

    Args:
        order: Order dictionary to translate.
        wallet_address: Wallet address used when recipient is 'self'.
        owner_address: Owner address used when recipient is 'owner'.

    Returns:
        List of Call objects to execute.

    Raises:
        OrderValidationError: If order fails schema or enum validation.
        ValueError: If amount or address arguments are invalid.
    """
    validate_order(order)

    recipient = order["recipient"]
    try:
        recipient_address = resolve_recipient(
            recipient, wallet_address, owner_address=owner_address
        )
    except ValueError as err:
        raise OrderValidationError(str(err)) from err

    amount_int = int(order["amount"]["value"])
    func = order["function"]
    token = order.get("token")

    def _token_address(t: str | None) -> str:
        if t == "USDC":
            return AAVE_V3_USDC
        if t == "USDT":
            return AAVE_V3_USDT
        raise OrderValidationError(f"Unsupported token for Aave adapter: '{t}'")

    if func == "approve":
        token_addr = _token_address(token)
        data = build_approve_calldata(recipient_address, amount_int)
        return [Call(to=token_addr, data=data, value=0)]

    elif func == "supply":
        token_addr = _token_address(token)
        data = build_supply_calldata(
            asset=token_addr,
            amount=amount_int,
            on_behalf_of=recipient_address,
            referral_code=0,
        )
        return [Call(to=AAVE_V3_POOL, data=data, value=0)]

    elif func == "withdraw":
        token_addr = _token_address(token)
        data = build_withdraw_calldata(
            asset=token_addr,
            amount=amount_int,
            to=recipient_address,
        )
        return [Call(to=AAVE_V3_POOL, data=data, value=0)]

    elif func == "mint":
        token_addr = _token_address(token)
        data = build_mint_calldata(
            token=token_addr,
            to=recipient_address,
            amount=amount_int,
        )
        return [Call(to=AAVE_V3_FAUCET, data=data, value=0)]

    elif func == "transfer":
        # Recipient is owner (resolved server-side); target is USDC contract or owner for ETH
        if token == "USDC":
            data = build_transfer_calldata(to=recipient_address, amount=amount_int)
            return [Call(to=AAVE_V3_USDC, data=data, value=0)]
        elif token == "ETH":
            return [Call(to=recipient_address, data="0x", value=amount_int)]
        raise OrderValidationError(f"Unsupported token for transfer: '{token}'")

    raise OrderValidationError(f"Unhandled function: {func}")


order_to_calls = build_calls


# --- registry wiring ---------------------------------------------------------------------


def _build(order: Mapping[str, Any], ctx: BuildContext) -> list[Call]:
    return build_calls(dict(order), ctx.wallet, owner_address=ctx.owner_address)


def _wallet_positions(wallet: str, reader: PositionReader) -> list[PositionLeg]:
    usdc = reader.address_for("USDC", AAVE_V3_USDC)
    return [PositionLeg("USDC", "wallet", reader.balance_of(usdc), 6)]


def _aave_positions(wallet: str, reader: PositionReader) -> list[PositionLeg]:
    legs = []
    ausdc = reader.address_for("aUSDC", AAVE_V3_A_USDC)
    legs.append(PositionLeg("USDC", "aave-v3", reader.balance_of(ausdc), 6))
    ausdt = reader.address_for("aUSDT", AAVE_V3_A_USDT)
    bal_usdt = reader.balance_of(ausdt)
    if bal_usdt > 0:
        legs.append(PositionLeg("USDT", "aave-v3", bal_usdt, 6))
    return legs


def _describe_aave(order: Mapping[str, Any]) -> str:
    verb = {
        "supply": "Supply",
        "withdraw": "Withdraw",
        "approve": "Approve the Aave pool to spend",
    }.get(str(order.get("function")), str(order.get("function")))
    return f"{verb} {order.get('token')} on Aave v3"


def _describe_wallet(order: Mapping[str, Any]) -> str:
    return f"{order.get('function')} {order.get('token')} in the wallet"


register(
    ProtocolSpec(
        id="wallet",
        name="Wallet",
        category="wallet",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        functions=frozenset({"transfer"}),
        tokens=SUPPORTED_TOKENS,
        spenders=frozenset(),
        build_calls=_build,
        positions=_wallet_positions,
        describe=_describe_wallet,
        owner_functions=frozenset({"transfer"}),
        recipients=frozenset({"owner"}),
        summary="Idle balances held by the agent wallet",
    )
)

register(
    ProtocolSpec(
        id="aave-v3",
        name="Aave v3",
        category="lending",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        functions=frozenset({"approve", "supply", "withdraw", "mint"}),
        tokens=_AAVE_V3_TOKENS,
        spenders=frozenset({AAVE_V3_POOL}),
        build_calls=_build,
        positions=_aave_positions,
        describe=_describe_aave,
        owner_functions=frozenset({"mint"}),
        recipients=frozenset({"self", "aave_pool"}),
        summary="Supply and withdraw USDC and USDT on Aave v3 (Base Sepolia)",
    )
)
