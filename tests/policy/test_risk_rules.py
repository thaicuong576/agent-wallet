"""Tests for RISK policy rule evaluations (allowed and blocked)."""

from __future__ import annotations

import copy
from typing import Any

from core.policy.engine import evaluate
from core.policy.ips import LoadedIps


def test_risk_rule_pool_tvl_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Pool TVL above min_pool_tvl_usd passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["pool_tvl_usd"] = "120000000.00"  # min is 50000000

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.POOL_TVL_TOO_LOW" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_pool_tvl_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Pool TVL exactly equal to min_pool_tvl_usd passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["pool_tvl_usd"] = "50000000"  # exact boundary

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.POOL_TVL_TOO_LOW" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_pool_tvl_too_low_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Pool TVL below min_pool_tvl_usd triggers RISK.POOL_TVL_TOO_LOW."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["pool_tvl_usd"] = "30000000.00"  # below 50000000

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.POOL_TVL_TOO_LOW" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.POOL_TVL_TOO_LOW")
    assert hit["evidence"]["pool_tvl_usd"] == "30000000.00"
    assert hit["evidence"]["min_pool_tvl_usd"] == "50000000"
    assert "below minimum threshold of 50000000 USD" in decision["reason"]


def test_risk_rule_reward_apy_share_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Reward APY share below max_reward_apy_share_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["reward_apy_share_bps"] = 1200  # max is 5000

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.REWARD_APY_SHARE_TOO_HIGH" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_reward_apy_share_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Reward APY share exactly at max_reward_apy_share_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["reward_apy_share_bps"] = 5000  # exact boundary

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.REWARD_APY_SHARE_TOO_HIGH" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_reward_apy_share_too_high_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Reward APY share exceeding limit triggers RISK.REWARD_APY_SHARE_TOO_HIGH."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["reward_apy_share_bps"] = 6500  # max is 5000

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.REWARD_APY_SHARE_TOO_HIGH" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.REWARD_APY_SHARE_TOO_HIGH")
    assert hit["evidence"]["reward_apy_share_bps"] == 6500
    assert hit["evidence"]["max_reward_apy_share_bps"] == 5000
    assert "Reward APY share 6500 bps exceeds maximum" in decision["reason"]


def test_risk_rule_apy_unstable_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """APY deviation below max_apy_deviation_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["apy_deviation_bps"] = 80  # max is 300

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.APY_UNSTABLE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_apy_unstable_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """APY deviation exactly equal to max_apy_deviation_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["apy_deviation_bps"] = 300  # exact boundary

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.APY_UNSTABLE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_apy_unstable_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """APY deviation above threshold triggers RISK.APY_UNSTABLE."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["apy_deviation_bps"] = 450  # max is 300

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.APY_UNSTABLE" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.APY_UNSTABLE")
    assert hit["evidence"]["apy_deviation_bps"] == 450
    assert hit["evidence"]["max_apy_deviation_bps"] == 300
    assert "APY deviation 450 bps exceeds maximum limit of 300 bps" in decision["reason"]


def test_risk_rule_depeg_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Stablecoin depeg below max_depeg_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["stablecoin_depeg_bps"] = 6  # max is 50

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DEPEG_TOO_WIDE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_depeg_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Stablecoin depeg exactly at max_depeg_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["stablecoin_depeg_bps"] = 50  # exact boundary

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DEPEG_TOO_WIDE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_depeg_null_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Null stablecoin depeg (non-stablecoin order) passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["stablecoin_depeg_bps"] = None

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DEPEG_TOO_WIDE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_depeg_too_wide_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Stablecoin depeg exceeding max_depeg_bps triggers RISK.DEPEG_TOO_WIDE."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["stablecoin_depeg_bps"] = 90  # max is 50

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DEPEG_TOO_WIDE" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.DEPEG_TOO_WIDE")
    assert hit["evidence"]["stablecoin_depeg_bps"] == 90
    assert hit["evidence"]["max_depeg_bps"] == 50
    assert "Stablecoin depeg 90 bps exceeds maximum limit of 50 bps" in decision["reason"]


def test_risk_rule_utilization_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Lending pool utilization below max_utilization_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["utilization_bps"] = 7400  # max is 9000

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.UTILIZATION_TOO_HIGH" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_utilization_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Lending pool utilization exactly at max_utilization_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["utilization_bps"] = 9000  # exact boundary

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.UTILIZATION_TOO_HIGH" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_utilization_null_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Null lending pool utilization passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["utilization_bps"] = None

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.UTILIZATION_TOO_HIGH" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_utilization_too_high_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Lending pool utilization exceeding threshold triggers RISK.UTILIZATION_TOO_HIGH."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["utilization_bps"] = 9400  # max is 9000

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.UTILIZATION_TOO_HIGH" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.UTILIZATION_TOO_HIGH")
    assert hit["evidence"]["utilization_bps"] == 9400
    assert hit["evidence"]["max_utilization_bps"] == 9000
    assert "Lending pool utilization 9400 bps exceeds maximum" in decision["reason"]


def test_risk_rule_slippage_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Estimated and order slippage below max_slippage_bps pass evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["estimated_slippage_bps"] = 30  # max is 50

    order = copy.deepcopy(valid_swap_order)
    order["max_slippage_bps"] = 40  # max is 50

    decision = evaluate(loaded_ips, valid_state, order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.SLIPPAGE_TOO_HIGH" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_slippage_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Estimated slippage exactly at max_slippage_bps passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["estimated_slippage_bps"] = 50  # exact boundary

    order = copy.deepcopy(valid_swap_order)
    order["max_slippage_bps"] = 50

    decision = evaluate(loaded_ips, valid_state, order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.SLIPPAGE_TOO_HIGH" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_slippage_too_high_from_risk_inputs_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Estimated slippage above limit triggers RISK.SLIPPAGE_TOO_HIGH."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["estimated_slippage_bps"] = 75  # max is 50

    decision = evaluate(loaded_ips, valid_state, valid_swap_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.SLIPPAGE_TOO_HIGH" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.SLIPPAGE_TOO_HIGH")
    assert hit["evidence"]["estimated_slippage_bps"] == 75
    assert hit["evidence"]["max_slippage_bps"] == 50
    assert "Slippage exceeds maximum allowed threshold of 50 bps" in decision["reason"]


def test_risk_rule_slippage_too_high_from_order_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_swap_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Order requested slippage above limit triggers RISK.SLIPPAGE_TOO_HIGH."""
    order = copy.deepcopy(valid_swap_order)
    order["max_slippage_bps"] = 80  # max is 50

    decision = evaluate(loaded_ips, valid_state, order, risk_inputs=valid_risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.SLIPPAGE_TOO_HIGH" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.SLIPPAGE_TOO_HIGH")
    assert hit["evidence"]["order_slippage_bps"] == 80
    assert hit["evidence"]["max_slippage_bps"] == 50


