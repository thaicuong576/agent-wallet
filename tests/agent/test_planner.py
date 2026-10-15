"""Tests for Planner: order proposals, strict schema validation, and universe flagging."""

from __future__ import annotations

import json
import uuid

from core.agent.client import ScriptedClient
from core.agent.planner import PLANNER_SYSTEM_PROMPT, Planner
from core.agent.reader import ReaderFacts
from core.policy.engine import evaluate as evaluate_policy
from core.policy.ips import parse_ips

SAMPLE_IPS_DATA = {
    "schema_version": 1,
    "owner": "0x1111111111111111111111111111111111111111",
    "objective": "Balanced yield on testnet",
    "universe": {
        "tokens": ["USDC"],
        "protocols": ["aave-v3"],
        "functions": ["approve", "supply", "withdraw"],
        "recipients": ["self", "aave_pool"],
    },
    "allocation": {
        "max_weight_per_asset_bps": 4000,
        "min_stable_weight_bps": 6000,
        "rebalance_band_bps": 500,
    },
    "risk": {
        "min_pool_tvl_usd": "50000000",
        "max_depeg_bps": 100,
        "max_slippage_bps": 50,
        "max_reward_apy_share_bps": 5000,
        "max_apy_deviation_bps": 200,
        "max_utilization_bps": 8500,
        "min_benefit_cost_ratio_bps": 20000,
        "max_data_age_seconds": 3600,
    },
    "limits": {
        "per_tx_usd": "1000.00",
        "per_day_usd": "5000.00",
        "max_tx_per_hour": 5,
    },
    "approval": {
        "escalate_above_usd": "2500.00",
    },
}

SAMPLE_PORTFOLIO_STATE = {
    "schema_version": 1,
    "as_of": "2026-10-14T12:00:00Z",
    "price_source": "mainnet_reference",
    "positions": [
        {
            "token": "USDC",
            "location": "wallet",
            "balance": {"value": "2000000000", "decimals": 6},
            "price_usd": "1.00",
            "value_usd": "2000.00",
            "weight_bps": 10000,
        }
    ],
    "total_value_usd": "2000.00",
    "spent_24h_usd": "0.00",
    "tx_count_1h": 0,
}


def test_planner_proposes_valid_aave_supply_order():
    """Planner proposes a valid Aave V3 supply order validating against order.schema.json."""
    order_id = str(uuid.uuid4())
    canned_response = json.dumps(
        {
            "action": "propose",
            "order": {
                "schema_version": 1,
                "order_id": order_id,
                "created_at": "2026-10-14T12:30:00Z",
                "proposed_by": "planner",
                "protocol": "aave-v3",
                "function": "supply",
                "token": "USDC",
                "amount": {"value": "500000000", "decimals": 6},
                "recipient": "aave_pool",
            },
        }
    )

    client = ScriptedClient(canned_response)
    planner = Planner(client=client)

    facts = ReaderFacts(
        source_type="pool_description",
        summary="Aave V3 USDC pool offers 4.5% APY",
        tokens_mentioned=("USDC",),
        protocols_mentioned=("aave-v3",),
        metrics={"apy_bps": 450, "tvl_usd": "100000000"},
        sentiment="bullish",
        instruction_detected=False,
        flags=(),
    )

    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=SAMPLE_IPS_DATA,
        facts=facts,
    )

    assert not result.is_no_action
    assert len(result.orders) == 1
    order = result.orders[0]
    assert order["order_id"] == order_id
    assert order["protocol"] == "aave-v3"
    assert order["function"] == "supply"
    assert order["token"] == "USDC"
    assert order["amount"]["value"] == "500000000"
    assert len(result.validation_errors) == 0
    assert len(result.warnings) == 0


def test_planner_proposes_no_action_with_reason():
    """Planner proposes explicit no_action when portfolio is balanced."""
    canned = json.dumps(
        {
            "action": "no_action",
            "reason": "Portfolio is fully balanced within IPS allocation targets.",
        }
    )
    planner = Planner(client=ScriptedClient(canned))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=SAMPLE_IPS_DATA,
    )

    assert result.is_no_action
    assert len(result.orders) == 0
    assert result.no_action is not None
    assert "fully balanced" in result.no_action["reason"]


