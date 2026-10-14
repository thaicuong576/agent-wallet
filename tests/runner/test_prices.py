"""Tests for reference prices in the runner."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.runner.prices import (
    MAINNET_REFERENCE_PRICES,
    PRICE_SOURCE,
    PriceUnavailable,
    price_usd,
)


def test_unknown_token_raises() -> None:
    """Unknown token or missing price raises PriceUnavailable (never zero or default).

    Enforces the design requirement in section 10 that unknown tokens and missing
    prices raise PriceUnavailable, omitting positions from portfolio state and
    failing closed under policy rules.
    """
    with pytest.raises(PriceUnavailable, match="No reference price available for token 'UNKNOWN'"):
        price_usd("UNKNOWN")

    # WETH is omitted from the price table in v1
    with pytest.raises(PriceUnavailable, match="No reference price available for token 'WETH'"):
        price_usd("WETH")

    with pytest.raises(PriceUnavailable):
        price_usd("")


def test_usdc_reference_price() -> None:
    """USDC has a 1.00 USD reference price."""
    price = price_usd("USDC")
    assert price == Decimal("1.00")
    assert isinstance(price, Decimal)


def test_price_source_constant() -> None:
    """Price source is labelled mainnet_reference."""
    assert PRICE_SOURCE == "mainnet_reference"


def test_mainnet_reference_prices_table() -> None:
    """MAINNET_REFERENCE_PRICES table contains only USDC in v1."""
    assert MAINNET_REFERENCE_PRICES == {"USDC": "1.00"}
    assert "WETH" not in MAINNET_REFERENCE_PRICES