def test_risk_rule_cost_exceeds_benefit_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Benefit-to-cost ratio exceeding required threshold passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    # Benefit 3.10 USD / Cost 0.40 USD = 7.75x (77500 bps >= 20000 bps)
    risk_inputs["estimated_cost_usd"] = "0.40"
    risk_inputs["estimated_benefit_usd"] = "3.10"

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.COST_EXCEEDS_BENEFIT" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_cost_exceeds_benefit_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Benefit-to-cost ratio exactly at required threshold passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    # Benefit 2.00 USD / Cost 1.00 USD = 2x (20000 bps == 20000 bps)
    risk_inputs["estimated_cost_usd"] = "1.00"
    risk_inputs["estimated_benefit_usd"] = "2.00"

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.COST_EXCEEDS_BENEFIT" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_cost_exceeds_benefit_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Benefit below required multiple of cost triggers RISK.COST_EXCEEDS_BENEFIT."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    # Benefit 1.00 USD / Cost 2.00 USD = 0.5x (5000 bps < 20000 bps)
    risk_inputs["estimated_cost_usd"] = "2.00"
    risk_inputs["estimated_benefit_usd"] = "1.00"

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.COST_EXCEEDS_BENEFIT" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.COST_EXCEEDS_BENEFIT")
    assert hit["evidence"]["estimated_benefit_usd"] == "1.00"
    assert hit["evidence"]["estimated_cost_usd"] == "2.00"
    assert hit["evidence"]["min_benefit_cost_ratio_bps"] == 20000
    assert hit["evidence"]["actual_ratio_bps"] == 5000
    assert "Benefit/cost ratio 5000 bps is below required multiple" in decision["reason"]


def test_risk_rule_data_stale_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Fresh data within max_data_age_seconds passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["data_age_seconds"] = 90  # max is 7200

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DATA_STALE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_data_stale_exact_boundary_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Data age exactly at max_data_age_seconds passes evaluation."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["data_age_seconds"] = 7200  # exact boundary

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DATA_STALE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_data_stale_age_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    valid_risk_inputs: dict[str, Any],
) -> None:
    """Data age exceeding max_data_age_seconds triggers RISK.DATA_STALE."""
    risk_inputs = copy.deepcopy(valid_risk_inputs)
    risk_inputs["data_age_seconds"] = 8000  # max is 7200

    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=risk_inputs)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DATA_STALE" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.DATA_STALE")
    assert hit["evidence"]["data_age_seconds"] == 8000
    assert hit["evidence"]["max_data_age_seconds"] == 7200
    assert "Risk inputs data age 8000s exceeds maximum allowed age of 7200s" in decision["reason"]


def test_risk_rule_data_stale_missing_risk_inputs_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Order needing risk inputs (supply) with risk_inputs=None triggers RISK.DATA_STALE."""
    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs=None)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DATA_STALE" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.DATA_STALE")
    assert hit["evidence"] == {"risk_inputs": "missing"}
    assert "Risk inputs are required for this order but are missing" in decision["reason"]


def test_risk_rule_approve_does_not_need_risk_inputs(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Approve order does not require risk inputs and passes with risk_inputs=None."""
    order = copy.deepcopy(valid_order)
    order["function"] = "approve"
    order["recipient"] = "aave_pool"
    order["amount"] = {"value": "80000000", "decimals": 6}

    decision = evaluate(loaded_ips, valid_state, order, risk_inputs=None)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DATA_STALE" not in hit_codes
    assert decision["decision"] == "allow"


def test_risk_rule_incomplete_risk_inputs_fail_closed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Risk inputs with a missing field never pass as fresh: denied with RISK.DATA_STALE."""
    decision = evaluate(loaded_ips, valid_state, valid_order, risk_inputs={"data_age_seconds": 0})
    assert decision["decision"] == "deny"
    hit = next(h for h in decision["rules_hit"] if h["code"] == "RISK.DATA_STALE")
    assert hit["evidence"] == {"risk_inputs": "incomplete"}
