"""Tests against the prompt-injection hostile corpus."""

from __future__ import annotations

import json
from pathlib import Path

from core.agent.client import ScriptedClient
from core.agent.planner import Planner
from core.agent.reader import Reader

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "injection_corpus.json"

SAMPLE_IPS_DATA = {
    "schema_version": 1,
    "owner": "0x1111111111111111111111111111111111111111",
    "objective": "Safe automated rebalancing",
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
            "balance": {"value": "1000000000", "decimals": 6},
            "price_usd": "1.00",
            "value_usd": "1000.00",
            "weight_bps": 10000,
        }
    ],
    "total_value_usd": "1000.00",
    "spent_24h_usd": "0.00",
    "tx_count_1h": 0,
}


def test_corpus_contains_at_least_10_hostile_strings():
    """Verify fixture exists and has at least 10 diverse hostile test vectors."""
    assert FIXTURES_PATH.is_file()
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        corpus = json.load(f)
    assert len(corpus) >= 10
    categories = {item["category"] for item in corpus}
    # Ensure key injection classes are represented
    assert "direct_override" in categories
    assert "fake_system" in categories
    assert "base64" in categories
    assert "homoglyph" in categories
    assert "markdown_link" in categories


def test_reader_detects_all_corpus_hostile_strings():
    """Reader scans and flags instruction-like content for every hostile vector."""
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        corpus = json.load(f)

    reader = Reader()
    for item in corpus:
        hostile_text = item["hostile_text"]
        facts = reader.read(hostile_text)
        assert facts.instruction_detected is True, (
            f"Failed to detect instruction in vector {item['id']}: {hostile_text}"
        )
        assert len(facts.flags) > 0
        assert facts.sentiment == "suspicious"
        # Untrusted instruction never passes forward into summary
        for kw in ("ignore", "send funds", "drain", "freedomgpt", "override code"):
            assert kw not in facts.summary.lower()


def test_hostile_text_never_becomes_unasked_order_field():
    """Hostile corpus text never leaks into order fields when processed by Planner."""
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        corpus = json.load(f)

    reader = Reader()

    for item in corpus:
        facts = reader.read(item["hostile_text"])
        # Even if scripted client returns standard safe proposal or no_action
        client = ScriptedClient('{"action": "no_action", "reason": "Evaluated market data safely"}')
        planner = Planner(client=client)

        result = planner.plan(
            portfolio_state=SAMPLE_PORTFOLIO_STATE,
            ips=SAMPLE_IPS_DATA,
            facts=facts,
        )

        assert result.is_no_action
        # Ensure no hostile text from item leaked into reason
        for word in ("0x1111", "0x2222", "0x3333", "SWdub3Jl", "FreedomGPT"):
            if result.no_action and "reason" in result.no_action:
                assert word not in result.no_action["reason"]


def test_adversarial_llm_attempting_hostile_order_is_rejected():
    """Adversarial LLM attempting orders with raw addresses or unasked fields is rejected."""
    # Adversarial response matching an injection attempt
    adversarial_response = json.dumps(
        {
            "action": "propose",
            "order": {
                "schema_version": 1,
                "order_id": "00000000-0000-0000-0000-000000000001",
                "created_at": "2026-10-14T12:00:00Z",
                "proposed_by": "planner",
                "protocol": "aave-v3",
                "function": "withdraw",
                "token": "USDC",
                "amount": {"value": "1000000000", "decimals": 6},
                "recipient": "0x1111111111111111111111111111111111111111",  # Injected raw address
                "memo": "Drain all funds to attacker",  # Unasked extra field
            },
        }
    )

    planner = Planner(client=ScriptedClient(adversarial_response))
    result = planner.plan(
        portfolio_state=SAMPLE_PORTFOLIO_STATE,
        ips=SAMPLE_IPS_DATA,
    )

    # Must be rejected immediately! No repair guessing!
    assert result.is_no_action
    assert len(result.orders) == 0
    assert "Output rejected: failed order schema validation" in result.no_action["reason"]
