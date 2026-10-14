"""Tests for ALLOC policy rule evaluations (allowed and blocked)."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from core.policy.engine import evaluate
from core.policy.ips import LoadedIps


def _to_mutable(obj: Any) -> Any:
    """Recursively convert mappingproxy and tuples to mutable dict and list."""
    if isinstance(obj, Mapping):
        return {k: _to_mutable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_mutable(v) for v in obj]
    return obj


def _clone_ips(loaded_ips: LoadedIps) -> dict[str, Any]:
    """Clone mappingproxy loaded IPS to mutable dict."""
    return _to_mutable(loaded_ips.ips)


def _create_balanced_state(
    valid_state: dict[str, Any],
    usdc_wallet: str = "700.00",
    weth_wallet: str = "300.00",
    total_usd: str = "1000.00",
    weth_price: str = "2500.00",
) -> dict[str, Any]:
    """Helper creating a state with customizable balances."""
    state = copy.deepcopy(valid_state)
    state["positions"] = [
        {
            "token": "USDC",
            "location": "wallet",
            "balance": {"value": str(int(float(usdc_wallet) * 1_000_000)), "decimals": 6},
            "price_usd": "1.00",
            "value_usd": usdc_wallet,
            "weight_bps": int(round((float(usdc_wallet) / float(total_usd)) * 10000)),
        },
        {
            "token": "WETH",
            "location": "wallet",
            "balance": {
                "value": str(int((float(weth_wallet) / float(weth_price)) * 10**18)),
                "decimals": 18,
            },
            "price_usd": weth_price,
            "value_usd": weth_wallet,
            "weight_bps": int(round((float(weth_wallet) / float(total_usd)) * 10000)),
        },
    ]
    state["total_value_usd"] = total_usd
    return state


def test_alloc_rule_max_weight_per_asset_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Swap that leaves non-stable asset at or below max_weight_per_asset passes."""
    # State has 700 USD USDC (70%) and 300 USD WETH (30%). Total = 1000 USD.
    state = _create_balanced_state(valid_state, usdc_wallet="700.00", weth_wallet="300.00")
    # Swap 50 USDC for WETH -> WETH becomes 350 USD (35% <= 40% max_weight_per_asset_bps).
    order = copy.deepcopy(valid_swap_order)
    order["amount"] = {"value": "50000000", "decimals": 6}

    decision = evaluate(loaded_ips, state, order, risk_inputs=valid_risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.MAX_WEIGHT_PER_ASSET" not in hit_codes
    assert decision["decision"] == "allow"


def test_alloc_rule_max_weight_per_asset_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Swap that leaves non-stable asset exactly at max_weight_per_asset passes."""
    # State has 700 USD USDC and 300 USD WETH.
    state = _create_balanced_state(valid_state, usdc_wallet="700.00", weth_wallet="300.00")
    # Swap 100 USDC for WETH -> WETH becomes 400 USD (4000 bps == 4000 bps threshold).
    order = copy.deepcopy(valid_swap_order)
    order["amount"] = {"value": "100000000", "decimals": 6}

    decision = evaluate(loaded_ips, state, order, risk_inputs=valid_risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.MAX_WEIGHT_PER_ASSET" not in hit_codes
    assert decision["decision"] == "allow"


def test_alloc_rule_max_weight_per_asset_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Swap pushing non-stable asset above max_weight_per_asset triggers rule."""
    # State has 700 USD USDC and 300 USD WETH.
    state = _create_balanced_state(valid_state, usdc_wallet="700.00", weth_wallet="300.00")
    # Swap 150 USDC for WETH -> WETH becomes 450 USD (4500 bps > 4000 bps threshold).
    # Also adjust per_tx_usd so LIMIT.PER_TX does not fire
    ips = _clone_ips(loaded_ips)
    ips["limits"]["per_tx_usd"] = "500"

    order = copy.deepcopy(valid_swap_order)
    order["amount"] = {"value": "150000000", "decimals": 6}

    decision = evaluate(ips, state, order, risk_inputs=valid_risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.MAX_WEIGHT_PER_ASSET" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ALLOC.MAX_WEIGHT_PER_ASSET")
    assert hit["evidence"]["token"] == "WETH"
    assert hit["evidence"]["weight_bps"] == 4500
    assert hit["evidence"]["max_weight_per_asset_bps"] == 4000
    assert "Asset 'WETH' post-order weight 4500 bps exceeds maximum" in decision["reason"]


def test_alloc_rule_min_stable_weight_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Swap leaving stablecoin weight above min_stable_weight_bps passes."""
    # State has 800 USD USDC and 200 USD WETH.
    state = _create_balanced_state(valid_state, usdc_wallet="800.00", weth_wallet="200.00")
    # Swap 50 USDC for WETH -> USDC becomes 750 USD (7500 bps >= 6000 bps).
    order = copy.deepcopy(valid_swap_order)
    order["amount"] = {"value": "50000000", "decimals": 6}

    decision = evaluate(loaded_ips, state, order, risk_inputs=valid_risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.MIN_STABLE_WEIGHT" not in hit_codes
    assert decision["decision"] == "allow"


def test_alloc_rule_min_stable_weight_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Swap leaving stablecoin weight exactly at min_stable_weight_bps passes."""
    # State has 700 USD USDC and 300 USD WETH.
    state = _create_balanced_state(valid_state, usdc_wallet="700.00", weth_wallet="300.00")
    # Swap 100 USDC for WETH -> USDC becomes 600 USD (6000 bps == 6000 bps).
    order = copy.deepcopy(valid_swap_order)
    order["amount"] = {"value": "100000000", "decimals": 6}

    decision = evaluate(loaded_ips, state, order, risk_inputs=valid_risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.MIN_STABLE_WEIGHT" not in hit_codes
    assert decision["decision"] == "allow"


def test_alloc_rule_min_stable_weight_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Swap dropping stablecoin weight below min_stable_weight_bps triggers rule."""
    # State has 650 USD USDC and 350 USD WETH.
    state = _create_balanced_state(valid_state, usdc_wallet="650.00", weth_wallet="350.00")
    # Swap 80 USDC for WETH -> USDC becomes 570 USD (5700 bps < 6000 bps threshold).
    order = copy.deepcopy(valid_swap_order)
    order["amount"] = {"value": "80000000", "decimals": 6}

    decision = evaluate(loaded_ips, state, order, risk_inputs=valid_risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.MIN_STABLE_WEIGHT" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ALLOC.MIN_STABLE_WEIGHT")
    assert hit["evidence"]["stable_weight_bps"] == 5700
    assert hit["evidence"]["min_stable_weight_bps"] == 6000
    assert "stablecoin weight 5700 bps is below the minimum" in decision["reason"]


def test_alloc_rule_within_rebalance_band_allowed_when_outside_band(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Rebalance swap when portfolio is outside rebalance band passes (AM-01)."""
    # Target stable min: 6000 bps (60%). Target WETH max: 4000 bps (40%).
    # Rebalance band: 500 bps. Total: 200 USD. State: USDC 90 USD (45%), WETH 110 USD (55%).
    # Deviation = 6000 - 4500 = 1500 bps (15%). 1500 bps > 500 bps -> outside band.
    state = _create_balanced_state(
        valid_state, usdc_wallet="90.00", weth_wallet="110.00", total_usd="200.00"
    )
    # Rebalance swap: sell 30 USD of WETH to buy USDC (0.012 WETH at 2500 USD/WETH)
    # Post-order: WETH becomes 80 USD (4000 bps <= 4000 bps), USDC becomes 120 USD (6000 bps)
    order = copy.deepcopy(valid_swap_order)
    order["token"] = "WETH"
    order["token_out"] = "USDC"
    order["amount"] = {"value": "12000000000000000", "decimals": 18}  # 30 USD of WETH

    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["token"] = "WETH"

    decision = evaluate(loaded_ips, state, order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.WITHIN_REBALANCE_BAND" not in hit_codes
    assert decision["decision"] == "allow"


def test_alloc_rule_within_rebalance_band_no_action_when_inside_band(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Rebalance swap when portfolio deviation is inside rebalance band yields no_action (AM-02)."""
    # Target stable min: 6000 bps. Target WETH max: 4000 bps. Rebalance band: 500 bps.
    # State: USDC is 580 USD (58%), WETH is 420 USD (42%). Total: 1000 USD.
    # Deviation = 6000 - 5800 = 200 bps (2%). 200 bps <= 500 bps -> inside band!
    state = _create_balanced_state(valid_state, usdc_wallet="580.00", weth_wallet="420.00")
    order = copy.deepcopy(valid_swap_order)
    order["token"] = "WETH"
    order["token_out"] = "USDC"
    order["amount"] = {"value": "8000000000000000", "decimals": 18}  # 20 USD of WETH

    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["token"] = "WETH"

    decision = evaluate(loaded_ips, state, order, risk_inputs=risk_inputs)
    assert decision["decision"] == "no_action"
    assert decision["outcome"] == "no_action"
    assert "no_action" in decision
    assert decision["no_action"]["rules_hit"] == decision["rules_hit"]

    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.WITHIN_REBALANCE_BAND" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ALLOC.WITHIN_REBALANCE_BAND")
    assert hit["evidence"]["deviation_bps"] == 200
    assert hit["evidence"]["rebalance_band_bps"] == 500
    assert "within rebalance band of 500 bps; no action needed" in decision["reason"]


def test_alloc_rule_within_rebalance_band_when_order_is_none(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Evaluate without an order yields no_action with order_id=None when in band."""
    state = _create_balanced_state(valid_state, usdc_wallet="580.00", weth_wallet="420.00")
    decision = evaluate(loaded_ips, state, order=None, risk_inputs=valid_risk_inputs)
    assert decision["decision"] == "no_action"
    assert decision["outcome"] == "no_action"
    assert decision["order_id"] is None
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ALLOC.WITHIN_REBALANCE_BAND" in hit_codes
