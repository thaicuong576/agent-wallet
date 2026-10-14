"""Tests for LIMIT policy rule evaluations (allowed and blocked)."""

from __future__ import annotations

import copy
from typing import Any

from core.policy.engine import evaluate
from core.policy.ips import LoadedIps


def test_limit_rule_per_tx_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Order with USD value below per_tx_usd passes limit evaluation."""
    order = copy.deepcopy(valid_order)
    # 80 USDC * 1.00 = 80 USD <= 100 USD per_tx_usd
    order["amount"] = {"value": "80000000", "decimals": 6}
    decision = evaluate(loaded_ips, valid_state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.PER_TX" not in hit_codes


def test_limit_rule_per_tx_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Order with USD value exactly equal to per_tx_usd passes limit evaluation."""
    order = copy.deepcopy(valid_order)
    # 100 USDC * 1.00 = 100 USD == 100 USD per_tx_usd
    order["amount"] = {"value": "100000000", "decimals": 6}
    decision = evaluate(loaded_ips, valid_state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.PER_TX" not in hit_codes


def test_limit_rule_per_tx_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Order with USD value exceeding per_tx_usd triggers LIMIT.PER_TX."""
    order = copy.deepcopy(valid_order)
    # 150 USDC * 1.00 = 150 USD > 100 USD per_tx_usd
    order["amount"] = {"value": "150000000", "decimals": 6}
    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.PER_TX" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "LIMIT.PER_TX")
    assert hit["evidence"] == {"order_usd": "150.00", "per_tx_usd": "100"}
    assert (
        "Order value 150.00 USD is above the per-transaction limit of 100 USD" in decision["reason"]
    )


def test_limit_rule_per_tx_missing_price_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Order with token missing from portfolio state positions triggers LIMIT.PER_TX."""
    order = copy.deepcopy(valid_order)
    # WETH is allowed in IPS universe, but we remove WETH position from state
    order["token"] = "WETH"
    order["amount"] = {"value": "10000000000000000", "decimals": 18}

    state = copy.deepcopy(valid_state)
    state["positions"] = [p for p in state["positions"] if p["token"] != "WETH"]

    decision = evaluate(loaded_ips, state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.PER_TX" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "LIMIT.PER_TX")
    assert hit["evidence"]["price_missing"] is True
    assert hit["evidence"]["token"] == "WETH"
    assert "Cannot evaluate transaction value" in decision["reason"]


def test_limit_rule_per_day_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Projected daily spend <= per_day_usd passes limit evaluation."""
    order = copy.deepcopy(valid_order)
    # 80 USDC = 80 USD. spent_24h_usd = 120.00 USD. Total = 200.00 USD <= 300 USD
    order["amount"] = {"value": "80000000", "decimals": 6}
    state = copy.deepcopy(valid_state)
    state["spent_24h_usd"] = "120.00"

    decision = evaluate(loaded_ips, state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.PER_DAY" not in hit_codes


def test_limit_rule_per_day_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Projected daily spend exactly equal to per_day_usd passes limit evaluation."""
    order = copy.deepcopy(valid_order)
    # 100 USDC = 100 USD. spent_24h_usd = 200.00 USD. Total = 300.00 USD == 300 USD
    order["amount"] = {"value": "100000000", "decimals": 6}
    state = copy.deepcopy(valid_state)
    state["spent_24h_usd"] = "200.00"

    decision = evaluate(loaded_ips, state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.PER_DAY" not in hit_codes


def test_limit_rule_per_day_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Projected daily spend exceeding per_day_usd triggers LIMIT.PER_DAY."""
    order = copy.deepcopy(valid_order)
    # 90 USDC = 90 USD (under per_tx_usd 100). spent_24h_usd = 220.00. Total = 310.00 > 300 USD
    order["amount"] = {"value": "90000000", "decimals": 6}
    state = copy.deepcopy(valid_state)
    state["spent_24h_usd"] = "220.00"

    decision = evaluate(loaded_ips, state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.PER_DAY" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "LIMIT.PER_DAY")
    assert hit["evidence"]["spent_24h_usd"] == "220.00"
    assert hit["evidence"]["order_usd"] == "90.00"
    assert hit["evidence"]["projected_24h_usd"] == "310.00"
    assert hit["evidence"]["per_day_usd"] == "300"
    assert (
        "push 24-hour spend to 310.00 USD, above the daily limit of 300 USD" in decision["reason"]
    )


def test_limit_rule_tx_per_hour_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Hourly transaction count strictly below max_tx_per_hour passes evaluation."""
    state = copy.deepcopy(valid_state)
    state["tx_count_1h"] = 4  # max is 5

    decision = evaluate(loaded_ips, state, valid_order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.TX_PER_HOUR" not in hit_codes


def test_limit_rule_tx_per_hour_exact_boundary_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Hourly transaction count already equal to max_tx_per_hour triggers LIMIT.TX_PER_HOUR."""
    state = copy.deepcopy(valid_state)
    state["tx_count_1h"] = 5  # max is 5

    decision = evaluate(loaded_ips, state, valid_order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.TX_PER_HOUR" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "LIMIT.TX_PER_HOUR")
    assert hit["evidence"] == {"tx_count_1h": 5, "max_tx_per_hour": 5}
    assert "already at or above limit of 5" in decision["reason"]


def test_limit_rule_tx_per_hour_above_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Hourly transaction count above max_tx_per_hour triggers LIMIT.TX_PER_HOUR."""
    state = copy.deepcopy(valid_state)
    state["tx_count_1h"] = 8  # max is 5

    decision = evaluate(loaded_ips, state, valid_order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "LIMIT.TX_PER_HOUR" in hit_codes
