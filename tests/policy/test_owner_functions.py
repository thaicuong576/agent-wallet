"""Tests for owner-only policy functions: mint and transfer (Design 7.3)."""

from __future__ import annotations

import copy
from typing import Any

from core.policy.engine import evaluate
from core.policy.ips import LoadedIps


def _make_mint_order(
    value: str = "1000000000000",  # 1,000,000 USDC
    token: str = "USDC",
    recipient: str = "self",
    protocol: str = "aave-v3",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": "0b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d60",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "owner",
        "protocol": protocol,
        "function": "mint",
        "token": token,
        "amount": {"value": value, "decimals": 6},
        "recipient": recipient,
    }


def _make_transfer_order(
    token: str = "USDC",
    value: str = "500000000",  # 500 USDC
    decimals: int = 6,
    recipient: str = "owner",
    protocol: str = "wallet",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": "1b6f1d52-8a1c-4e3a-b7d2-5e9c3a1f4d61",
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "owner",
        "protocol": protocol,
        "function": "transfer",
        "token": token,
        "amount": {"value": value, "decimals": decimals},
        "recipient": recipient,
    }


# =============================================================================
# Row 1: mint or transfer with origin != "owner" -> ACCESS.OWNER_ONLY_FUNCTION
# =============================================================================


def test_mint_non_owner_origin_denied(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint with non-owner origin (default planner) is denied ACCESS.OWNER_ONLY_FUNCTION."""
    order = _make_mint_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="planner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.OWNER_ONLY_FUNCTION" in hit_codes
    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.OWNER_ONLY_FUNCTION")
    assert hit["evidence"]["function"] == "mint"
    assert hit["evidence"]["origin"] == "planner"


def test_transfer_non_owner_origin_denied(
    loaded_ips: LoadedIps, valid_state: dict[str, Any]
) -> None:
    """Transfer with non-owner origin (default planner) is denied ACCESS.OWNER_ONLY_FUNCTION."""
    order = _make_transfer_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="planner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.OWNER_ONLY_FUNCTION" in hit_codes
    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.OWNER_ONLY_FUNCTION")
    assert hit["evidence"]["function"] == "transfer"
    assert hit["evidence"]["origin"] == "planner"


def test_default_origin_is_planner(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Default origin argument is 'planner', preserving backwards compatibility."""
    order = _make_mint_order()
    decision = evaluate(loaded_ips, valid_state, order)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.OWNER_ONLY_FUNCTION" in hit_codes


# =============================================================================
# Row 2: mint token not Aave test USDC / transfer token not USDC or ETH
# =============================================================================


def test_mint_token_and_target_pinned(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint token is pinned to USDC; other tokens trigger ACCESS.TOKEN_NOT_ALLOWED."""
    order = _make_mint_order(token="DAI")
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes
    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.TOKEN_NOT_ALLOWED")
    assert hit["evidence"]["token"] == "DAI"


def test_mint_token_not_allowed(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint with WETH triggers ACCESS.TOKEN_NOT_ALLOWED."""
    order = _make_mint_order(token="WETH")
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes


def test_transfer_token_not_allowed(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Transfer with unpermitted token triggers ACCESS.TOKEN_NOT_ALLOWED."""
    order = _make_transfer_order(token="DAI")
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes


# =============================================================================
# Row 3: transfer recipient not owner / mint recipient not self
# =============================================================================


def test_transfer_to_self_denied(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Transfer with recipient 'self' triggers ACCESS.RECIPIENT_NOT_ALLOWED."""
    order = _make_transfer_order(recipient="self")
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in hit_codes
    hit = next(h for h in decision["rules_hit"] if h["code"] == "ACCESS.RECIPIENT_NOT_ALLOWED")
    assert hit["evidence"]["recipient"] == "self"


def test_transfer_recipient_not_allowed(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Transfer with recipient 'aave_pool' triggers ACCESS.RECIPIENT_NOT_ALLOWED."""
    order = _make_transfer_order(recipient="aave_pool")
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in hit_codes


def test_mint_recipient_not_self_denied(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint with recipient 'owner' triggers ACCESS.RECIPIENT_NOT_ALLOWED."""
    order = _make_mint_order(recipient="owner")
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in hit_codes


# =============================================================================
# Row 4: Tier and stop rules (T0, T1, emergency stop, global stop)
# =============================================================================


def test_transfer_allowed_under_owner_stop(
    loaded_ips: LoadedIps, valid_state: dict[str, Any]
) -> None:
    """Transfer to owner is allowed under emergency stop (funds exit exception)."""
    order = _make_transfer_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", emergency_stop=True)
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []


def test_transfer_allowed_under_t0(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Transfer to owner is allowed under tier T0 (funds exit exception)."""
    order = _make_transfer_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", tier="T0")
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []


def test_transfer_allowed_under_t1(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Transfer to owner is allowed under tier T1 (funds exit exception)."""
    order = _make_transfer_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", tier="T1")
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []


def test_transfer_denied_under_global_stop(
    loaded_ips: LoadedIps, valid_state: dict[str, Any]
) -> None:
    """Transfer to owner is denied under global stop (operator incident)."""
    order = _make_transfer_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", global_stop=True)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "TIER.GLOBAL_STOP" in hit_codes


def test_mint_denied_under_owner_stop(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint is denied under emergency stop (TIER.EMERGENCY_STOP)."""
    order = _make_mint_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", emergency_stop=True)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "TIER.EMERGENCY_STOP" in hit_codes


def test_mint_denied_under_t0(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint is denied under tier T0 (TIER.READ_ONLY)."""
    order = _make_mint_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", tier="T0")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "TIER.READ_ONLY" in hit_codes


def test_mint_denied_under_t1(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint is denied under tier T1 (TIER.PROPOSE_ONLY)."""
    order = _make_mint_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", tier="T1")
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "TIER.PROPOSE_ONLY" in hit_codes


def test_mint_denied_under_global_stop(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint is denied under global stop (TIER.GLOBAL_STOP)."""
    order = _make_mint_order()
    decision = evaluate(loaded_ips, valid_state, order, origin="owner", global_stop=True)
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "TIER.GLOBAL_STOP" in hit_codes


# =============================================================================
# Row 5 & 6: Limits and amount cap skips
# =============================================================================


def test_mint_has_no_amount_cap(loaded_ips: LoadedIps, valid_state: dict[str, Any]) -> None:
    """Mint has no contract amount cap: 50,000,000 USDC passes policy evaluation."""
    order = _make_mint_order(value="50000000000000")  # 50M USDC
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []


def test_owner_functions_skip_spend_limits_and_escalate(
    loaded_ips: LoadedIps, valid_state: dict[str, Any]
) -> None:
    """Mint and transfer skip per_tx_usd, per_day_usd, and escalation limits."""
    # State has already spent 290 USD of 300 USD limit
    state = copy.deepcopy(valid_state)
    state["spent_24h_usd"] = "290.00"

    # Mint of 10,000,000 USDC skips daily spend limit
    mint_order = _make_mint_order(value="10000000000000")
    mint_decision = evaluate(loaded_ips, state, mint_order, origin="owner")
    assert mint_decision["decision"] == "allow"

    # Transfer of 5,000 USDC skips per_tx limit (1000 USD)
    transfer_order = _make_transfer_order(value="5000000000")
    transfer_decision = evaluate(loaded_ips, state, transfer_order, origin="owner")
    assert transfer_decision["decision"] == "allow"


# =============================================================================
# Happy path: valid owner orders allowed
# =============================================================================


def test_mint_valid_allowed(
    loaded_ips: LoadedIps, valid_state: dict[str, Any], decision_validator: Any
) -> None:
    """Valid mint order with origin='owner' is allowed and conforms to schema."""
    order = _make_mint_order(value="1000000000")  # 1,000 USDC
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []
    decision_validator.validate(decision)


def test_transfer_usdc_valid_allowed(
    loaded_ips: LoadedIps, valid_state: dict[str, Any], decision_validator: Any
) -> None:
    """Valid transfer USDC order with origin='owner' is allowed and conforms to schema."""
    order = _make_transfer_order(token="USDC", value="500000000")
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []
    decision_validator.validate(decision)


def test_transfer_eth_valid_allowed(
    loaded_ips: LoadedIps, valid_state: dict[str, Any], decision_validator: Any
) -> None:
    """Valid transfer ETH order with origin='owner' is allowed and conforms to schema."""
    order = _make_transfer_order(token="ETH", value="1000000000000000000", decimals=18)
    decision = evaluate(loaded_ips, valid_state, order, origin="owner")
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []
    decision_validator.validate(decision)


def test_origin_inside_the_order_body_is_ignored(
    loaded_ips: LoadedIps, valid_state: dict[str, Any]
) -> None:
    """Origin is a code-set argument only; an order-body field never grants owner origin."""
    order = {**_make_mint_order(), "origin": "owner"}
    decision = evaluate(loaded_ips, valid_state, order)
    codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.OWNER_ONLY_FUNCTION" in codes
    assert decision["decision"] == "deny"
