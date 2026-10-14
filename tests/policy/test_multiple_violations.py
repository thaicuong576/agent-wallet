"""Tests verifying multiple rule violations simultaneously appear in rules_hit."""

from __future__ import annotations

import copy
from typing import Any

from core.policy.engine import evaluate
from core.policy.ips import LoadedIps


def test_access_and_limit_multiple_violations_all_appear(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """An order violating multiple ACCESS and LIMIT rules reports all violated codes."""
    order = copy.deepcopy(valid_order)
    order["token"] = "BAD_TOKEN"  # ACCESS.TOKEN_NOT_ALLOWED
    order["protocol"] = "bad-protocol"  # ACCESS.PROTOCOL_NOT_ALLOWED
    order["function"] = "badFunction"  # ACCESS.FUNCTION_NOT_ALLOWED
    order["recipient"] = "bad_recipient"  # ACCESS.RECIPIENT_NOT_ALLOWED

    state = copy.deepcopy(valid_state)
    state["tx_count_1h"] = 10  # LIMIT.TX_PER_HOUR (max 5)

    decision = evaluate(loaded_ips, state, order)
    assert decision["decision"] == "deny"
    hit_codes = {h["code"] for h in decision["rules_hit"]}

    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes
    assert "ACCESS.PROTOCOL_NOT_ALLOWED" in hit_codes
    assert "ACCESS.FUNCTION_NOT_ALLOWED" in hit_codes
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in hit_codes
    assert "LIMIT.TX_PER_HOUR" in hit_codes
    # Since BAD_TOKEN has no price in state, LIMIT.PER_TX also fires for missing price
    assert "LIMIT.PER_TX" in hit_codes

    assert len(decision["rules_hit"]) == 6


def test_access_and_limit_all_limit_violations_appear(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """An order triggering PER_TX, PER_DAY, and TX_PER_HOUR simultaneously lists all three."""
    order = copy.deepcopy(valid_order)
    # 150 USDC > per_tx_usd 100 -> triggers LIMIT.PER_TX
    order["amount"] = {"value": "150000000", "decimals": 6}

    state = copy.deepcopy(valid_state)
    # spent_24h_usd 200 + order 150 = 350 > per_day_usd 300 -> triggers LIMIT.PER_DAY
    state["spent_24h_usd"] = "200.00"
    # tx_count_1h 5 >= max 5 -> triggers LIMIT.TX_PER_HOUR
    state["tx_count_1h"] = 5

    decision = evaluate(loaded_ips, state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]

    assert "LIMIT.PER_TX" in hit_codes
    assert "LIMIT.PER_DAY" in hit_codes
    assert "LIMIT.TX_PER_HOUR" in hit_codes
    assert len(hit_codes) == 3