def test_planner_rejects_missing_fields_without_repair_guessing():
    """Planner rejects order missing required schema_version or order_id without guessing."""
    # Invalid order: missing schema_version, missing proposed_by
    canned = json.dumps(
        {
            "action": "propose",
            "order": {
                "order_id": str(uuid.uuid4()),
                "created_at": "2026-10-14T12:30:00Z",
                "protocol": "aave-v3",
                "function": "supply",
                "token": "USDC",
                "amount": {"value": "1000000", "decimals": 6},
                "recipient": "aave_pool",
            },
        }
    )
    planner = Planner(client=ScriptedClient(canned))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=SAMPLE_IPS_DATA,
    )

    assert result.is_no_action
    assert len(result.orders) == 0
    assert result.no_action is not None
    assert "Output rejected: failed order schema validation" in result.no_action["reason"]
    assert len(result.validation_errors) > 0


def test_planner_rejects_float_amount_without_repair_guessing():
    """Planner rejects orders with float values in amount without guessing."""
    canned = json.dumps(
        {
            "action": "propose",
            "order": {
                "schema_version": 1,
                "order_id": str(uuid.uuid4()),
                "created_at": "2026-10-14T12:30:00Z",
                "proposed_by": "planner",
                "protocol": "aave-v3",
                "function": "supply",
                "token": "USDC",
                "amount": {
                    "value": "100.50",
                    "decimals": 6,
                },  # Float string violates regex ^[1-9][0-9]{0,29}$
                "recipient": "aave_pool",
            },
        }
    )
    planner = Planner(client=ScriptedClient(canned))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=SAMPLE_IPS_DATA,
    )

    assert result.is_no_action
    assert len(result.orders) == 0
    assert "Output rejected: failed order schema validation" in result.no_action["reason"]


def test_planner_rejects_raw_eth_address_as_recipient():
    """Planner rejects orders where recipient is an address instead of the required enum."""
    canned = json.dumps(
        {
            "action": "propose",
            "order": {
                "schema_version": 1,
                "order_id": str(uuid.uuid4()),
                "created_at": "2026-10-14T12:30:00Z",
                "proposed_by": "planner",
                "protocol": "aave-v3",
                "function": "supply",
                "token": "USDC",
                "amount": {"value": "1000000", "decimals": 6},
                "recipient": "0xAttackerAddress000000000000000000000000",  # Violates enum
            },
        }
    )
    planner = Planner(client=ScriptedClient(canned))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=SAMPLE_IPS_DATA,
    )

    assert result.is_no_action
    assert len(result.orders) == 0
    assert "failed order schema validation" in result.no_action["reason"]


def test_planner_flags_token_outside_ips_universe_policy_decides():
    """Token outside IPS universe stays an order, is flagged, and is denied by policy."""
    # IPS universe allows only USDC, but order is for WETH
    loaded_ips = parse_ips(SAMPLE_IPS_DATA)
    canned = json.dumps(
        {
            "action": "propose",
            "order": {
                "schema_version": 1,
                "order_id": str(uuid.uuid4()),
                "created_at": "2026-10-14T12:30:00Z",
                "proposed_by": "planner",
                "protocol": "aave-v3",
                "function": "supply",
                "token": "WETH",  # Valid in order.schema.json, but NOT in this IPS universe
                "amount": {"value": "1000000000000000000", "decimals": 18},
                "recipient": "aave_pool",
            },
        }
    )
    planner = Planner(client=ScriptedClient(canned))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=loaded_ips,
    )

    # 1. Planner still outputs the order
    assert len(result.orders) == 1
    # 2. Planner flags that the token is outside the IPS universe
    assert len(result.warnings) > 0
    assert any("outside IPS universe tokens" in w for w in result.warnings)

    # 3. Code decides: Policy engine evaluates and denies the order!
    decision = evaluate_policy(
        ips=loaded_ips,
        state=SAMPLE_PORTFOLIO_STATE,
        order=result.orders[0],
    )
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.TOKEN_NOT_ALLOWED" in hit_codes


def test_planner_flags_recipient_outside_ips_universe_policy_decides():
    """Order with recipient outside IPS universe is returned, flagged, and denied by policy."""
    # IPS universe recipients: ["self", "aave_pool"]
    loaded_ips = parse_ips(SAMPLE_IPS_DATA)
    canned = json.dumps(
        {
            "action": "propose",
            "order": {
                "schema_version": 1,
                "order_id": str(uuid.uuid4()),
                "created_at": "2026-10-14T12:30:00Z",
                "proposed_by": "planner",
                "protocol": "aave-v3",
                "function": "supply",
                "token": "USDC",
                "amount": {"value": "500000000", "decimals": 6},
                # Valid in order.schema.json, but not in this IPS universe
                "recipient": "uniswap_router",
            },
        }
    )
    planner = Planner(client=ScriptedClient(canned))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=loaded_ips,
    )

    assert len(result.orders) == 1
    assert any("outside IPS universe recipients" in w for w in result.warnings)

    decision = evaluate_policy(
        ips=loaded_ips,
        state=SAMPLE_PORTFOLIO_STATE,
        order=result.orders[0],
    )
    assert decision["decision"] == "deny"
    hit_codes = [h["code"] for h in decision["rules_hit"]]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in hit_codes


