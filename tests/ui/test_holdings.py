"""Holdings grouping model: static checks on ui/lib/holdings.js, plus the node check."""

from __future__ import annotations

import functools
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
MODULE = UI / "lib" / "holdings.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


def test_holdings_module_has_no_unsafe_api_or_external_url() -> None:
    src = MODULE.read_text(encoding="utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.",
        "eval(",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "fetch(",
        "http://",
        "https://",
    ):
        assert banned not in src, banned


def test_holdings_money_never_goes_through_floats() -> None:
    src = MODULE.read_text(encoding="utf-8")
    for banned in ("parseFloat", "parseInt", "Number(", "toFixed"):
        assert banned not in src, banned


@functools.lru_cache(maxsize=1)
def holdings() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "holdings_check.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_real_fixtures_split_idle_from_lent() -> None:
    h = holdings()
    assert h["real_groups"] == ["idle", "lending"]
    assert h["real_group_usd"] == {"idle": "98.00", "lending": "2.00"}
    assert h["real_total"] == "100.00"
    assert h["real_working"] == "2.00"
    assert h["real_idle"] == "98.00"
    assert h["real_lending_display"] == "aUSDC"
    assert h["real_price_source"] == "mainnet reference"
    assert h["real_gas_ok"] is True


@needs_node
def test_every_location_and_token_lands_in_its_group() -> None:
    h = holdings()
    assert h["synth_groups"] == ["idle", "lending", "vaults", "morpho", "swapped"]
    assert h["synth_group_usd"] == {
        "idle": "110.00",
        "lending": "41.00",
        "vaults": "20.00",
        "morpho": "30.00",
        "swapped": "30.00",
    }
    assert h["synth_group_bps"] == {
        "idle": 4761,
        "lending": 1775,
        "vaults": 866,
        "morpho": 1299,
        "swapped": 1299,
    }
    assert h["synth_rows"] == [
        ["idle", "wallet:USDC", "USDC", None, "98.00", 4242],
        ["idle", "newprotocol:USDC", "USDC", None, "7.00", 303],
        ["idle", "wallet:USDT", "USDT", None, "5.00", 216],
        ["lending", "aave-v3:USDC", "aUSDC", "aave-v3", "40.00", 1732],
        ["lending", "aave-v3:USDT", "aUSDT", "aave-v3", "1.00", 43],
        ["vaults", "aave-stata:USDC", "stataUSDC", "aave-stata", "20.00", 866],
        ["morpho", "morpho-blue:USDC", "USDC", "morpho-blue", "30.00", 1299],
        ["swapped", "wallet:WBTC", "WBTC", "uniswap-v3", "20.00", 866],
        ["swapped", "wallet:WETH", "WETH", "uniswap-v3", "10.00", 433],
    ]
    assert "wallet:LINK" not in [row[1] for row in h["synth_rows"]]
    assert h["synth_total"] == "231.00"
    assert h["synth_working"] == "121.00"
    assert h["synth_idle"] == "110.00"
    assert h["synth_price_source"] == "mainnet reference"


@needs_node
def test_segments_have_one_decimal_pct_per_group() -> None:
    assert holdings()["synth_segments"] == [
        ["idle", 4761, 47.6],
        ["lending", 1775, 17.8],
        ["vaults", 866, 8.7],
        ["morpho", 1299, 13],
        ["swapped", 1299, 13],
    ]


@needs_node
def test_equal_values_sort_by_symbol_and_unpriced_rows_are_kept() -> None:
    h = holdings()
    assert h["tie_rows"] == [
        ["LINK", "3.00", True, 5],
        ["WETH", "3.00", True, 5],
        ["cbETH", "0.00", False, 0],
    ]
    assert h["tie_group"] == ["swapped", "6.00", 10]
    assert h["tie_segments"] == [["swapped", 10, 0.1]]


@needs_node
def test_sum_usd_uses_cents_and_skips_invalid_items() -> None:
    h = holdings()
    assert h["sum_basic"] == "101.50"
    assert h["sum_empty"] == "0.00"
    assert h["sum_not_array"] == "0.00"
    assert h["sum_round"] == "0.01"


@needs_node
def test_malformed_input_never_throws() -> None:
    cases = holdings()["malformed"]
    assert all(c["threw"] is False for c in cases), cases
    null_portfolio, undefined_both, string_portfolio, odd_positions, bad_rows = cases[:5]
    assert null_portfolio == {
        "threw": False,
        "priced": False,
        "empty": True,
        "groups": 0,
        "totalUsd": None,
        "gasOk": True,
    }
    assert undefined_both["gasOk"] is None
    assert string_portfolio["priced"] is False and string_portfolio["empty"] is True
    assert odd_positions["priced"] is False and odd_positions["empty"] is True
    assert bad_rows["priced"] is True and bad_rows["empty"] is True


@needs_node
def test_gas_is_ok_only_at_or_above_the_floor_and_above_zero() -> None:
    assert holdings()["gas"] == {
        "below": False,
        "zero": False,
        "garbage": False,
        "equal": True,
    }
