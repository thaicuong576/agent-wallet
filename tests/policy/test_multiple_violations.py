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


def test_access_limit_alloc_risk_multiple_violations_all_appear(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """An order triggering ACCESS, LIMIT, ALLOC, and RISK violations lists all of them."""
    order = copy.deepcopy(valid_swap_order)
    order["protocol"] = "disallowed-protocol"  # ACCESS.PROTOCOL_NOT_ALLOWED
    order["token_out"] = "DISALLOWED_TOKEN"  # ACCESS.TOKEN_NOT_ALLOWED
    order["amount"] = {"value": "200000000", "decimals": 6}  # LIMIT.PER_TX (200 > 100)

    # State: USDC 500 (50%), WETH 500 (50%) -> post swap, stable drops even more
    state = copy.deepcopy(valid_state)
    state["positions"] = [
        {
            "token": "USDC",
            "location": "wallet",
            "balance": {"value": "500000000", "decimals": 6},
            "price_usd": "1.00",
            "value_usd": "500.00",
            "weight_bps": 5000,
        },
        {
            "token": "WETH",
            "location": "wallet",
            "balance": {"value": "200000000000000000", "decimals": 18},
            "price_usd": "2500.00",
            "value_usd": "500.00",
            "weight_bps": 5000,
        },
    ]
    state["total_value_usd"] = "1000.00"
    state["tx_count_1h"] = 10  # LIMIT.TX_PER_HOUR (max 5)

    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["pool_tvl_usd"] = "1000.00"  # RISK.POOL_TVL_TOO_LOW
    risk_inputs["reward_apy_share_bps"] = 9000  # RISK.REWARD_APY_SHARE_TOO_HIGH
    risk_inputs["data_age_seconds"] = 99999  # RISK.DATA_STALE

    decision = evaluate(loaded_ips, state, order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = {h["code"] for h in decision["rules_hit"]}

    assert "ACCESS.PROTOCOL_NOT_ALLOWED" in hit_codes
    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes
    assert "LIMIT.PER_TX" in hit_codes
    assert "LIMIT.TX_PER_HOUR" in hit_codes
    assert "ALLOC.MIN_STABLE_WEIGHT" in hit_codes
    assert "RISK.POOL_TVL_TOO_LOW" in hit_codes
    assert "RISK.REWARD_APY_SHARE_TOO_HIGH" in hit_codes
    assert "RISK.DATA_STALE" in hit_codes
    assert len(decision["rules_hit"]) >= 8
