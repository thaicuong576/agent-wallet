"""Morpho Blue protocol adapter for Base Sepolia (Chain ID 84532).

Encodes calldata for ERC-20 approve (exact order amount only),
Morpho Blue supply, and Morpho Blue withdraw from a validated Order.
Whitelisted markets:
  - usdc-wbtc (Aave USDC loan token / Aave WBTC collateral)
  - usdc-cbeth (Aave USDC loan token / Aave cbETH collateral)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import eth_abi
from web3 import Web3

from core.protocols.addresses import AAVE_V3_USDC, BASE_SEPOLIA_CHAIN_ID
from core.protocols.registry import (
    BuildContext,
    Call,
    PositionLeg,
    PositionReader,
    ProtocolSpec,
    register,
)
from core.protocols.tokens import enable_token

# Contract addresses on Base Sepolia (Chain ID 84532)
# Verified on-chain via eth_getCode and view calls; see docs/research/testnet-facts-defi-survey.md
MORPHO_BLUE: Final[str] = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
ADAPTIVE_CURVE_IRM: Final[str] = "0x46415998764C29aB2a25CbeA6254146D50D22687"

# Collaterals and oracles for whitelisted markets
AAVE_WBTC: Final[str] = "0x54114591963CF60EF3aA63bEfD6eC263D98145a4"
WBTC_ORACLE: Final[str] = "0xb22092390D44EF7271a71a371097aBf44aBb6C27"
WBTC_LLTV: Final[int] = 860_000_000_000_000_000  # 0.86e18

AAVE_CBETH: Final[str] = "0xD171b9694f7A2597Ed006D41f7509aaD4B485c4B"
CBETH_ORACLE: Final[str] = "0xF4FbCFDdA39202549ca4f1230E694c32B5A2F80D"
CBETH_LLTV: Final[int] = 915_000_000_000_000_000  # 0.915e18

# Function selectors
APPROVE_SELECTOR: Final[str] = "0x095ea7b3"
SUPPLY_SELECTOR: Final[str] = "0xa99aad89"
WITHDRAW_SELECTOR: Final[str] = "0x5c2bea49"
POSITION_SELECTOR: Final[str] = "0x93c52062"
MARKET_SELECTOR: Final[str] = "0x5c60e39a"

# Virtual shares and assets from Morpho Blue SharesMathLib
VIRTUAL_SHARES: Final[int] = 1_000_000  # 1e6
VIRTUAL_ASSETS: Final[int] = 1


@dataclass(frozen=True)
class MarketConfig:
    """Whitelisted Morpho Blue market configuration."""

    id: str
    key: str
    label: str
    loan_token: str
    collateral_token: str
    oracle: str
    irm: str
    lltv: int

    @property
    def params_tuple(self) -> tuple[str, str, str, str, int]:
        return (
            Web3.to_checksum_address(self.loan_token),
            Web3.to_checksum_address(self.collateral_token),
            Web3.to_checksum_address(self.oracle),
            Web3.to_checksum_address(self.irm),
            self.lltv,
        )


def compute_market_id(
    loan_token: str,
    collateral_token: str,
    oracle: str,
    irm: str,
    lltv: int,
) -> str:
    """Compute keccak256 hash of ABI-encoded MarketParams struct."""
    encoded = eth_abi.encode(
        ["address", "address", "address", "address", "uint256"],
        [
            Web3.to_checksum_address(loan_token),
            Web3.to_checksum_address(collateral_token),
            Web3.to_checksum_address(oracle),
            Web3.to_checksum_address(irm),
            lltv,
        ],
    )
    return "0x" + Web3.keccak(encoded).hex()


WHITELISTED_MARKETS: Final[dict[str, MarketConfig]] = {
    "usdc-wbtc": MarketConfig(
        id="0xa7cc9d96371f4c4b5be131281cd24a410cf6b76dcfb4319004d5bd66045adb97",
        key="usdc-wbtc",
        label="USDC/WBTC",
        loan_token=AAVE_V3_USDC,
        collateral_token=AAVE_WBTC,
        oracle=WBTC_ORACLE,
        irm=ADAPTIVE_CURVE_IRM,
        lltv=WBTC_LLTV,
    ),
    "usdc-cbeth": MarketConfig(
        id="0x532165f57c1077690d6b2f413e5555eba34611df4e625820ca6aa187f3f89b65",
        key="usdc-cbeth",
        label="USDC/cbETH",
        loan_token=AAVE_V3_USDC,
        collateral_token=AAVE_CBETH,
        oracle=CBETH_ORACLE,
        irm=ADAPTIVE_CURVE_IRM,
        lltv=CBETH_LLTV,
    ),
}

SUPPORTED_PROTOCOLS: Final[frozenset[str]] = frozenset({"morpho-blue"})
SUPPORTED_TOKENS: Final[frozenset[str]] = frozenset({"USDC"})
SUPPORTED_FUNCTIONS: Final[frozenset[str]] = frozenset({"approve", "supply", "withdraw"})
SUPPORTED_MARKETS: Final[frozenset[str]] = frozenset(WHITELISTED_MARKETS.keys())


class MorphoOrderValidationError(ValueError):
    """Raised when an order violates Morpho Blue adapter rules."""


def validate_order(order: Mapping[str, Any]) -> None:
    """Validate order against Morpho Blue adapter enum bounds and market rules."""
    protocol = order.get("protocol")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise MorphoOrderValidationError(
            f"Unsupported protocol for Morpho Blue adapter: '{protocol}'"
        )

    token = order.get("token")
    if token not in SUPPORTED_TOKENS:
        raise MorphoOrderValidationError(
            f"Unsupported token for Morpho Blue adapter: '{token}'. Expected USDC only."
        )

    func = order.get("function")
    if func not in SUPPORTED_FUNCTIONS:
        raise MorphoOrderValidationError(
            f"Unsupported function for Morpho Blue adapter: '{func}'. "
            f"Expected one of {sorted(SUPPORTED_FUNCTIONS)}"
        )

    amount_obj = order.get("amount")
    if not isinstance(amount_obj, Mapping) or "value" not in amount_obj:
        raise MorphoOrderValidationError("Order amount object missing or invalid")
    try:
        amount_int = int(amount_obj["value"])
    except (TypeError, ValueError) as err:
        raise MorphoOrderValidationError(f"Invalid amount value: {err}") from err
    if amount_int <= 0:
        raise MorphoOrderValidationError(f"Order amount must be positive, got {amount_int}")

    market = order.get("market")
    if func in ("supply", "withdraw"):
        if market is None:
            opts = sorted(SUPPORTED_MARKETS)
            raise MorphoOrderValidationError(
                f"Market is required for Morpho Blue {func}. Expected one of {opts}"
            )
        if market not in SUPPORTED_MARKETS:
            raise MorphoOrderValidationError(
                f"Unsupported market for Morpho Blue adapter: '{market}'. "
                f"Expected one of {sorted(SUPPORTED_MARKETS)}"
            )
    elif market is not None and market not in SUPPORTED_MARKETS:
        raise MorphoOrderValidationError(
            f"Unsupported market for Morpho Blue adapter: '{market}'. "
            f"Expected one of {sorted(SUPPORTED_MARKETS)}"
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
    market_params: tuple[str, str, str, str, int],
    assets: int,
    shares: int,
    on_behalf: str,
    data: bytes = b"",
) -> str:
    """Encode Morpho Blue supply(MarketParams,uint256,uint256,address,bytes) calldata."""
    if assets <= 0 and shares <= 0:
        raise ValueError("Supply assets or shares must be positive")
    encoded = eth_abi.encode(
        ["(address,address,address,address,uint256)", "uint256", "uint256", "address", "bytes"],
        [
            market_params,
            assets,
            shares,
            Web3.to_checksum_address(on_behalf),
            data,
        ],
    )
    return SUPPLY_SELECTOR + encoded.hex()


def build_withdraw_calldata(
    market_params: tuple[str, str, str, str, int],
    assets: int,
    shares: int,
    on_behalf: str,
    receiver: str,
) -> str:
    """Encode Morpho Blue withdraw(MarketParams,uint256,uint256,address,address) calldata."""
    if assets <= 0 and shares <= 0:
        raise ValueError("Withdraw assets or shares must be positive")
    encoded = eth_abi.encode(
        ["(address,address,address,address,uint256)", "uint256", "uint256", "address", "address"],
        [
            market_params,
            assets,
            shares,
            Web3.to_checksum_address(on_behalf),
            Web3.to_checksum_address(receiver),
        ],
    )
    return WITHDRAW_SELECTOR + encoded.hex()


def build_calls(order: Mapping[str, Any], ctx: BuildContext) -> list[Call]:
    """Turn a validated Morpho Blue order into Call(to, data, value) objects."""
    validate_order(order)

    func = str(order["function"])
    amount_int = int(order["amount"]["value"])
    wallet = Web3.to_checksum_address(ctx.wallet)

    if func == "approve":
        # Target is USDC token contract; spender is Morpho Blue
        data = build_approve_calldata(MORPHO_BLUE, amount_int)
        return [Call(to=AAVE_V3_USDC, data=data, value=0)]

    market_key = str(order["market"])
    market_config = WHITELISTED_MARKETS[market_key]

    if func == "supply":
        # Target is Morpho Blue; assets > 0, shares = 0, onBehalf = wallet, data = 0x
        data = build_supply_calldata(
            market_params=market_config.params_tuple,
            assets=amount_int,
            shares=0,
            on_behalf=wallet,
            data=b"",
        )
        return [Call(to=MORPHO_BLUE, data=data, value=0)]

    if func == "withdraw":
        # Target is Morpho Blue; assets > 0, shares = 0, onBehalf = receiver = wallet
        data = build_withdraw_calldata(
            market_params=market_config.params_tuple,
            assets=amount_int,
            shares=0,
            on_behalf=wallet,
            receiver=wallet,
        )
        return [Call(to=MORPHO_BLUE, data=data, value=0)]

    raise MorphoOrderValidationError(f"Unhandled function: {func}")


def _read_contract(reader: PositionReader, to: str, data: str) -> str:
    """Additive generic read helper dispatching to reader.call, reader.eth_call or reader.rpc."""
    if hasattr(reader, "call"):
        return reader.call(to, data)
    if hasattr(reader, "eth_call"):
        return reader.eth_call(to, data)
    if hasattr(reader, "read_contract"):
        return reader.read_contract(to, data)
    if hasattr(reader, "rpc"):
        res = reader.rpc("eth_call", [{"to": to, "data": data}, "latest"])
        if isinstance(res, Mapping):
            res = res.get("result", "")
        return str(res)
    return "0x"


def positions(wallet: str, reader: PositionReader) -> list[PositionLeg]:
    """Read wallet supply shares for all whitelisted markets and convert to USDC assets."""
    wallet_checksum = Web3.to_checksum_address(wallet)
    legs: list[PositionLeg] = []

    for market in WHITELISTED_MARKETS.values():
        market_bytes = bytes.fromhex(market.id[2:])
        pos_data = (
            POSITION_SELECTOR
            + eth_abi.encode(["bytes32", "address"], [market_bytes, wallet_checksum]).hex()
        )

        try:
            pos_res = _read_contract(reader, MORPHO_BLUE, pos_data)
            if not pos_res or pos_res in ("", "0x"):
                continue
            raw_pos_bytes = bytes.fromhex(pos_res[2:] if pos_res.startswith("0x") else pos_res)
            supply_shares, _borrow_shares, _collateral = eth_abi.decode(
                ["uint256", "uint128", "uint128"], raw_pos_bytes
            )
        except Exception:
            continue

        if supply_shares == 0:
            continue

        # Fetch market totals to calculate assets
        market_data = MARKET_SELECTOR + eth_abi.encode(["bytes32"], [market_bytes]).hex()
        try:
            market_res = _read_contract(reader, MORPHO_BLUE, market_data)
            raw_m_bytes = bytes.fromhex(
                market_res[2:] if market_res.startswith("0x") else market_res
            )
            decoded_m = eth_abi.decode(
                ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"], raw_m_bytes
            )
            total_supply_assets, total_supply_shares = decoded_m[0], decoded_m[1]
            assets = (supply_shares * (total_supply_assets + VIRTUAL_ASSETS)) // (
                total_supply_shares + VIRTUAL_SHARES
            )
        except Exception:
            assets = 0

        if assets > 0:
            legs.append(PositionLeg("USDC", "morpho-blue", assets, 6))

    return legs


def describe(order: Mapping[str, Any]) -> str:
    """Format plain English summary of a Morpho Blue order."""
    func = str(order.get("function", ""))
    token = str(order.get("token", "USDC"))
    market_key = str(order.get("market", ""))
    market_config = WHITELISTED_MARKETS.get(market_key)
    market_label = market_config.label if market_config else market_key

    amount_obj = order.get("amount")
    if isinstance(amount_obj, Mapping) and "value" in amount_obj:
        try:
            val = int(amount_obj["value"])
            dec = int(amount_obj.get("decimals", 6))
            if val % (10**dec) == 0:
                amount_str = str(val // (10**dec))
            else:
                amount_str = f"{Decimal(val) / (Decimal(10) ** dec):f}"
        except Exception:
            amount_str = str(amount_obj.get("value", ""))
    else:
        amount_str = ""

    if func == "supply":
        market_suffix = f" ({market_label} market)" if market_label else ""
        return f"Lend {amount_str} {token} to Morpho{market_suffix}".strip()
    if func == "withdraw":
        market_suffix = f" ({market_label} market)" if market_label else ""
        return f"Withdraw {amount_str} {token} from Morpho{market_suffix}".strip()
    if func == "approve":
        return f"Approve Morpho Blue to spend {amount_str} {token}".strip()

    return f"{func} {amount_str} {token} on Morpho Blue".strip()


# Enable USDC token in token registry
enable_token("USDC")

# Register Morpho Blue ProtocolSpec
register(
    ProtocolSpec(
        id="morpho-blue",
        name="Morpho Blue",
        category="lending",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        functions=SUPPORTED_FUNCTIONS,
        tokens=SUPPORTED_TOKENS,
        spenders=frozenset({MORPHO_BLUE}),
        markets=SUPPORTED_MARKETS,
        recipients=frozenset({"self"}),
        build_calls=build_calls,
        positions=positions,
        describe=describe,
        summary="Supply and withdraw USDC on Morpho Blue markets (Base Sepolia)",
    )
)
