"""Policy engine: pure evaluation of orders against IPS and portfolio state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from core.policy.ips import LoadedIps, compute_ips_hash

UINT256_MAX: int = (1 << 256) - 1


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


def evaluate(
    ips: LoadedIps | Mapping[str, Any],
    state: Mapping[str, Any],
    order: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure evaluation function returning a Decision dict.

    Evaluates ACCESS and LIMIT rule families. Does not mutate inputs, perform I/O,
    make network requests, or call any LLM.
    """
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

    # --- ACCESS Rules ---
    # 1. ACCESS.TOKEN_NOT_ALLOWED
    order_token = order.get("token")
    if order_token not in allowed_tokens:
        rules_hit.append(
            {
                "code": "ACCESS.TOKEN_NOT_ALLOWED",
                "evidence": {"token": str(order_token)},
            }
        )
        reasons.append(f"Token '{order_token}' is not in the IPS universe")

    token_out = order.get("token_out")
    if token_out and token_out not in allowed_tokens:
        rules_hit.append(
            {
                "code": "ACCESS.TOKEN_NOT_ALLOWED",
                "evidence": {"token_out": str(token_out)},
            }
        )
        reasons.append(f"Output token '{token_out}' is not in the IPS universe")

    # 2. ACCESS.PROTOCOL_NOT_ALLOWED
    order_protocol = order.get("protocol")
    if order_protocol not in allowed_protocols:
        rules_hit.append(
            {
                "code": "ACCESS.PROTOCOL_NOT_ALLOWED",
                "evidence": {"protocol": str(order_protocol)},
            }
        )
        reasons.append(f"Protocol '{order_protocol}' is not in the IPS universe")

    # 3. ACCESS.FUNCTION_NOT_ALLOWED
    order_function = order.get("function")
    if order_function not in allowed_functions:
        rules_hit.append(
            {
                "code": "ACCESS.FUNCTION_NOT_ALLOWED",
                "evidence": {"function": str(order_function)},
            }
        )
        reasons.append(f"Function '{order_function}' is not in the IPS universe")

    # 4. ACCESS.RECIPIENT_NOT_ALLOWED
    order_recipient = order.get("recipient")
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
        amount_dict = order.get("amount", {})
        amount_val = int(amount_dict.get("value", 0))
        is_unlimited = False
        approval_evidence: dict[str, Any] = {"amount": str(amount_val)}

        if amount_val >= UINT256_MAX:
            is_unlimited = True
            approval_evidence["max_uint256"] = True
            reasons.append("Approve amount is unlimited (maximum uint256)")

        for limit_key in ("supply_amount", "withdraw_amount"):
            if limit_key in order:
                limit_spec = order[limit_key]
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

    order_usd, price_err = _calculate_order_usd(order, state)

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

    # --- Synthesize Decision ---
    if rules_hit:
        decision = "deny"
        if len(reasons) == 1:
            reason = f"{reasons[0]}."
        else:
            reason = f"Order denied: {'; '.join(reasons)}."
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

    order_id = str(order.get("order_id", ""))

    return {
        "schema_version": 1,
        "order_id": order_id,
        "decision": decision,
        "rules_hit": rules_hit,
        "reason": reason,
        "ips_hash": ips_hash,
        "decided_at": decided_at,
    }
