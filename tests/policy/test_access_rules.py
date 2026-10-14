"""Tests for ACCESS policy rule evaluations (allowed and blocked)."""

from __future__ import annotations

import copy
from typing import Any

from core.policy.engine import UINT256_MAX, evaluate
from core.policy.ips import LoadedIps


def test_access_rule_token_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Allowed token passes access evaluation."""
    order = copy.deepcopy(valid_order)
    order["token"] = "USDC"
    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "allow"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" not in hit_codes


def test_access_rule_token_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Disallowed token triggers ACCESS.TOKEN_NOT_ALLOWED."""
    order = copy.deepcopy(valid_order)
    order["token"] = "DAI"
    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.TOKEN_NOT_ALLOWED")
    assert hit["evidence"] == {"token": "DAI"}
    assert "Token 'DAI' is not in the IPS universe" in decision["reason"]


def test_access_rule_swap_token_out_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Swap order with disallowed output token triggers ACCESS.TOKEN_NOT_ALLOWED."""
    order = copy.deepcopy(valid_order)
    order["function"] = "exactInputSingle"
    order["protocol"] = "uniswap-v3"
    order["token"] = "USDC"
    order["token_out"] = "SHIB"
    order["recipient"] = "self"

    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.TOKEN_NOT_ALLOWED")
    assert hit["evidence"] == {"token_out": "SHIB"}
    assert "Output token 'SHIB' is not in the IPS universe" in decision["reason"]


def test_access_rule_protocol_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Allowed protocol passes access evaluation."""
    order = copy.deepcopy(valid_order)
    order["protocol"] = "aave-v3"
    decision = evaluate(loaded_ips, valid_state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.PROTOCOL_NOT_ALLOWED" not in hit_codes


def test_access_rule_protocol_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Disallowed protocol triggers ACCESS.PROTOCOL_NOT_ALLOWED."""
    order = copy.deepcopy(valid_order)
    order["protocol"] = "compound-v3"
    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.PROTOCOL_NOT_ALLOWED" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.PROTOCOL_NOT_ALLOWED")
    assert hit["evidence"] == {"protocol": "compound-v3"}
    assert "Protocol 'compound-v3' is not in the IPS universe" in decision["reason"]


def test_access_rule_function_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Allowed function passes access evaluation."""
    order = copy.deepcopy(valid_order)
    order["function"] = "supply"
    decision = evaluate(loaded_ips, valid_state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.FUNCTION_NOT_ALLOWED" not in hit_codes


def test_access_rule_function_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Disallowed function triggers ACCESS.FUNCTION_NOT_ALLOWED."""
    order = copy.deepcopy(valid_order)
    order["function"] = "flashLoan"
    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.FUNCTION_NOT_ALLOWED" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.FUNCTION_NOT_ALLOWED")
    assert hit["evidence"] == {"function": "flashLoan"}
    assert "Function 'flashLoan' is not in the IPS universe" in decision["reason"]


def test_access_rule_recipient_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Allowed recipient passes access evaluation."""
    order = copy.deepcopy(valid_order)
    order["recipient"] = "self"
    decision = evaluate(loaded_ips, valid_state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" not in hit_codes


def test_access_rule_recipient_blocked(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Disallowed recipient triggers ACCESS.RECIPIENT_NOT_ALLOWED."""
    order = copy.deepcopy(valid_order)
    order["recipient"] = "external_wallet"
    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.RECIPIENT_NOT_ALLOWED")
    assert hit["evidence"] == {"recipient": "external_wallet"}
    assert "Recipient 'external_wallet' is not in the IPS universe" in decision["reason"]


def test_access_rule_unlimited_approval_allowed(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Approve order for exact required amount passes evaluation."""
    order = copy.deepcopy(valid_order)
    order["function"] = "approve"
    order["recipient"] = "aave_pool"
    order["amount"] = {"value": "80000000", "decimals": 6}

    decision = evaluate(loaded_ips, valid_state, order)
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.UNLIMITED_APPROVAL" not in hit_codes


def test_access_rule_unlimited_approval_blocked_by_max_uint256(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Approve order with uint256 max amount triggers ACCESS.UNLIMITED_APPROVAL."""
    order = copy.deepcopy(valid_order)
    order["function"] = "approve"
    order["recipient"] = "aave_pool"
    order["amount"] = {"value": str(UINT256_MAX), "decimals": 6}

    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.UNLIMITED_APPROVAL" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.UNLIMITED_APPROVAL")
    assert hit["evidence"]["amount"] == str(UINT256_MAX)
    assert hit["evidence"]["max_uint256"] is True
    assert "unlimited (maximum uint256)" in decision["reason"]


def test_access_rule_unlimited_approval_blocked_exceeding_supply_amount(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Approve order exceeding the order's own supply amount triggers ACCESS.UNLIMITED_APPROVAL."""
    order = copy.deepcopy(valid_order)
    order["function"] = "approve"
    order["recipient"] = "aave_pool"
    order["amount"] = {"value": "90000000", "decimals": 6}
    order["supply_amount"] = {"value": "80000000", "decimals": 6}

    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.UNLIMITED_APPROVAL" in hit_codes

    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.UNLIMITED_APPROVAL")
    assert hit["evidence"]["amount"] == "90000000"
    assert hit["evidence"]["supply_amount"] == "80000000"
    assert "exceeds order's own supply amount" in decision["reason"]
