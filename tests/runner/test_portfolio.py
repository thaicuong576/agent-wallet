"""Tests for portfolio reader and replay portfolio reader."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.policy.engine import evaluate
from core.runner.portfolio import (
    PORTFOLIO_VALIDATOR,
    PortfolioReader,
    PortfolioUnavailable,
    ReplayPortfolioReader,
)
from core.runner.prices import PRICE_SOURCE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLES_DIR = REPO_ROOT / "contracts" / "examples"
TEST_WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"


class FakeLedger:
    """Fake AuditLedger returning injected limit query counters."""

    def __init__(self, spent_24h: Decimal = Decimal("120.00"), count_1h: int = 1) -> None:
        self.spent_24h = spent_24h
        self.count_1h = count_1h
        self.queries: list[tuple[str, datetime]] = []

    def spent_usd_since(self, since: datetime) -> Decimal:
        self.queries.append(("spent", since))
        return self.spent_24h

    def executed_count_since(self, since: datetime) -> int:
        self.queries.append(("count", since))
        return self.count_1h


def make_mock_rpc(
    eth_balance: int = 10**18,  # 1 ETH
    usdc_balance: int = 500_000_000,  # 500 USDC
    ausdc_balance: int = 300_000_000,  # 300 aUSDC
    extra_balances: dict[str, int] | None = None,
) -> Callable[[str, list[Any]], Any]:
    extra = extra_balances or {}

    def rpc(method: str, params: list[Any]) -> Any:
        if method == "eth_getBalance":
            return hex(eth_balance)
        if method == "eth_call":
            call_obj = params[0]
            to_addr = call_obj.get("to", "").lower()
            from core.protocols.addresses import AAVE_V3_A_USDC, AAVE_V3_USDC

            if to_addr == AAVE_V3_USDC.lower():
                return hex(usdc_balance)
            if to_addr == AAVE_V3_A_USDC.lower():
                return hex(ausdc_balance)
            for addr, bal in extra.items():
                if to_addr == addr.lower():
                    return hex(bal)
            return hex(0)
        raise ValueError(f"Unexpected RPC call: {method} {params}")

    return rpc


def test_portfolio_reader_live_shape_and_validation() -> None:
    """PortfolioReader.read(now) builds a state validating against schema from RPC."""
    rpc = make_mock_rpc(eth_balance=2 * 10**18, usdc_balance=500_000_000, ausdc_balance=300_000_000)
    ledger = FakeLedger(spent_24h=Decimal("45.50"), count_1h=3)
    reader = PortfolioReader(rpc=rpc, wallet=TEST_WALLET, ledger=ledger)

    now = datetime(2026, 10, 16, 8, 59, 0, tzinfo=UTC)
    snapshot = reader.read(now)

    # 1. Native ETH wei is returned separately on the snapshot
    assert snapshot.eth_wei == 2 * 10**18

    state = snapshot.state
    # 2. Schema validation succeeds
    PORTFOLIO_VALIDATOR.validate(state)

    # 3. Labelled with price_source mainnet_reference
    assert state["price_source"] == "mainnet_reference"
    assert state["schema_version"] == 1
    assert state["as_of"] == "2026-10-16T08:59:00Z"

    # 4. Positions: USDC at wallet and USDC at aave-v3
    positions = state["positions"]
    assert len(positions) == 2

    wallet_pos = next(p for p in positions if p["location"] == "wallet")
    assert wallet_pos["token"] == "USDC"
    assert wallet_pos["balance"] == {"value": "500000000", "decimals": 6}
    assert wallet_pos["price_usd"] == "1.00"
    assert wallet_pos["value_usd"] == "500.00"
    assert wallet_pos["weight_bps"] == 6250  # 500 / 800 * 10000

    aave_pos = next(p for p in positions if p["location"] == "aave-v3")
    assert aave_pos["token"] == "USDC"
    assert aave_pos["balance"] == {"value": "300000000", "decimals": 6}
    assert aave_pos["price_usd"] == "1.00"
    assert aave_pos["value_usd"] == "300.00"
    assert aave_pos["weight_bps"] == 3750  # 300 / 800 * 10000

    assert state["total_value_usd"] == "800.00"

    # 5. Limits filled strictly from ledger
    assert state["spent_24h_usd"] == "45.50"
    assert state["tx_count_1h"] == 3

    # Check ledger queries had exact time windows
    assert len(ledger.queries) == 2
    assert ledger.queries[0] == ("spent", now - timedelta(hours=24))
    assert ledger.queries[1] == ("count", now - timedelta(hours=1))


def test_portfolio_reader_never_returns_zeros_on_rpc_failure() -> None:
    """RPC failure raises PortfolioUnavailable and never substitutes zeros."""

    # 1. eth_getBalance failure
    def rpc_eth_fail(method: str, params: list[Any]) -> Any:
        if method == "eth_getBalance":
            raise RuntimeError("RPC node offline")
        return hex(0)

    reader = PortfolioReader(rpc=rpc_eth_fail, wallet=TEST_WALLET, ledger=FakeLedger())
    with pytest.raises(PortfolioUnavailable, match="eth_getBalance failed"):
        reader.read(datetime.now(UTC))

    # 2. RPC returns error dict
    def rpc_err_dict(method: str, params: list[Any]) -> Any:
        return {"error": {"code": -32000, "message": "execution reverted"}}

    reader2 = PortfolioReader(rpc=rpc_err_dict, wallet=TEST_WALLET, ledger=FakeLedger())
    with pytest.raises(PortfolioUnavailable, match="returned error"):
        reader2.read(datetime.now(UTC))

    # 3. Empty RPC response
    def rpc_empty(method: str, params: list[Any]) -> Any:
        return ""

    reader3 = PortfolioReader(rpc=rpc_empty, wallet=TEST_WALLET, ledger=FakeLedger())
    with pytest.raises(PortfolioUnavailable, match="returned empty response"):
        reader3.read(datetime.now(UTC))


def test_portfolio_reader_does_not_cache_balances() -> None:
    """Balances must never be cached across calls to read()."""
    current_usdc = 100_000_000

    def rpc(method: str, params: list[Any]) -> Any:
        if method == "eth_getBalance":
            return hex(10**18)
        if method == "eth_call":
            from core.protocols.addresses import AAVE_V3_USDC

            call_obj = params[0]
            if call_obj.get("to", "").lower() == AAVE_V3_USDC.lower():
                return hex(current_usdc)
            return hex(0)
        return hex(0)

    reader = PortfolioReader(rpc=rpc, wallet=TEST_WALLET, ledger=FakeLedger())
    now = datetime.now(UTC)

    snap1 = reader.read(now)
    pos1 = next(p for p in snap1.state["positions"] if p["location"] == "wallet")
    assert pos1["value_usd"] == "100.00"

    # Balance updates on-chain
    current_usdc = 400_000_000
    snap2 = reader.read(now)
    pos2 = next(p for p in snap2.state["positions"] if p["location"] == "wallet")
    assert pos2["value_usd"] == "400.00"


def test_replay_portfolio_reader_loads_fixture() -> None:
    """ReplayPortfolioReader loads a fixture file with the same output shape."""
    fixture_path = EXAMPLES_DIR / "portfolio-state.valid.json"
    ledger = FakeLedger(spent_24h=Decimal("99.99"), count_1h=5)

    reader = ReplayPortfolioReader(fixture=fixture_path, ledger=ledger)
    now = datetime(2026, 10, 16, 9, 0, 0, tzinfo=UTC)
    snapshot = reader.read(now)

    state = snapshot.state
    PORTFOLIO_VALIDATOR.validate(state)

    assert state["price_source"] == "mainnet_reference"
    assert state["as_of"] == "2026-10-16T09:00:00Z"
    # Limits filled from ledger, overriding fixture limits
    assert state["spent_24h_usd"] == "99.99"
    assert state["tx_count_1h"] == 5


def test_replay_portfolio_reader_missing_fixture() -> None:
    """Non-existent fixture path raises PortfolioUnavailable."""
    reader = ReplayPortfolioReader(fixture=Path("nonexistent.json"), ledger=FakeLedger())
    with pytest.raises(PortfolioUnavailable, match="Fixture file not found"):
        reader.read(datetime.now(UTC))


def test_missing_price_omits_position_and_engine_denies() -> None:
    """Unknown token or missing price denies, never zero.

    When a token lacks a reference price (such as WETH in v1), it is omitted from
    portfolio state positions. Any order proposing an action with that token cannot
    find a price in portfolio state, causing the policy engine to fail closed and
    deny with LIMIT.PER_TX (price_missing=True).
    """
    # 1. Verify WETH position in portfolio-state.valid.json is omitted
    fixture_path = EXAMPLES_DIR / "portfolio-state.valid.json"
    raw_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert any(p.get("token") == "WETH" for p in raw_fixture.get("positions", []))

    reader = ReplayPortfolioReader(fixture=fixture_path, ledger=FakeLedger())
    now = datetime(2026, 10, 16, 9, 0, 0, tzinfo=UTC)
    snapshot = reader.read(now)

    # Position with WETH is omitted
    tokens_in_state = [p["token"] for p in snapshot.state["positions"]]
    assert "WETH" not in tokens_in_state
    assert "USDC" in tokens_in_state

    # 2. Evaluate a WETH order against this state with the policy engine
    ips_data = json.loads((EXAMPLES_DIR / "ips.valid.json").read_text(encoding="utf-8"))
    order_weth = {
        "schema_version": 1,
        "order_id": "test-order-weth-1",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "WETH",
        "amount": {"value": "1000000000000000000", "decimals": 18},
        "recipient": "aave_pool",
    }

    decision = evaluate(ips_data, snapshot.state, order=order_weth)

    # The engine denies fail-closed
    assert decision["decision"] == "deny"
    rule_codes = [r["code"] for r in decision["rules_hit"]]
    assert "LIMIT.PER_TX" in rule_codes

    limit_rule = next(r for r in decision["rules_hit"] if r["code"] == "LIMIT.PER_TX")
    assert limit_rule["evidence"]["price_missing"] is True
    assert limit_rule["evidence"]["token"] == "WETH"


def test_price_source_labelled_mainnet_reference() -> None:
    """Records and eval report say prices are reference values."""
    rpc = make_mock_rpc()
    reader = PortfolioReader(rpc=rpc, wallet=TEST_WALLET, ledger=FakeLedger())
    snapshot = reader.read(datetime.now(UTC))

    assert snapshot.state["price_source"] == PRICE_SOURCE
    assert snapshot.state["price_source"] == "mainnet_reference"

    # Schema only accepts "mainnet_reference"
    allowed_sources = PORTFOLIO_VALIDATOR.schema["properties"]["price_source"]["enum"]
    assert allowed_sources == ["mainnet_reference"]


def test_portfolio_reader_concurrent_under_serial_time_and_identical_state() -> None:
    """Fake RPC sleeping 50ms per call finishes well under serial time with identical state."""
    import time

    from core.protocols import registry
    from core.runner.portfolio import _finalize_positions, _make_raw_position

    call_delay = 0.05  # 50 ms
    specs = registry.all_specs()
    assert len(specs) == 5

    def fake_rpc(method: str, params: list[Any]) -> Any:
        time.sleep(call_delay)
        if method == "eth_getBalance":
            return hex(2 * 10**18)
        if method == "eth_call":
            call_obj = params[0]
            to_addr = call_obj.get("to", "").lower()
            from core.protocols.addresses import AAVE_V3_A_USDC, AAVE_V3_USDC

            if to_addr == AAVE_V3_USDC.lower():
                return hex(500_000_000)
            if to_addr == AAVE_V3_A_USDC.lower():
                return hex(300_000_000)
            return "0x" + "00" * 64
        return hex(0)

    ledger = FakeLedger(spent_24h=Decimal("45.50"), count_1h=3)
    reader = PortfolioReader(rpc=fake_rpc, wallet=TEST_WALLET, ledger=ledger)
    now = datetime(2026, 10, 16, 8, 59, 0, tzinfo=UTC)

    # 1. Build serial reference directly using sequential execution
    t0_serial = time.perf_counter()
    ref_eth_wei = reader._call_get_balance()
    ref_raw_positions: list[dict[str, Any]] = []
    for spec in specs:
        for leg in spec.positions(reader.wallet, reader):
            pos = _make_raw_position(leg.token, leg.location, leg.balance, leg.decimals)
            if pos is not None:
                ref_raw_positions.append(pos)
    for token_symbol, token_addr in reader.extra_tokens.items():
        bal = reader._call_balance_of(token_addr)
        decimals = 18 if token_symbol == "WETH" else 6
        extra_pos = _make_raw_position(token_symbol, "wallet", bal, decimals)
        if extra_pos is not None:
            ref_raw_positions.append(extra_pos)
    ref_positions, ref_total_value = _finalize_positions(ref_raw_positions)
    serial_elapsed = time.perf_counter() - t0_serial

    # 2. Run concurrent PortfolioReader.read
    t0_concurrent = time.perf_counter()
    concurrent_snapshot = reader.read(now)
    concurrent_elapsed = time.perf_counter() - t0_concurrent

    # Proves it finishes in well under the serial time
    assert serial_elapsed >= 0.40
    assert concurrent_elapsed < serial_elapsed * 0.6

    # Returns byte-identical state
    assert concurrent_snapshot.eth_wei == ref_eth_wei
    assert concurrent_snapshot.state["positions"] == ref_positions
    assert concurrent_snapshot.state["total_value_usd"] == f"{ref_total_value:.2f}"
    PORTFOLIO_VALIDATOR.validate(concurrent_snapshot.state)


def test_portfolio_reader_concurrent_failed_read_raises_portfolio_unavailable() -> None:
    """Failed RPC read during concurrent execution raises PortfolioUnavailable."""

    def failing_rpc(method: str, params: list[Any]) -> Any:
        if method == "eth_call":
            call_obj = params[0]
            from core.protocols.addresses import AAVE_V3_USDC

            if call_obj.get("to", "").lower() == AAVE_V3_USDC.lower():
                raise RuntimeError("simulated node failure")
        return hex(10**18)

    reader = PortfolioReader(rpc=failing_rpc, wallet=TEST_WALLET, ledger=FakeLedger())
    with pytest.raises(PortfolioUnavailable):
        reader.read(datetime.now(UTC))
