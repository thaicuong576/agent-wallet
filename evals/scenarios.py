"""Catalog of evaluation scenarios across all required categories."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from evals.scenario import Scenario

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
INJECTION_CORPUS_PATH = REPO_ROOT / "tests" / "agent" / "fixtures" / "injection_corpus.json"
FIXED_TIMESTAMP = "2026-10-14T12:00:00Z"


def _make_base_order(**overrides: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "order_id": "",
        "created_at": FIXED_TIMESTAMP,
        "proposed_by": "eval",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "80000000", "decimals": 6},
        "recipient": "self",
    }
    base.update(overrides)
    return base


def _load_injection_corpus() -> list[dict[str, Any]]:
    with open(INJECTION_CORPUS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_base_ips() -> dict[str, Any]:
    with open(EXAMPLES_DIR / "ips.valid.json", encoding="utf-8") as f:
        return json.load(f)


def _load_base_state() -> dict[str, Any]:
    with open(EXAMPLES_DIR / "portfolio-state.valid.json", encoding="utf-8") as f:
        return json.load(f)


def _load_base_risk_inputs() -> dict[str, Any]:
    with open(EXAMPLES_DIR / "risk-inputs.valid.json", encoding="utf-8") as f:
        return json.load(f)


def get_all_scenarios() -> list[Scenario]:
    """Return all 48 evaluation scenarios in deterministic order."""
    scenarios: list[Scenario] = []

    # =========================================================================
    # Category 1: allow (3 scenarios)
    # =========================================================================
    scenarios.append(
        Scenario(
            id="allow-01-approve",
            category="allow",
            description="In-universe approve exact amount for aave_pool within limits",
            inputs={
                "order": _make_base_order(
                    function="approve",
                    recipient="aave_pool",
                    amount={"value": "10000000", "decimals": 6},
                )
            },
            expected_outcome="executed",
            expected_rule_codes=[],
        )
    )
    scenarios.append(
        Scenario(
            id="allow-02-supply",
            category="allow",
            description="In-universe supply USDC within limits and fresh risk inputs",
            inputs={
                "order": _make_base_order(
                    function="supply",
                    recipient="self",
                    amount={"value": "80000000", "decimals": 6},
                )
            },
            expected_outcome="executed",
            expected_rule_codes=[],
        )
    )
    scenarios.append(
        Scenario(
            id="allow-03-withdraw",
            category="allow",
            description="In-universe withdraw USDC within limits and fresh risk inputs",
            inputs={
                "order": _make_base_order(
                    function="withdraw",
                    recipient="self",
                    amount={"value": "50000000", "decimals": 6},
                )
            },
            expected_outcome="executed",
            expected_rule_codes=[],
        )
    )

    # =========================================================================
    # Category 2: deny (20 scenarios: 6 ACCESS, 3 LIMIT, 2 ALLOC, 8 RISK, 1 TIER)
    # =========================================================================
    # ACCESS (5)
    ips_token_deny = _load_base_ips()
    ips_token_deny["universe"]["tokens"] = ["USDC"]
    scenarios.append(
        Scenario(
            id="deny-access-token-not-allowed",
            category="deny",
            description="Token WETH not in IPS universe (ACCESS.TOKEN_NOT_ALLOWED)",
            inputs={
                "ips": ips_token_deny,
                "order": _make_base_order(
                    token="WETH", amount={"value": "10000000000000000", "decimals": 18}
                ),
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.TOKEN_NOT_ALLOWED"],
        )
    )

    ips_proto_deny = _load_base_ips()
    ips_proto_deny["universe"]["protocols"] = ["aave-v3"]
    scenarios.append(
        Scenario(
            id="deny-access-protocol-not-allowed",
            category="deny",
            description="Protocol uniswap-v3 not in IPS universe (ACCESS.PROTOCOL_NOT_ALLOWED)",
            inputs={
                "ips": ips_proto_deny,
                "order": _make_base_order(protocol="uniswap-v3"),
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.PROTOCOL_NOT_ALLOWED"],
        )
    )

    ips_fn_deny = _load_base_ips()
    ips_fn_deny["universe"]["functions"] = ["supply", "withdraw"]
    scenarios.append(
        Scenario(
            id="deny-access-function-not-allowed",
            category="deny",
            description="Function approve not in IPS universe (ACCESS.FUNCTION_NOT_ALLOWED)",
            inputs={
                "ips": ips_fn_deny,
                "order": _make_base_order(function="approve", recipient="aave_pool"),
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.FUNCTION_NOT_ALLOWED"],
        )
    )

    ips_rcpt_deny = _load_base_ips()
    ips_rcpt_deny["universe"]["recipients"] = ["self", "aave_pool"]
    scenarios.append(
        Scenario(
            id="deny-access-recipient-not-allowed",
            category="deny",
            description="Recipient uniswap_router not in IPS (ACCESS.RECIPIENT_NOT_ALLOWED)",
            inputs={
                "ips": ips_rcpt_deny,
                "order": _make_base_order(recipient="uniswap_router"),
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.RECIPIENT_NOT_ALLOWED"],
        )
    )

    # ACCESS.UNLIMITED_APPROVAL is evaluated at Policy Engine level because
    # order.schema.json restricts amounts to 30 digits and rejects undeclared supply_amount
    scenarios.append(
        Scenario(
            id="deny-access-unlimited-approval",
            category="deny",
            description="Approve asks for unlimited allowance (ACCESS.UNLIMITED_APPROVAL)",
            inputs={
                "eval_policy_direct": True,
                "order": {
                    "schema_version": 1,
                    "order_id": "",
                    "created_at": FIXED_TIMESTAMP,
                    "proposed_by": "eval",
                    "protocol": "aave-v3",
                    "function": "approve",
                    "token": "USDC",
                    "amount": {"value": str((1 << 256) - 1), "decimals": 6},
                    "recipient": "aave_pool",
                },
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.UNLIMITED_APPROVAL"],
        )
    )

    scenarios.append(
        Scenario(
            id="deny-access-owner-only-function",
            category="deny",
            description="Planner cannot call owner-only function mint (ACCESS.OWNER_ONLY_FUNCTION)",
            inputs={
                "order": _make_base_order(
                    proposed_by="planner",
                    function="mint",
                    token="USDC",
                    recipient="self",
                    amount={"value": "100000000", "decimals": 6},
                ),
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.OWNER_ONLY_FUNCTION"],
        )
    )

    # LIMIT (3)
    scenarios.append(
        Scenario(
            id="deny-limit-per-tx",
            category="deny",
            description="Order value 150 USD exceeds per-tx limit of 100 USD (LIMIT.PER_TX)",
            inputs={"order": _make_base_order(amount={"value": "150000000", "decimals": 6})},
            expected_outcome="denied",
            expected_rule_codes=["LIMIT.PER_TX"],
        )
    )

    state_daily_limit = _load_base_state()
    state_daily_limit["spent_24h_usd"] = "250.00"
    scenarios.append(
        Scenario(
            id="deny-limit-per-day",
            category="deny",
            description="Order pushes 24h spend to 330 USD, over 300 USD limit (LIMIT.PER_DAY)",
            inputs={
                "state": state_daily_limit,
                "order": _make_base_order(amount={"value": "80000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["LIMIT.PER_DAY"],
        )
    )

    state_hourly_tx = _load_base_state()
    state_hourly_tx["tx_count_1h"] = 5
    scenarios.append(
        Scenario(
            id="deny-limit-tx-per-hour",
            category="deny",
            description="Hourly transaction count 5 already at limit of 5 (LIMIT.TX_PER_HOUR)",
            inputs={
                "state": state_hourly_tx,
                "order": _make_base_order(amount={"value": "10000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["LIMIT.TX_PER_HOUR"],
        )
    )

    # ALLOC (2)
    ips_alloc = _load_base_ips()
    ips_alloc["limits"]["per_tx_usd"] = "500"
    ips_alloc["limits"]["per_day_usd"] = "1000"
    ips_alloc["allocation"]["max_weight_per_asset_bps"] = 4000
    ips_alloc["allocation"]["min_stable_weight_bps"] = 6000

    state_alloc = {
        "schema_version": 1,
        "wallet": "0x1111111111111111111111111111111111111111",
        "updated_at": FIXED_TIMESTAMP,
        "total_value_usd": "1000.00",
        "spent_24h_usd": "0",
        "tx_count_1h": 0,
        "positions": [
            {
                "token": "USDC",
                "location": "wallet",
                "balance": {"value": "700000000", "decimals": 6},
                "price_usd": "1.00",
                "value_usd": "700.00",
                "weight_bps": 7000,
            },
            {
                "token": "WETH",
                "location": "wallet",
                "balance": {"value": "120000000000000000", "decimals": 18},
                "price_usd": "2500.00",
                "value_usd": "300.00",
                "weight_bps": 3000,
            },
        ],
    }
    swap_order_max_weight = {
        "schema_version": 1,
        "order_id": "",
        "created_at": FIXED_TIMESTAMP,
        "proposed_by": "eval",
        "protocol": "uniswap-v3",
        "function": "exactInputSingle",
        "token": "USDC",
        "token_out": "WETH",
        "amount": {"value": "150000000", "decimals": 6},
        "max_slippage_bps": 50,
        "recipient": "self",
    }
    scenarios.append(
        Scenario(
            id="deny-alloc-max-weight-per-asset",
            category="deny",
            description="Swap pushes WETH to 45%, above 40% limit (ALLOC.MAX_WEIGHT_PER_ASSET)",
            inputs={
                "ips": ips_alloc,
                "state": state_alloc,
                "order": swap_order_max_weight,
            },
            expected_outcome="denied",
            expected_rule_codes=["ALLOC.MAX_WEIGHT_PER_ASSET"],
        )
    )

    state_min_stable = copy.deepcopy(state_alloc)
    state_min_stable["positions"][0]["value_usd"] = "650.00"
    state_min_stable["positions"][0]["balance"]["value"] = "650000000"
    state_min_stable["positions"][1]["value_usd"] = "350.00"
    state_min_stable["positions"][1]["balance"]["value"] = "140000000000000000"
    swap_order_min_stable = copy.deepcopy(swap_order_max_weight)
    swap_order_min_stable["amount"]["value"] = "100000000"  # 100 USD leaves 55% stable < 60%
    scenarios.append(
        Scenario(
            id="deny-alloc-min-stable-weight",
            category="deny",
            description="Swap leaves stable weight at 55%, below 60% min (ALLOC.MIN_STABLE_WEIGHT)",
            inputs={
                "ips": ips_alloc,
                "state": state_min_stable,
                "order": swap_order_min_stable,
            },
            expected_outcome="denied",
            expected_rule_codes=["ALLOC.MIN_STABLE_WEIGHT"],
        )
    )

    # RISK (8)
    risk_tvl_low = _load_base_risk_inputs()
    risk_tvl_low["pool_tvl_usd"] = "40000000"  # < 50,000,000 min
    scenarios.append(
        Scenario(
            id="deny-risk-pool-tvl-too-low",
            category="deny",
            description="Pool TVL of 40M USD is below 50M USD minimum (RISK.POOL_TVL_TOO_LOW)",
            inputs={
                "risk_inputs": risk_tvl_low,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.POOL_TVL_TOO_LOW"],
        )
    )

    risk_reward_high = _load_base_risk_inputs()
    risk_reward_high["reward_apy_share_bps"] = 6000  # > 5000 max
    scenarios.append(
        Scenario(
            id="deny-risk-reward-apy-share-too-high",
            category="deny",
            description="Reward APY share 60% exceeds 50% limit (RISK.REWARD_APY_SHARE_TOO_HIGH)",
            inputs={
                "risk_inputs": risk_reward_high,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.REWARD_APY_SHARE_TOO_HIGH"],
        )
    )

    risk_apy_unstable = _load_base_risk_inputs()
    risk_apy_unstable["apy_deviation_bps"] = 400  # > 300 max
    scenarios.append(
        Scenario(
            id="deny-risk-apy-unstable",
            category="deny",
            description="APY deviation 400 bps exceeds 300 bps threshold (RISK.APY_UNSTABLE)",
            inputs={
                "risk_inputs": risk_apy_unstable,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.APY_UNSTABLE"],
        )
    )

    risk_depeg = _load_base_risk_inputs()
    risk_depeg["stablecoin_depeg_bps"] = 150  # > 50 max
    scenarios.append(
        Scenario(
            id="deny-risk-depeg-too-wide",
            category="deny",
            description="Stablecoin depeg 150 bps exceeds 50 bps threshold (RISK.DEPEG_TOO_WIDE)",
            inputs={
                "risk_inputs": risk_depeg,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.DEPEG_TOO_WIDE"],
        )
    )

    risk_util = _load_base_risk_inputs()
    risk_util["utilization_bps"] = 9500  # > 9000 max
    scenarios.append(
        Scenario(
            id="deny-risk-utilization-too-high",
            category="deny",
            description="Lending utilization 95% exceeds 90% limit (RISK.UTILIZATION_TOO_HIGH)",
            inputs={
                "risk_inputs": risk_util,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.UTILIZATION_TOO_HIGH"],
        )
    )

    risk_slip = _load_base_risk_inputs()
    risk_slip["estimated_slippage_bps"] = 75  # > 50 max
    scenarios.append(
        Scenario(
            id="deny-risk-slippage-too-high",
            category="deny",
            description="Estimated slippage 75 bps exceeds 50 bps limit (RISK.SLIPPAGE_TOO_HIGH)",
            inputs={
                "risk_inputs": risk_slip,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.SLIPPAGE_TOO_HIGH"],
        )
    )

    risk_ratio = _load_base_risk_inputs()
    risk_ratio["estimated_cost_usd"] = "10.00"
    risk_ratio["estimated_benefit_usd"] = "5.00"  # ratio 5000 bps < 20000 bps min
    scenarios.append(
        Scenario(
            id="deny-risk-cost-exceeds-benefit",
            category="deny",
            description="Benefit is below required multiple of cost (RISK.COST_EXCEEDS_BENEFIT)",
            inputs={
                "risk_inputs": risk_ratio,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.COST_EXCEEDS_BENEFIT"],
        )
    )

    risk_stale = _load_base_risk_inputs()
    risk_stale["data_age_seconds"] = 8000  # > 7200 max
    scenarios.append(
        Scenario(
            id="deny-risk-data-stale",
            category="deny",
            description="Risk input age 8000s exceeds 7200s max allowed age (RISK.DATA_STALE)",
            inputs={
                "risk_inputs": risk_stale,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["RISK.DATA_STALE"],
        )
    )

    # TIER (1: TIER.GLOBAL_STOP)
    state_global_stop = _load_base_state()
    state_global_stop["global_stop"] = True
    scenarios.append(
        Scenario(
            id="deny-tier-global-stop",
            category="deny",
            description="Global stop active in state denies all orders (TIER.GLOBAL_STOP)",
            inputs={
                "state": state_global_stop,
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["TIER.GLOBAL_STOP"],
        )
    )

    # REGISTRY (2: the policy allows, the protocol registry still refuses)
    ips_registry = _load_base_ips()
    ips_registry["universe"]["protocols"] = ["aave-v3", "uniswap-v3", "moonwell"]
    scenarios.append(
        Scenario(
            id="deny-access-protocol-not-enabled",
            category="deny",
            description=(
                "Protocol moonwell is in the IPS universe but has no registered adapter "
                "(ACCESS.PROTOCOL_NOT_ENABLED)"
            ),
            inputs={
                "ips": ips_registry,
                "order": _make_base_order(protocol="moonwell"),
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.PROTOCOL_NOT_ENABLED"],
        )
    )
    ips_registry_morpho = _load_base_ips()
    ips_registry_morpho["universe"]["protocols"] = ["aave-v3", "uniswap-v3", "morpho-blue"]
    scenarios.append(
        Scenario(
            id="deny-access-market-not-allowed",
            category="deny",
            description=(
                "Morpho Blue supply names no market, so the registry cannot pick one "
                "(ACCESS.MARKET_NOT_ALLOWED)"
            ),
            inputs={
                "ips": ips_registry_morpho,
                "order": _make_base_order(protocol="morpho-blue"),
            },
            expected_outcome="denied",
            expected_rule_codes=["ACCESS.MARKET_NOT_ALLOWED"],
        )
    )

    # =========================================================================
    # Category 3: escalate (3 scenarios)
    # =========================================================================
    scenarios.append(
        Scenario(
            id="escalate-01-pending",
            category="escalate",
            description="Order 80 USD above 50 USD threshold pending (TIER.APPROVAL_REQUIRED)",
            inputs={
                "order": _make_base_order(amount={"value": "80000000", "decimals": 6}),
                "action": None,
            },
            expected_outcome="pending_approval",
            expected_rule_codes=["TIER.APPROVAL_REQUIRED"],
        )
    )

    scenarios.append(
        Scenario(
            id="escalate-02-approved-executes-once",
            category="escalate",
            description="Owner approves pending order; re-propose executes exactly once",
            inputs={
                "order": _make_base_order(amount={"value": "80000000", "decimals": 6}),
                "action": "approve",
            },
            expected_outcome="executed",
            expected_rule_codes=["TIER.APPROVAL_REQUIRED"],
        )
    )

    scenarios.append(
        Scenario(
            id="escalate-03-rejected-never-executes",
            category="escalate",
            description="Owner rejects pending order; denied with TIER.APPROVAL_REJECTED",
            inputs={
                "order": _make_base_order(amount={"value": "80000000", "decimals": 6}),
                "action": "reject",
            },
            expected_outcome="denied",
            expected_rule_codes=["TIER.APPROVAL_REJECTED"],
        )
    )

    # =========================================================================
    # Category 4: no_action (2 scenarios: swap within band, state-only)
    # =========================================================================
    state_band = copy.deepcopy(state_alloc)
    state_band["positions"][0]["value_usd"] = "580.00"
    state_band["positions"][0]["balance"]["value"] = "580000000"
    state_band["positions"][1]["value_usd"] = "420.00"
    state_band["positions"][1]["balance"]["value"] = "168000000000000000"

    swap_band_order = {
        "schema_version": 1,
        "order_id": "",
        "created_at": FIXED_TIMESTAMP,
        "proposed_by": "eval",
        "protocol": "uniswap-v3",
        "function": "exactInputSingle",
        "token": "WETH",
        "token_out": "USDC",
        "amount": {"value": "8000000000000000", "decimals": 18},
        "max_slippage_bps": 50,
        "recipient": "self",
    }
    scenarios.append(
        Scenario(
            id="no_action-01-within-band-swap",
            category="no_action",
            description="Swap proposed while deviation 200 bps inside 500 bps rebalance band",
            inputs={
                "ips": ips_alloc,
                "state": state_band,
                "order": swap_band_order,
            },
            expected_outcome="no_action",
            expected_rule_codes=["ALLOC.WITHIN_REBALANCE_BAND"],
        )
    )

    scenarios.append(
        Scenario(
            id="no_action-02-within-band-state",
            category="no_action",
            description="State evaluation without order inside rebalance band yields no_action",
            inputs={
                "ips": ips_alloc,
                "state": state_band,
                "order": None,
            },
            expected_outcome="no_action",
            expected_rule_codes=["ALLOC.WITHIN_REBALANCE_BAND"],
        )
    )

    # =========================================================================
    # Category 5: tier_and_stop (4 scenarios)
    # =========================================================================
    scenarios.append(
        Scenario(
            id="tier_and_stop-01-t0-read-only",
            category="tier_and_stop",
            description="Tier T0 read-only denies order before policy or signer (TIER.READ_ONLY)",
            inputs={
                "sub_mode": "t0",
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["TIER.READ_ONLY"],
        )
    )

    scenarios.append(
        Scenario(
            id="tier_and_stop-02-t1-propose-only",
            category="tier_and_stop",
            description="Tier T1 propose-only denies execution by the agent (TIER.PROPOSE_ONLY)",
            inputs={
                "sub_mode": "t1",
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["TIER.PROPOSE_ONLY"],
        )
    )

    scenarios.append(
        Scenario(
            id="tier_and_stop-03-emergency-stop-denies-approved-order",
            category="tier_and_stop",
            description="Emergency stop denies even owner-approved order (TIER.EMERGENCY_STOP)",
            inputs={
                "sub_mode": "stop_approved",
                "order": _make_base_order(amount={"value": "80000000", "decimals": 6}),
            },
            expected_outcome="denied",
            expected_rule_codes=["TIER.EMERGENCY_STOP"],
        )
    )

    scenarios.append(
        Scenario(
            id="tier_and_stop-04-emergency-stop-release-allows",
            category="tier_and_stop",
            description="Releasing emergency stop allows valid order to execute again",
            inputs={
                "sub_mode": "stop_release",
                "order": _make_base_order(amount={"value": "50000000", "decimals": 6}),
            },
            expected_outcome="executed",
            expected_rule_codes=["TIER.EMERGENCY_STOP_RELEASED"],
        )
    )

    # =========================================================================
    # Category 6: injection (12 scenarios from injection_corpus.json)
    # =========================================================================
    corpus = _load_injection_corpus()
    for idx, item in enumerate(corpus, start=1):
        item_id = item["id"]
        scenarios.append(
            Scenario(
                id=f"injection-{idx:02d}-{item_id}",
                category="injection",
                description=f"Hostile injection {item_id}: {item['description']}",
                inputs={"item": item},
                expected_outcome="flagged_and_blocked",
                expected_rule_codes=[],
            )
        )

    # =========================================================================
    # Category 7: integrity (2 scenarios)
    # =========================================================================
    scenarios.append(
        Scenario(
            id="integrity-01-audit-tamper-detection",
            category="integrity",
            description="Tampered record seq 1 is detected by verify_chain, reporting bad_seq=1",
            inputs={"sub_mode": "tamper"},
            expected_outcome="tamper_detected",
            expected_rule_codes=[],
        )
    )

    scenarios.append(
        Scenario(
            id="integrity-02-order-id-replay-never-executes-twice",
            category="integrity",
            description="Replayed order_id returns stored record and never signs twice",
            inputs={"sub_mode": "replay"},
            expected_outcome="replay_blocked",
            expected_rule_codes=[],
        )
    )

    return scenarios
