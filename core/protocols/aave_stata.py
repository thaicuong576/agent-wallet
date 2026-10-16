"""Aave Stata (static aToken, ERC-4626) protocol adapter for Base Sepolia (Chain ID 84532).

Encodes calldata for:
- ERC-20 approve (exact order amount, spender is the token's stata vault)
- ERC-4626 deposit(uint256 assets, address receiver) via 'supply'
- ERC-4626 withdraw(uint256 assets, address receiver, address owner) via 'withdraw'

Addresses are verified on-chain against Base Sepolia RPC (docs/design/testnet-facts-defi-survey.md).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import eth_abi
import jsonschema
from web3 import Web3

from core.protocols.addresses import BASE_SEPOLIA_CHAIN_ID
from core.protocols.registry import (
    BuildContext,
    Call,
    PositionLeg,
    PositionReader,
    ProtocolSpec,
    register,
)
from core.protocols.tokens import get_token

# Contract addresses on Base Sepolia (verified on-chain)
# Source: docs/design/testnet-facts-defi-survey.md, section 7
AAVE_STATA_USDC: Final[str] = "0xf430cb6E2b85f99222fBFA6dFEa18Ff60FA6B32a"
AAVE_STATA_USDT: Final[str] = "0xf63dA51069FAe9448747FA425F8Cb84B0149eC0F"

# Mapping from token symbol to underlying and vault addresses
VAULTS: Final[dict[str, str]] = {
    "USDC": AAVE_STATA_USDC,
    "USDT": AAVE_STATA_USDT,
}

SUPPORTED_PROTOCOLS: Final[frozenset[str]] = frozenset({"aave-stata"})
SUPPORTED_TOKENS: Final[frozenset[str]] = frozenset({"USDC", "USDT"})
SUPPORTED_FUNCTIONS: Final[frozenset[str]] = frozenset({"approve", "supply", "withdraw"})
SUPPORTED_RECIPIENTS: Final[frozenset[str]] = frozenset({"self"})

# Selectors (verified on-chain)
APPROVE_SELECTOR: Final[str] = "0x095ea7b3"
DEPOSIT_SELECTOR: Final[str] = "0x6e553f65"  # deposit(uint256,address)
WITHDRAW_SELECTOR: Final[str] = "0xb460af94"  # withdraw(uint256,address,address)
REDEEM_SELECTOR: Final[str] = "0xba087652"  # redeem(uint256,address,address)
CONVERT_TO_ASSETS_SELECTOR: Final[str] = "0x07a2d13a"  # convertToAssets(uint256)

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


def validate_order(order: dict) -> None:
    """Validate order against JSON schema and Aave Stata adapter bounds."""
    if not isinstance(order, dict):
        raise OrderValidationError("Order must be a dictionary")

    errors = list(ORDER_VALIDATOR.iter_errors(order))
    if errors:
        raise OrderValidationError(f"Order schema validation failed: {errors[0].message}")

    protocol = order.get("protocol")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise OrderValidationError(
            f"Unsupported protocol for Aave Stata adapter: '{protocol}'. "
            f"Expected one of {sorted(SUPPORTED_PROTOCOLS)}"
        )

    token = order.get("token")
    if token not in SUPPORTED_TOKENS:
        raise OrderValidationError(
            f"Unsupported token for Aave Stata adapter: '{token}'. "
            f"Expected one of {sorted(SUPPORTED_TOKENS)}"
        )

    func = order.get("function")
    if func not in SUPPORTED_FUNCTIONS:
        raise OrderValidationError(
            f"Unsupported function for Aave Stata adapter: '{func}'. "
            f"Expected one of {sorted(SUPPORTED_FUNCTIONS)}"
        )

    recipient = order.get("recipient")
    if recipient not in SUPPORTED_RECIPIENTS:
        raise OrderValidationError(
            f"Unsupported recipient for Aave Stata adapter: '{recipient}'. "
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


def build_deposit_calldata(assets: int, receiver: str) -> str:
    """Encode ERC-4626 deposit(uint256 assets,address receiver) calldata."""
    if assets <= 0:
        raise ValueError(f"Deposit amount must be positive, got {assets}")
    encoded = eth_abi.encode(
        ["uint256", "address"],
        [assets, Web3.to_checksum_address(receiver)],
    )
    return DEPOSIT_SELECTOR + encoded.hex()


def build_withdraw_calldata(assets: int, receiver: str, owner: str) -> str:
    """Encode ERC-4626 withdraw(uint256 assets,address receiver,address owner) calldata."""
    if assets <= 0:
        raise ValueError(f"Withdraw amount must be positive, got {assets}")
    encoded = eth_abi.encode(
        ["uint256", "address", "address"],
        [
            assets,
            Web3.to_checksum_address(receiver),
            Web3.to_checksum_address(owner),
        ],
    )
    return WITHDRAW_SELECTOR + encoded.hex()


def build_redeem_calldata(shares: int, receiver: str, owner: str) -> str:
    """Encode ERC-4626 redeem(uint256 shares,address receiver,address owner) calldata."""
    if shares <= 0:
        raise ValueError(f"Redeem shares must be positive, got {shares}")
    encoded = eth_abi.encode(
        ["uint256", "address", "address"],
        [
            shares,
            Web3.to_checksum_address(receiver),
            Web3.to_checksum_address(owner),
        ],
    )
    return REDEEM_SELECTOR + encoded.hex()


def build_convert_to_assets_calldata(shares: int) -> str:
    """Encode ERC-4626 convertToAssets(uint256 shares) calldata."""
    if shares < 0:
        raise ValueError(f"Shares must be non-negative, got {shares}")
    encoded = eth_abi.encode(["uint256"], [shares])
    return CONVERT_TO_ASSETS_SELECTOR + encoded.hex()


def _get_underlying_address(token: str) -> str:
    spec = get_token(token)
    if spec is None or spec.address is None:
        raise OrderValidationError(f"Token address unknown for '{token}'")
    return spec.address


def build_calls(
    order: dict, wallet_address: str, *, owner_address: str | None = None
) -> list[Call]:
    """Turn a validated Order into a list of Call objects for Aave Stata vaults."""
    validate_order(order)

    if not wallet_address or not Web3.is_address(wallet_address):
        raise ValueError(f"Valid wallet address required, got '{wallet_address}'")
    wallet = Web3.to_checksum_address(wallet_address)

    token = str(order["token"])
    vault_address = VAULTS[token]
    amount_int = int(order["amount"]["value"])
    func = order["function"]

    if func == "approve":
        # Target is the underlying ERC-20 token; spender is the stata vault
        underlying = _get_underlying_address(token)
        data = build_approve_calldata(spender=vault_address, amount=amount_int)
        return [Call(to=underlying, data=data, value=0)]

    elif func == "supply":
        # Target is the stata vault; calls deposit(assets, receiver=wallet)
        data = build_deposit_calldata(assets=amount_int, receiver=wallet)
        return [Call(to=vault_address, data=data, value=0)]

    elif func == "withdraw":
        # Target is the stata vault; calls withdraw(assets, receiver=wallet, owner=wallet)
        data = build_withdraw_calldata(assets=amount_int, receiver=wallet, owner=wallet)
        return [Call(to=vault_address, data=data, value=0)]

    raise OrderValidationError(f"Unhandled function: {func}")


# --- registry wiring ---------------------------------------------------------------------


def _build(order: Mapping[str, Any], ctx: BuildContext) -> list[Call]:
    return build_calls(dict(order), ctx.wallet, owner_address=ctx.owner_address)


def _stata_positions(wallet: str, reader: PositionReader) -> list[PositionLeg]:
    legs: list[PositionLeg] = []
    for token, default_vault in VAULTS.items():
        vault_addr = reader.address_for(f"stata_{token}", default_vault)
        shares = reader.balance_of(vault_addr)
        if shares <= 0:
            continue

        if hasattr(reader, "convert_to_assets"):
            assets = reader.convert_to_assets(vault_addr, shares)
        elif hasattr(reader, "call"):
            c2a_data = build_convert_to_assets_calldata(shares)
            raw = reader.call(vault_addr, c2a_data)
            try:
                assets = eth_abi.decode(["uint256"], bytes.fromhex(raw.removeprefix("0x")))[0]
            except Exception:
                assets = shares
        else:
            assets = shares

        legs.append(PositionLeg(token=token, location="aave-stata", balance=assets, decimals=6))
    return legs


def _describe_stata(order: Mapping[str, Any]) -> str:
    verb = {
        "supply": "Deposit",
        "withdraw": "Withdraw",
        "approve": "Approve",
    }.get(str(order.get("function")), str(order.get("function")))
    return f"{verb} {order.get('token')} in Aave Stata vault"


register(
    ProtocolSpec(
        id="aave-stata",
        name="Aave Stata",
        category="vault",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        functions=SUPPORTED_FUNCTIONS,
        tokens=SUPPORTED_TOKENS,
        spenders=frozenset({AAVE_STATA_USDC, AAVE_STATA_USDT}),
        build_calls=_build,
        positions=_stata_positions,
        describe=_describe_stata,
        recipients=SUPPORTED_RECIPIENTS,
        summary="Deposit and withdraw USDC and USDT in Aave Stata ERC-4626 vaults (Base Sepolia)",
    )
)
