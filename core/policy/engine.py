"""Policy engine: pure evaluation of orders against IPS and portfolio state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from core.policy.ips import LoadedIps, compute_ips_hash

UINT256_MAX: int = (1 << 256) - 1
OWNER_FUNCTIONS: Final[frozenset[str]] = frozenset({"mint", "transfer"})

_REQUIRED_RISK_KEYS = (
    "pool_tvl_usd",
    "reward_apy_share_bps",
    "apy_deviation_bps",
    "stablecoin_depeg_bps",
    "utilization_bps",
    "estimated_slippage_bps",
    "estimated_cost_usd",
    "data_age_seconds",
)


def _calculate_order_usd(
    order: Mapping[str, Any],
    state: Mapping[str, Any],
) -> tuple[Decimal | None, str | None]:
    """Calculate order USD value from token amount and portfolio position price.

    Returns (order_usd, None) on success, or (None, error_message) if price cannot be found.
    """
    token = order.get("token")
    if not token:
        return None, "Order is missing 'token' field"

    positions = state.get("positions", [])
    matching_pos = None
    for pos in positions:
        if pos.get("token") == token and "price_usd" in pos:
            matching_pos = pos
            break

    if matching_pos is None:
        return None, f"Price not found in portfolio state for token '{token}'"

    amount_obj = order.get("amount")
    if not isinstance(amount_obj, (dict, Mapping)):
        return None, "Order is missing or has invalid 'amount' object"

    value_str = str(amount_obj.get("value", "0"))
    decimals = int(amount_obj.get("decimals", 0))

    try:
        amount_int = Decimal(value_str)
        token_units = amount_int / (Decimal(10) ** decimals)
        price_usd = Decimal(str(matching_pos["price_usd"]))
        order_usd = token_units * price_usd
        return order_usd, None
    except Exception as exc:
        return None, f"Failed calculating USD value for token '{token}': {exc}"


def _format_usd(amount: Decimal) -> str:
    """Format decimal amount as a USD string compliant with UsdString pattern."""
    return f"{amount:.2f}"


_DEFAULT_RISK_INPUTS = object()
STABLE_TOKENS: frozenset[str] = frozenset({"USDC", "USDT", "DAI"})


def evaluate(
    ips: LoadedIps | Mapping[str, Any],
    state: Mapping[str, Any],
    order: Mapping[str, Any] | None = None,
    risk_inputs: Mapping[str, Any] | None = _DEFAULT_RISK_INPUTS,  # type: ignore[assignment]
    now: datetime | None = None,
    *,
    origin: str = "planner",
    tier: str = "T2",
    emergency_stop: bool = False,
    global_stop: bool = False,
) -> dict[str, Any]:
    """Pure evaluation function returning a Decision dict.

    Evaluates ACCESS, LIMIT, ALLOC, and RISK rule families, and owner-only function
    rules per design 7.3. Does not mutate inputs, perform I/O, make network requests,
    or call any LLM.
    """
    if state:
        global_stop = global_stop or bool(state.get("global_stop", False))
        emergency_stop = emergency_stop or bool(state.get("emergency_stop", False))
        if "tier" in state:
            tier = str(state["tier"])

    if isinstance(ips, LoadedIps):
        ips_data: Mapping[str, Any] = ips.ips
        ips_hash: str = ips.hash
    elif hasattr(ips, "hash") and isinstance(ips.hash, str):
        ips_data = ips.ips if hasattr(ips, "ips") else ips
        ips_hash = ips.hash
    else:
        ips_data = ips
        ips_hash = compute_ips_hash(ips)

    rules_hit: list[dict[str, Any]] = []
    reasons: list[str] = []

    universe = ips_data.get("universe", {})
    allowed_tokens = set(universe.get("tokens", []))
    allowed_protocols = set(universe.get("protocols", []))
    allowed_functions = set(universe.get("functions", []))
    allowed_recipients = set(universe.get("recipients", []))

    order_dict: Mapping[str, Any] = order if order is not None else {}
    order_token = order_dict.get("token")
    order_function = order_dict.get("function")
    order_protocol = order_dict.get("protocol")
    order_recipient = order_dict.get("recipient")

    order_usd: Decimal | None = None

    # --- ACCESS & LIMIT Rules (only if order is provided) ---
    if order is not None:
        if order_function in OWNER_FUNCTIONS:
            # 1. ACCESS.OWNER_ONLY_FUNCTION
            if origin != "owner":
                rules_hit.append(
                    {
                        "code": "ACCESS.OWNER_ONLY_FUNCTION",
                        "evidence": {
                            "function": str(order_function),
                            "origin": str(origin),
                        },
                    }
                )
                reasons.append(
                    f"Owner function '{order_function}' requires owner origin, got '{origin}'"
                )

            # 2. ACCESS.TOKEN_NOT_ALLOWED
            if order_function == "mint":
                if order_token != "USDC":
                    rules_hit.append(
                        {
                            "code": "ACCESS.TOKEN_NOT_ALLOWED",
                            "evidence": {"token": str(order_token)},
                        }
                    )
                    reasons.append(f"Mint token must be Aave test USDC, got '{order_token}'")
            elif order_function == "transfer":
                if order_token not in ("USDC", "ETH"):
                    rules_hit.append(
                        {
                            "code": "ACCESS.TOKEN_NOT_ALLOWED",
                            "evidence": {"token": str(order_token)},
                        }
                    )
                    reasons.append(f"Transfer token must be USDC or ETH, got '{order_token}'")

            # 3. ACCESS.RECIPIENT_NOT_ALLOWED
            if order_function == "transfer":
                if order_recipient != "owner":
                    rules_hit.append(
                        {
                            "code": "ACCESS.RECIPIENT_NOT_ALLOWED",
                            "evidence": {"recipient": str(order_recipient)},
                        }
                    )
                    reasons.append(f"Transfer recipient must be 'owner', got '{order_recipient}'")
            elif order_function == "mint":
                if order_recipient != "self":
                    rules_hit.append(
                        {
                            "code": "ACCESS.RECIPIENT_NOT_ALLOWED",
                            "evidence": {"recipient": str(order_recipient)},
                        }
                    )
                    reasons.append(f"Mint recipient must be 'self', got '{order_recipient}'")

            # 4. TIER / STOP rules
            if global_stop:
                rules_hit.append(
                    {
                        "code": "TIER.GLOBAL_STOP",
                        "evidence": {"global_stop": True},
                    }
                )
                reasons.append("Global emergency stop is engaged; no order is executed")
            elif order_function == "transfer":
                # Exception: transfer to the owner is allowed under stop and under T0/T1
                pass
            else:
                if emergency_stop:
                    rules_hit.append(
                        {
                            "code": "TIER.EMERGENCY_STOP",
                            "evidence": {"emergency_stop": True, "tier": str(tier)},
                        }
                    )
                    reasons.append("Emergency stop is engaged; owner function denied")
                elif tier == "T0":
                    rules_hit.append(
                        {
                            "code": "TIER.READ_ONLY",
                            "evidence": {"tier": "T0"},
                        }
                    )
                    reasons.append("Tier T0 is read-only; no order is executed")
                elif tier == "T1":
                    rules_hit.append(
                        {
                            "code": "TIER.PROPOSE_ONLY",
                            "evidence": {"tier": "T1"},
                        }
                    )
                    reasons.append("Tier T1 is propose-only; no order is executed")
        else:
            if global_stop:
                rules_hit.append(
                    {
                        "code": "TIER.GLOBAL_STOP",
                        "evidence": {"global_stop": True},
                    }
                )
                reasons.append("Global emergency stop is engaged; no order is executed")
            elif emergency_stop:
                rules_hit.append(
                    {
                        "code": "TIER.EMERGENCY_STOP",
                        "evidence": {"emergency_stop": True, "tier": str(tier)},
                    }
                )
                reasons.append("Emergency stop is engaged; no order is executed")
            elif tier == "T0":
                rules_hit.append(
                    {
                        "code": "TIER.READ_ONLY",
                        "evidence": {"tier": "T0"},
                    }
                )
                reasons.append("Tier T0 is read-only; no order is executed")
            elif tier == "T1":
                rules_hit.append(
                    {
                        "code": "TIER.PROPOSE_ONLY",
                        "evidence": {"tier": "T1"},
                    }
                )
                reasons.append("Tier T1 is propose-only; no order is executed")

            # 1. ACCESS.TOKEN_NOT_ALLOWED
            if order_token not in allowed_tokens:
                rules_hit.append(
                    {
                        "code": "ACCESS.TOKEN_NOT_ALLOWED",
                        "evidence": {"token": str(order_token)},
                    }
                )
                reasons.append(f"Token '{order_token}' is not in the IPS universe")

            token_out = order_dict.get("token_out")
            if token_out and token_out not in allowed_tokens:
                rules_hit.append(
                    {
                        "code": "ACCESS.TOKEN_NOT_ALLOWED",
                        "evidence": {"token_out": str(token_out)},
                    }
                )
                reasons.append(f"Output token '{token_out}' is not in the IPS universe")

            # 2. ACCESS.PROTOCOL_NOT_ALLOWED
            if order_protocol not in allowed_protocols:
                rules_hit.append(
                    {
                        "code": "ACCESS.PROTOCOL_NOT_ALLOWED",
                        "evidence": {"protocol": str(order_protocol)},
                    }
                )
                reasons.append(f"Protocol '{order_protocol}' is not in the IPS universe")

            # 3. ACCESS.FUNCTION_NOT_ALLOWED
            if order_function not in allowed_functions:
                rules_hit.append(
                    {
                        "code": "ACCESS.FUNCTION_NOT_ALLOWED",
                        "evidence": {"function": str(order_function)},
                    }
                )
                reasons.append(f"Function '{order_function}' is not in the IPS universe")

            # 4. ACCESS.RECIPIENT_NOT_ALLOWED
            if order_recipient not in allowed_recipients:
                rules_hit.append(
                    {
                        "code": "ACCESS.RECIPIENT_NOT_ALLOWED",
                        "evidence": {"recipient": str(order_recipient)},
                    }
                )
                reasons.append(f"Recipient '{order_recipient}' is not in the IPS universe")

            # 5. ACCESS.UNLIMITED_APPROVAL
            if order_function == "approve":
                amount_dict = order_dict.get("amount", {})
                amount_val = int(amount_dict.get("value", 0))
                is_unlimited = False
                approval_evidence: dict[str, Any] = {"amount": str(amount_val)}

                if amount_val >= UINT256_MAX:
                    is_unlimited = True
                    approval_evidence["max_uint256"] = True
                    reasons.append("Approve amount is unlimited (maximum uint256)")

                for limit_key in ("supply_amount", "withdraw_amount"):
                    if limit_key in order_dict:
                        limit_spec = order_dict[limit_key]
                        if isinstance(limit_spec, (dict, Mapping)):
                            limit_val = int(limit_spec.get("value", 0))
                        else:
                            limit_val = int(limit_spec)
                        if amount_val > limit_val:
                            is_unlimited = True
                            approval_evidence[limit_key] = str(limit_val)
                            readable_target = limit_key.replace("_", " ")
                            reasons.append(
                                f"Approve amount {amount_val} exceeds order's own "
                                f"{readable_target} {limit_val}"
                            )

                if is_unlimited:
                    rules_hit.append(
                        {
                            "code": "ACCESS.UNLIMITED_APPROVAL",
                            "evidence": approval_evidence,
                        }
                    )

            # --- LIMIT Rules ---
            limits = ips_data.get("limits", {})
            per_tx_usd_limit_str = str(limits.get("per_tx_usd", "0"))
            per_tx_limit = Decimal(per_tx_usd_limit_str)
            per_day_usd_limit_str = str(limits.get("per_day_usd", "0"))
            per_day_limit = Decimal(per_day_usd_limit_str)

            calculated_usd, price_err = _calculate_order_usd(order_dict, state)
            order_usd = calculated_usd

            # 6. LIMIT.PER_TX
            if price_err is not None:
                rules_hit.append(
                    {
                        "code": "LIMIT.PER_TX",
                        "evidence": {
                            "per_tx_usd": per_tx_usd_limit_str,
                            "price_missing": True,
                            "token": str(order_token),
                        },
                    }
                )
                reasons.append(f"Cannot evaluate transaction value: {price_err}")
            else:
                order_usd_str = _format_usd(order_usd)
                if order_usd > per_tx_limit:
                    rules_hit.append(
                        {
                            "code": "LIMIT.PER_TX",
                            "evidence": {
                                "order_usd": order_usd_str,
                                "per_tx_usd": per_tx_usd_limit_str,
                            },
                        }
                    )
                    reasons.append(
                        f"Order value {order_usd_str} USD is above the per-transaction limit "
                        f"of {per_tx_usd_limit_str} USD"
                    )

            # 7. LIMIT.PER_DAY
            if order_usd is not None:
                spent_24h_str = str(state.get("spent_24h_usd", "0"))
                spent_24h = Decimal(spent_24h_str)
                projected_spend = spent_24h + order_usd
                if projected_spend > per_day_limit:
                    rules_hit.append(
                        {
                            "code": "LIMIT.PER_DAY",
                            "evidence": {
                                "order_usd": _format_usd(order_usd),
                                "per_day_usd": per_day_usd_limit_str,
                                "projected_24h_usd": _format_usd(projected_spend),
                                "spent_24h_usd": spent_24h_str,
                            },
                        }
                    )
                    reasons.append(
                        f"Order would push 24-hour spend to {_format_usd(projected_spend)} USD, "
                        f"above the daily limit of {per_day_usd_limit_str} USD"
                    )

            # 8. LIMIT.TX_PER_HOUR
            max_tx_per_hour = int(limits.get("max_tx_per_hour", 0))
            tx_count_1h = int(state.get("tx_count_1h", 0))
            if tx_count_1h >= max_tx_per_hour:
                rules_hit.append(
                    {
                        "code": "LIMIT.TX_PER_HOUR",
                        "evidence": {
                            "max_tx_per_hour": max_tx_per_hour,
                            "tx_count_1h": tx_count_1h,
                        },
                    }
                )
                reasons.append(
                    f"Hourly transaction count {tx_count_1h} is already at or above limit "
                    f"of {max_tx_per_hour}"
                )

    # --- ALLOC and RISK Rules ---
    # Evaluated when risk_inputs is passed or when order is None (skipped for owner functions)
    if (risk_inputs is not _DEFAULT_RISK_INPUTS or order is None) and (
        order_function not in OWNER_FUNCTIONS
    ):
        allocation = ips_data.get("allocation", {})
        max_weight_per_asset_bps = int(allocation.get("max_weight_per_asset_bps", 0))
        min_stable_weight_bps = int(allocation.get("min_stable_weight_bps", 10000))
        rebalance_band_bps = int(allocation.get("rebalance_band_bps", 0))

        # Current portfolio token values and total
        current_token_values: dict[str, Decimal] = {}
        total_val = Decimal("0")
        for pos in state.get("positions", []):
            pos_val = Decimal(str(pos.get("value_usd", "0")))
            tok = pos.get("token")
            if tok:
                current_token_values[tok] = current_token_values.get(tok, Decimal("0")) + pos_val
            total_val += pos_val

        if total_val == 0 and "total_value_usd" in state:
            total_val = Decimal(str(state["total_value_usd"]))

        # Check pre-order deviation from allocation thresholds
        if total_val > 0:
            stable_val_pre = sum(current_token_values.get(t, Decimal("0")) for t in STABLE_TOKENS)
            stable_weight_pre_bps = int(round((stable_val_pre * 10000) / total_val))
            delta_stable = max(0, min_stable_weight_bps - stable_weight_pre_bps)

            delta_risky = 0
            for tok, val in current_token_values.items():
                if tok not in STABLE_TOKENS:
                    weight_bps = int(round((val * 10000) / total_val))
                    if weight_bps > max_weight_per_asset_bps:
                        delta_risky = max(delta_risky, weight_bps - max_weight_per_asset_bps)

            deviation_bps = max(delta_stable, delta_risky)
        else:
            deviation_bps = 0

        # ALLOC.WITHIN_REBALANCE_BAND: portfolio deviation is inside rebalance band
        if 0 < deviation_bps <= rebalance_band_bps:
            rules_hit.append(
                {
                    "code": "ALLOC.WITHIN_REBALANCE_BAND",
                    "evidence": {
                        "deviation_bps": deviation_bps,
                        "rebalance_band_bps": rebalance_band_bps,
                    },
                }
            )
            reasons.append(
                f"Portfolio deviation {deviation_bps} bps is within rebalance band of "
                f"{rebalance_band_bps} bps; no action needed"
            )

        # Calculate post-order weights if order is present
        if order is not None:
            post_token_values = dict(current_token_values)
            if order_function == "exactInputSingle" and order_usd is not None:
                in_tok = order_dict.get("token")
                out_tok = order_dict.get("token_out")
                if in_tok:
                    post_token_values[in_tok] = max(
                        Decimal("0"), post_token_values.get(in_tok, Decimal("0")) - order_usd
                    )
                if out_tok:
                    post_token_values[out_tok] = (
                        post_token_values.get(out_tok, Decimal("0")) + order_usd
                    )

            post_total = sum(post_token_values.values()) if post_token_values else total_val
            if post_total > 0:
                stable_val_post = sum(post_token_values.get(t, Decimal("0")) for t in STABLE_TOKENS)
                stable_weight_post_bps = int(round((stable_val_post * 10000) / post_total))

                # ALLOC.MIN_STABLE_WEIGHT
                if stable_weight_post_bps < min_stable_weight_bps:
                    rules_hit.append(
                        {
                            "code": "ALLOC.MIN_STABLE_WEIGHT",
                            "evidence": {
                                "min_stable_weight_bps": min_stable_weight_bps,
                                "stable_weight_bps": stable_weight_post_bps,
                            },
                        }
                    )
                    reasons.append(
                        f"Post-order stablecoin weight {stable_weight_post_bps} bps is below "
                        f"the minimum of {min_stable_weight_bps} bps"
                    )

                # ALLOC.MAX_WEIGHT_PER_ASSET
                for tok, val in post_token_values.items():
                    if tok not in STABLE_TOKENS:
                        tok_weight_bps = int(round((val * 10000) / post_total))
                        if tok_weight_bps > max_weight_per_asset_bps:
                            rules_hit.append(
                                {
                                    "code": "ALLOC.MAX_WEIGHT_PER_ASSET",
                                    "evidence": {
                                        "max_weight_per_asset_bps": max_weight_per_asset_bps,
                                        "token": str(tok),
                                        "weight_bps": tok_weight_bps,
                                    },
                                }
                            )
                            reasons.append(
                                f"Asset '{tok}' post-order weight {tok_weight_bps} bps exceeds "
                                f"maximum weight of {max_weight_per_asset_bps} bps"
                            )

        # --- RISK Rules ---
        ips_risk = ips_data.get("risk", {})
        needs_risk_inputs = order_function in ("supply", "withdraw", "exactInputSingle")

        # Fail closed: absent, non-mapping or incomplete risk inputs never pass as fresh.
        risk_inputs_ok = isinstance(risk_inputs, Mapping) and all(
            key in risk_inputs for key in _REQUIRED_RISK_KEYS
        )
        if not risk_inputs_ok:
            if needs_risk_inputs:
                evidence = "missing" if risk_inputs is None else "incomplete"
                rules_hit.append(
                    {
                        "code": "RISK.DATA_STALE",
                        "evidence": {"risk_inputs": evidence},
                    }
                )
                reasons.append(f"Risk inputs are required for this order but are {evidence}")
        else:
            # 1. RISK.DATA_STALE (age check)
            data_age_seconds = int(risk_inputs.get("data_age_seconds", 0))
            max_data_age = int(ips_risk.get("max_data_age_seconds", 0))
            if data_age_seconds > max_data_age:
                rules_hit.append(
                    {
                        "code": "RISK.DATA_STALE",
                        "evidence": {
                            "data_age_seconds": data_age_seconds,
                            "max_data_age_seconds": max_data_age,
                        },
                    }
                )
                reasons.append(
                    f"Risk inputs data age {data_age_seconds}s exceeds maximum allowed age "
                    f"of {max_data_age}s"
                )

            # 2. RISK.POOL_TVL_TOO_LOW
            pool_tvl_str = str(risk_inputs.get("pool_tvl_usd", "0"))
            min_tvl_str = str(ips_risk.get("min_pool_tvl_usd", "0"))
            pool_tvl = Decimal(pool_tvl_str)
            min_tvl = Decimal(min_tvl_str)
            if pool_tvl < min_tvl:
                rules_hit.append(
                    {
                        "code": "RISK.POOL_TVL_TOO_LOW",
                        "evidence": {
                            "min_pool_tvl_usd": min_tvl_str,
                            "pool_tvl_usd": pool_tvl_str,
                        },
                    }
                )
                reasons.append(
                    f"Pool TVL of {pool_tvl_str} USD is below minimum "
                    f"threshold of {min_tvl_str} USD"
                )

            # 3. RISK.REWARD_APY_SHARE_TOO_HIGH
            reward_apy_share_bps = int(risk_inputs.get("reward_apy_share_bps", 0))
            max_reward_share_bps = int(ips_risk.get("max_reward_apy_share_bps", 0))
            if reward_apy_share_bps > max_reward_share_bps:
                rules_hit.append(
                    {
                        "code": "RISK.REWARD_APY_SHARE_TOO_HIGH",
                        "evidence": {
                            "max_reward_apy_share_bps": max_reward_share_bps,
                            "reward_apy_share_bps": reward_apy_share_bps,
                        },
                    }
                )
                reasons.append(
                    f"Reward APY share {reward_apy_share_bps} bps exceeds maximum allowed share "
                    f"of {max_reward_share_bps} bps"
                )

            # 4. RISK.APY_UNSTABLE
            apy_deviation_bps = int(risk_inputs.get("apy_deviation_bps", 0))
            max_apy_deviation_bps = int(ips_risk.get("max_apy_deviation_bps", 0))
            if apy_deviation_bps > max_apy_deviation_bps:
                rules_hit.append(
                    {
                        "code": "RISK.APY_UNSTABLE",
                        "evidence": {
                            "apy_deviation_bps": apy_deviation_bps,
                            "max_apy_deviation_bps": max_apy_deviation_bps,
                        },
                    }
                )
                reasons.append(
                    f"APY deviation {apy_deviation_bps} bps exceeds maximum limit of "
                    f"{max_apy_deviation_bps} bps"
                )

            # 5. RISK.DEPEG_TOO_WIDE
            stablecoin_depeg_bps = risk_inputs.get("stablecoin_depeg_bps")
            max_depeg_bps = int(ips_risk.get("max_depeg_bps", 0))
            if stablecoin_depeg_bps is not None and int(stablecoin_depeg_bps) > max_depeg_bps:
                rules_hit.append(
                    {
                        "code": "RISK.DEPEG_TOO_WIDE",
                        "evidence": {
                            "max_depeg_bps": max_depeg_bps,
                            "stablecoin_depeg_bps": int(stablecoin_depeg_bps),
                        },
                    }
                )
                reasons.append(
                    f"Stablecoin depeg {stablecoin_depeg_bps} bps exceeds maximum limit of "
                    f"{max_depeg_bps} bps"
                )

            # 6. RISK.UTILIZATION_TOO_HIGH
            utilization_bps = risk_inputs.get("utilization_bps")
            max_utilization_bps = int(ips_risk.get("max_utilization_bps", 0))
            if utilization_bps is not None and int(utilization_bps) > max_utilization_bps:
                rules_hit.append(
                    {
                        "code": "RISK.UTILIZATION_TOO_HIGH",
                        "evidence": {
                            "max_utilization_bps": max_utilization_bps,
                            "utilization_bps": int(utilization_bps),
                        },
                    }
                )
                reasons.append(
                    f"Lending pool utilization {utilization_bps} bps exceeds maximum limit of "
                    f"{max_utilization_bps} bps"
                )

            # 7. RISK.SLIPPAGE_TOO_HIGH
            max_slippage_bps = int(ips_risk.get("max_slippage_bps", 0))
            est_slippage_bps = risk_inputs.get("estimated_slippage_bps")
            order_slippage_bps = order_dict.get("max_slippage_bps")
            slippage_evidence: dict[str, Any] = {"max_slippage_bps": max_slippage_bps}
            slippage_hit = False

            if est_slippage_bps is not None and int(est_slippage_bps) > max_slippage_bps:
                slippage_hit = True
                slippage_evidence["estimated_slippage_bps"] = int(est_slippage_bps)
            if order_slippage_bps is not None and int(order_slippage_bps) > max_slippage_bps:
                slippage_hit = True
                slippage_evidence["order_slippage_bps"] = int(order_slippage_bps)

            if slippage_hit:
                rules_hit.append(
                    {
                        "code": "RISK.SLIPPAGE_TOO_HIGH",
                        "evidence": slippage_evidence,
                    }
                )
                reasons.append(
                    f"Slippage exceeds maximum allowed threshold of {max_slippage_bps} bps"
                )

            # 8. RISK.COST_EXCEEDS_BENEFIT
            cost_str = str(risk_inputs.get("estimated_cost_usd", "0"))
            benefit_str = str(risk_inputs.get("estimated_benefit_usd", "0"))
            min_benefit_cost_ratio_bps = int(ips_risk.get("min_benefit_cost_ratio_bps", 0))
            cost_dec = Decimal(cost_str)
            benefit_dec = Decimal(benefit_str)

            if cost_dec > 0:
                actual_ratio_bps = int((benefit_dec * 10000) // cost_dec)
                if (benefit_dec * 10000) < (cost_dec * min_benefit_cost_ratio_bps):
                    rules_hit.append(
                        {
                            "code": "RISK.COST_EXCEEDS_BENEFIT",
                            "evidence": {
                                "actual_ratio_bps": actual_ratio_bps,
                                "estimated_benefit_usd": benefit_str,
                                "estimated_cost_usd": cost_str,
                                "min_benefit_cost_ratio_bps": min_benefit_cost_ratio_bps,
                            },
                        }
                    )
                    reasons.append(
                        f"Benefit/cost ratio {actual_ratio_bps} bps is below required multiple "
                        f"of {min_benefit_cost_ratio_bps} bps"
                    )

    # --- Synthesize Decision ---
    has_violations = any(h["code"] != "ALLOC.WITHIN_REBALANCE_BAND" for h in rules_hit)
    is_no_action = (
        any(h["code"] == "ALLOC.WITHIN_REBALANCE_BAND" for h in rules_hit) and not has_violations
    )

    if has_violations:
        decision = "deny"
        if len(reasons) == 1:
            reason = f"{reasons[0]}."
        else:
            reason = f"Order denied: {'; '.join(reasons)}."
        if len(reason) > 500:
            reason = reason[:497] + "..."
    elif is_no_action:
        decision = "no_action"
        reason = f"{reasons[0]}." if reasons else "Inside rebalance band; no action needed."
        if len(reason) > 500:
            reason = reason[:497] + "..."
    else:
        decision = "allow"
        reason = "Order is inside every limit of the IPS."

    # Decide timestamp
    if now is None:
        now_dt = datetime.now(UTC)
    else:
        if now.tzinfo is None:
            now_dt = now.replace(tzinfo=UTC)
        else:
            now_dt = now.astimezone(UTC)
    decided_at = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    order_id = str(order.get("order_id", "")) if order is not None else None

    result: dict[str, Any] = {
        "schema_version": 1,
        "order_id": order_id,
        "decision": decision,
        "rules_hit": rules_hit,
        "reason": reason,
        "ips_hash": ips_hash,
        "decided_at": decided_at,
    }
    if is_no_action:
        result["outcome"] = "no_action"
        result["no_action"] = {
            "rules_hit": rules_hit,
            "reason": reason,
        }
    return result
