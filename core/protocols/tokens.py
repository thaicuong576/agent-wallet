"""Token registry and the single USD valuation function.

Every USD value used by limits, the ledger and the portfolio reader goes through
`usd_value`. A token whose price cannot be established raises `PriceUnavailable`;
callers fail closed (the order is denied or the position is omitted, nothing is signed).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Final, Literal

import eth_abi
from web3 import Web3

UsdSource = Literal["stable", "aave_oracle", "unavailable"]

# AaveOracle on Base Sepolia (8 decimals). Source: docs/design/testnet-facts-defi-survey.md
AAVE_ORACLE: Final[str] = "0x943b0de18d4abf4ef02a85912f8fc07684c141df"
GET_ASSET_PRICE_SELECTOR: Final[str] = "0xb3596f07"  # getAssetPrice(address)
ORACLE_DECIMALS: Final[int] = 8

# A stable token is priced at its peg. If an observed price is supplied it must sit in the band.
DEFAULT_PEG_BAND_BPS: Final[int] = 500

RpcCall = Callable[[str, list[Any]], Any]


class PriceUnavailable(Exception):
    """Raised when a USD price cannot be established for a token."""


@dataclass(frozen=True)
class TokenSpec:
    """One token the system knows about. Data only: listing a token enables nothing."""

    symbol: str
    address: str | None  # None for the native asset
    decimals: int
    usd_source: UsdSource
    peg_usd: str = "1.00"  # only used when usd_source == "stable"
    peg_band_bps: int = DEFAULT_PEG_BAND_BPS
    enabled: bool = False  # data only until an adapter uses the token and enables it


_TOKENS: dict[str, TokenSpec] = {}


def register_token(spec: TokenSpec) -> None:
    if spec.symbol in _TOKENS:
        raise ValueError(f"token already registered: {spec.symbol}")
    _TOKENS[spec.symbol] = spec


def enable_token(symbol: str) -> None:
    """Make a listed token priceable. Called by the adapter that starts using the token."""
    _TOKENS[symbol] = replace(_TOKENS[symbol], enabled=True)


def get_token(symbol: str) -> TokenSpec | None:
    return _TOKENS.get(symbol)


def all_tokens() -> list[TokenSpec]:
    return list(_TOKENS.values())


def _wire() -> None:
    # Addresses from docs/design/testnet-facts-defi-survey.md (Base Sepolia, 84532).
    # USDC is Aave's testnet mock USDC, not Circle native USDC.
    register_token(
        TokenSpec("USDC", "0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f", 6, "stable", enabled=True)
    )
    register_token(TokenSpec("ETH", None, 18, "unavailable"))
    register_token(
        TokenSpec("USDT", "0x0a215D8ba66387DCA84B284D18c3B4ec3de6E54a", 6, "stable", enabled=True)
    )
    register_token(
        TokenSpec("WETH", "0x4200000000000000000000000000000000000006", 18, "aave_oracle")
    )
    register_token(
        TokenSpec("WBTC", "0x54114591963CF60EF3aA63bEfD6eC263D98145a4", 8, "aave_oracle")
    )
    register_token(
        TokenSpec("cbETH", "0xD171b9694f7A2597Ed006D41f7509aaD4B485c4B", 18, "aave_oracle")
    )
    register_token(
        TokenSpec("LINK", "0x810D46F9a9027E28F9B01F75E2bdde839dA61115", 18, "aave_oracle")
    )
    register_token(TokenSpec("USDbC", "0x4616975f7E6045FD9737e95bC527e9e14c727BD2", 6, "stable"))


_wire()


def _oracle_price(spec: TokenSpec, rpc: RpcCall) -> Decimal:
    """Read AaveOracle.getAssetPrice(asset) once. Any failure or a zero price is fail-closed."""
    if spec.address is None:
        raise PriceUnavailable(f"token '{spec.symbol}' has no address for an oracle read")
    data = (
        GET_ASSET_PRICE_SELECTOR
        + eth_abi.encode(["address"], [Web3.to_checksum_address(spec.address)]).hex()
    )
    try:
        res = rpc("eth_call", [{"to": AAVE_ORACLE, "data": data}, "latest"])
        if isinstance(res, Mapping):
            if "error" in res:
                raise RuntimeError(str(res["error"]))
            res = res.get("result")
        raw = int(res, 16) if isinstance(res, str) else int(res)
    except Exception as exc:
        raise PriceUnavailable(f"oracle read failed for '{spec.symbol}': {exc}") from exc
    if raw <= 0:
        raise PriceUnavailable(f"oracle returned no price for '{spec.symbol}'")
    return Decimal(raw) / (Decimal(10) ** ORACLE_DECIMALS)


def unit_price_usd(
    token: str,
    *,
    rpc: RpcCall | None = None,
    observed_price: Decimal | str | None = None,
) -> Decimal:
    """USD price of one whole token. Raises PriceUnavailable when it cannot be established."""
    spec = _TOKENS.get(token)
    if spec is None or not spec.enabled:
        raise PriceUnavailable(f"No reference price available for token '{token}'")
    if spec.usd_source == "stable":
        peg = Decimal(spec.peg_usd)
        if observed_price is not None:
            observed = Decimal(str(observed_price))
            band = peg * Decimal(spec.peg_band_bps) / Decimal(10000)
            if abs(observed - peg) > band:
                raise PriceUnavailable(
                    f"observed price {observed} for '{token}' is outside the peg band"
                )
        return peg
    if spec.usd_source == "aave_oracle":
        if rpc is None:
            raise PriceUnavailable(f"no oracle reader available to price '{token}'")
        return _oracle_price(spec, rpc)
    raise PriceUnavailable(f"No reference price available for token '{token}'")


def usd_value(
    token: str,
    amount: int,
    *,
    rpc: RpcCall | None = None,
    observed_price: Decimal | str | None = None,
) -> Decimal:
    """USD value of `amount` (smallest unit) of `token`: the one valuation function."""
    price = unit_price_usd(token, rpc=rpc, observed_price=observed_price)
    spec = _TOKENS[token]
    return Decimal(amount) / (Decimal(10) ** spec.decimals) * price
