"""Reference prices for runner portfolio valuation.

Labels all values as mainnet_reference (not testnet market prices).
Unknown tokens or missing prices raise PriceUnavailable.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Final

PRICE_SOURCE: Final[str] = "mainnet_reference"

# Mainnet reference prices in USD string format.
# Omit WETH in v1 until a swap ticket exists (positions with WETH are omitted,
# causing the policy engine to deny fail-closed on LIMIT.PER_TX).
MAINNET_REFERENCE_PRICES: Final[Mapping[str, str]] = {
    "USDC": "1.00",
}


class PriceUnavailable(Exception):
    """Raised when a reference price is unavailable for a given token."""


def price_usd(token: str) -> Decimal:
    """Return the mainnet reference price in USD for the given token.

    Args:
        token: Token symbol (e.g., 'USDC').

    Returns:
        Decimal representation of the USD price.

    Raises:
        PriceUnavailable: If the token has no price in MAINNET_REFERENCE_PRICES.
    """
    if token not in MAINNET_REFERENCE_PRICES:
        raise PriceUnavailable(f"No reference price available for token '{token}'")
    return Decimal(MAINNET_REFERENCE_PRICES[token])
