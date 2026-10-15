"""Reference prices for runner portfolio valuation.

Labels all values as mainnet_reference (not testnet market prices).
Valuation lives in the token registry (`core.protocols.tokens.usd_value`); this module keeps the
historic `price_usd` entry point on top of it. Unknown tokens or missing prices raise
PriceUnavailable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from core.protocols.tokens import PriceUnavailable, all_tokens, unit_price_usd

__all__ = ["MAINNET_REFERENCE_PRICES", "PRICE_SOURCE", "PriceUnavailable", "price_usd"]

PRICE_SOURCE: Final[str] = "mainnet_reference"

# Tokens that resolve without a live reader: enabled, stable-pegged tokens (USDC in v1).
MAINNET_REFERENCE_PRICES: Final[dict[str, str]] = {
    t.symbol: t.peg_usd for t in all_tokens() if t.enabled and t.usd_source == "stable"
}


def price_usd(token: str) -> Decimal:
    """Return the reference price in USD for one whole token (registry-backed).

    Only `stable` tokens resolve without a live reader; oracle-priced tokens raise
    PriceUnavailable here and are valued where an RPC reader is available.

    Raises:
        PriceUnavailable: If no price can be established for the token.
    """
    return unit_price_usd(token)