def test_planner_validate_order_rejects_mint():
    """Planner rejects orders proposing function 'mint'."""
    planner = Planner(client=None)
    bad_order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-14T12:30:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "mint",
        "token": "USDC",
        "amount": {"value": "1000000", "decimals": 6},
        "recipient": "self",
    }
    errors = planner.validate_order(bad_order)
    assert len(errors) > 0
    assert any("not permitted for planner" in e for e in errors)


def test_planner_validate_order_rejects_transfer():
    """Planner rejects orders proposing function 'transfer'."""
    planner = Planner(client=None)
    bad_order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-14T12:30:00Z",
        "proposed_by": "planner",
        "protocol": "wallet",
        "function": "transfer",
        "token": "USDC",
        "amount": {"value": "1000000", "decimals": 6},
        "recipient": "owner",
    }
    errors = planner.validate_order(bad_order)
    assert len(errors) > 0
    assert any("not permitted for planner" in e for e in errors)


def test_planner_validate_order_rejects_recipient_owner():
    """Planner rejects orders proposing recipient 'owner'."""
    planner = Planner(client=None)
    bad_order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-14T12:30:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "1000000", "decimals": 6},
        "recipient": "owner",
    }
    errors = planner.validate_order(bad_order)
    assert len(errors) > 0
    assert any("Recipient 'owner' is not permitted" in e for e in errors)


def test_planner_system_prompt_does_not_mention_mint_or_transfer():
    """PLANNER_SYSTEM_PROMPT does not advertise owner functions mint or transfer to the LLM."""
    assert "mint" not in PLANNER_SYSTEM_PROMPT
    assert "transfer" not in PLANNER_SYSTEM_PROMPT


def test_planner_rejects_owner_function_order_end_to_end():
    """End-to-end: Planner rejects an LLM proposal attempting to call mint."""
    canned = json.dumps(
        {
            "action": "propose",
            "order": {
                "schema_version": 1,
                "order_id": str(uuid.uuid4()),
                "created_at": "2026-10-14T12:30:00Z",
                "proposed_by": "planner",
                "protocol": "aave-v3",
                "function": "mint",
                "token": "USDC",
                "amount": {"value": "1000000", "decimals": 6},
                "recipient": "self",
            },
        }
    )
    planner = Planner(client=ScriptedClient(canned))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=SAMPLE_IPS_DATA,
    )
    assert result.is_no_action
    assert len(result.orders) == 0
    assert len(result.validation_errors) > 0
    assert any("not permitted for planner" in e for e in result.validation_errors)


def test_planner_replaces_a_malformed_order_id_but_keeps_financial_fields_strict():
    """Models invent ids that are not hex; code assigns the id, amounts stay strict."""
    base = {
        "schema_version": 1,
        "order_id": "e1b2c3d4-e5f6-7a8b-9c0d-e1f2g3h4i5j6",
        "created_at": "2026-10-14T12:30:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "aave_pool",
    }
    facts = ReaderFacts(
        source_type="pool_description",
        summary="x",
        tokens_mentioned=("USDC",),
        protocols_mentioned=("aave-v3",),
        metrics={},
        sentiment="neutral",
        instruction_detected=False,
        flags=(),
    )

    def run(order):
        client = ScriptedClient(json.dumps({"action": "propose", "order": order}))
        return Planner(client=client).plan(
            portfolio_state=SAMPLE_PORTFOLIO_STATE, ips=SAMPLE_IPS_DATA, facts=facts
        )

    ok = run(dict(base))
    assert len(ok.orders) == 1
    assert ok.orders[0]["order_id"] != base["order_id"]
    assert str(uuid.UUID(ok.orders[0]["order_id"])) == ok.orders[0]["order_id"]

    bad = run({**base, "amount": {"value": "-5", "decimals": 6}})
    assert bad.is_no_action
