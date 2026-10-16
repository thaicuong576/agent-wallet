"""Chat text and steps, composed by code from audit records (ticket 115).

Nothing here comes from model prose except the optional `reply`, which is appended under its own
label ("Agent says") and is never part of a step. A step is rebuilt from the audit record the
gateway wrote, so what the owner sees as an outcome is what was decided and signed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from core.runner.runner import SKIP_PREFIX

TEXT_MAX_CHARS = 3900
AGENT_SAYS = "Agent says: "

FALLBACK_UNCONFIGURED = "unconfigured"
FALLBACK_LLM_ERROR = "llm_error"
FALLBACK_NOTES = {
    FALLBACK_UNCONFIGURED: (
        "No language model is configured, so the rule-based fallback planner answered this."
    ),
    FALLBACK_LLM_ERROR: (
        "The language model was unavailable, so the rule-based fallback planner answered this."
    ),
}

RECIPIENT_LABELS = {
    "aave_pool": "the Aave pool",
    "uniswap_router": "the Uniswap router",
    "self": "the agent wallet",
}

# Plain words for the codes an owner meets in chat. Anything else uses the policy engine's reason.
CODE_TEXT = {
    "TIER.READ_ONLY": (
        "your agent is in read-only mode (tier T0), so it cannot act yet. Activate it to let it "
        "work inside your policy"
    ),
    "TIER.PROPOSE_ONLY": (
        "your agent may only propose (tier T1), so nothing runs until you raise its tier"
    ),
    "TIER.EMERGENCY_STOP": "the emergency stop is on, so the agent is frozen",
    "TIER.GLOBAL_STOP": "the service operator has paused every agent",
    "ACCESS.OWNER_ONLY_FUNCTION": (
        "that is an owner action (minting or withdrawing) and the agent may never do it"
    ),
}

ERROR_TEXT = {
    "GAS_BELOW_FLOOR": "the agent wallet has too little ETH for gas",
    "RPC_UNAVAILABLE": "the chain RPC was unavailable",
    "GATEWAY_BUSY": "the agent was busy with another action",
    "RECONCILE_REQUIRED": "a previous transaction needs to be reconciled first",
    "SIMULATION_FAILED": "the transaction failed a dry run, so it was never sent",
    "TX_REVERTED": "the transaction was reverted on chain",
    "INTERNAL_ERROR": "something went wrong on the server",
    "GATEWAY_ERROR": "the gateway could not handle the order",
}

CANCEL_REASON = "owner-cancelled"
_SKIPPED = re.compile(rf"^{SKIP_PREFIX} (\w+) \((\d+(?:\.\d+)?) (\w+)\): (.*)$", re.DOTALL)


def _units(amount: Mapping[str, Any] | None) -> str | None:
    if not isinstance(amount, Mapping):
        return None
    try:
        value, decimals = int(amount["value"]), int(amount["decimals"])
    except (KeyError, TypeError, ValueError):
        return None
    whole, frac = divmod(value, 10**decimals) if decimals else (value, 0)
    if not decimals:
        return str(whole)
    return f"{whole}.{frac:0{decimals}d}".rstrip("0").rstrip(".")


def _rule_codes(rec: Mapping[str, Any]) -> list[str]:
    block = rec.get("decision") or rec.get("no_action") or {}
    return [str(h.get("code")) for h in block.get("rules_hit", []) if h.get("code")]


def _sentence(text: str) -> str:
    text = text.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text[:1].upper() + text[1:]


def _protocol_label(order: Mapping[str, Any]) -> str | None:
    """Where the money goes, in words, for every protocol except Aave v3 (kept as before)."""
    pid = str(order.get("protocol") or "")
    if pid in ("", "aave-v3", "wallet"):
        return None
    from core.protocols import registry

    spec = registry.get(pid)
    label = spec.name if spec is not None else pid
    market = order.get("market")
    if market:
        label += f" ({str(market).upper().replace('-', '/')} market)"
    return label


def action_phrase(order: Mapping[str, Any], *, past: bool) -> str:
    """'approved 10 USDC for the Aave pool' or, with past=False, 'approve 10 USDC ...'."""
    func = str(order.get("function"))
    amount = _units(order.get("amount"))
    token = str(order.get("token") or "")
    qty = f"{amount} {token}".strip() if amount else token or "tokens"
    proto = _protocol_label(order)
    where = proto or RECIPIENT_LABELS.get(str(order.get("recipient")), "the recipient")
    if func == "approve":
        return f"{'approved' if past else 'approve'} {qty} for {where}"
    if func == "supply":
        return f"{'supplied' if past else 'supply'} {qty} to {proto or 'Aave'}"
    if func == "withdraw":
        return f"{'withdrew' if past else 'withdraw'} {qty} from {proto or 'Aave'}"
    if func == "exactInputSingle":
        out = order.get("token_out")
        tail = f" for {out}" if out else ""
        on = f" on {proto}" if proto else ""
        return f"{'swapped' if past else 'swap'} {qty}{tail}{on}"
    if func == "mint":
        return f"{'minted' if past else 'mint'} {qty}"
    return f"{'ran' if past else 'run'} {func} on {qty}"


def _why(rec: Mapping[str, Any]) -> str:
    """Plain reason an order did not go through: our wording for known codes, else the engine's."""
    codes = _rule_codes(rec)
    for code in codes:
        if code in CODE_TEXT:
            return CODE_TEXT[code]
    block = rec.get("decision") or rec.get("no_action") or {}
    reason = str(block.get("reason") or "").strip().rstrip(".")
    if reason:
        return reason[:1].lower() + reason[1:] if reason[1:2].islower() else reason
    return "the policy engine blocked this order"


def _error(rec: Mapping[str, Any]) -> str:
    code = str((rec.get("execution") or {}).get("error_code") or "EXECUTION_FAILED")
    return ERROR_TEXT.get(code, "the transaction could not be completed")


def step_from_record(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    """One audit record as a chat step, or None for a record that is not a step."""
    order = rec.get("order")
    execution = rec.get("execution") or {}
    block = rec.get("decision") or rec.get("no_action") or {}
    if isinstance(order, Mapping) and order:
        outcome = str(rec.get("outcome"))
        phrase = action_phrase(order, past=outcome == "executed")
        if outcome == "executed":
            explanation = _sentence(phrase)
        elif outcome == "pending_approval":
            explanation = f"Waiting for your approval in Approvals before the agent can {phrase}."
        elif outcome == "failed":
            explanation = f"The agent tried to {phrase}, but {_error(rec)}. Nothing was lost."
        else:
            explanation = f"The agent could not {phrase}: {_why(rec)}. Nothing was sent."
        return {
            "order_id": rec.get("order_id"),
            "function": order.get("function"),
            "token": order.get("token"),
            "amount": order.get("amount"),
            "outcome": outcome,
            "reason": block.get("reason"),
            "rule_codes": _rule_codes(rec),
            "tx_hash": execution.get("tx_hash") if outcome == "executed" else None,
            "explanation": explanation,
        }
    reason = str(block.get("reason") or "")
    match = _SKIPPED.match(reason)
    if rec.get("outcome") == "no_action" and match:
        func, amount, token, why = match.groups()
        value = str(int(round(float(amount) * 10**6))) if token == "USDC" else None
        if why == CANCEL_REASON:
            why = "you cancelled this run."
        return {
            "order_id": None,
            "function": func,
            "token": token,
            "amount": {"value": value, "decimals": 6} if value is not None else None,
            "outcome": "skipped",
            "reason": reason,
            "rule_codes": [],
            "tx_hash": None,
            "explanation": f"Not attempted: {why}",
        }
    return None


def steps_from_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    steps = (step_from_record(r) for r in records)
    return [s for s in steps if s is not None]


def _join(parts: list[str]) -> str:
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def compose_agent_text(
    records: Sequence[Mapping[str, Any]],
    *,
    reply: str | None = None,
    plan_reason: str | None = None,
    validation_errors: Sequence[str] = (),
    fallback: str | None = None,
    stop_text: str | None = None,
) -> str:
    """The agent message: what code recorded, then the model's reply under its own label."""
    steps_rec = [r for r in records if r.get("order") or _SKIPPED.match(_reason_of(r))]
    done = [r for r in steps_rec if r.get("outcome") == "executed"]
    parts: list[str] = []

    if stop_text is not None:
        parts.append(f"I did not act: {stop_text}.")
    elif not steps_rec:
        if validation_errors:
            parts.append(
                "I could not turn that into a valid order, so nothing was done. "
                "No order was proposed and nothing was signed."
            )
        elif reply:
            parts.append("No transaction was made.")
        else:
            parts.append("I did not propose any transaction.")
    else:
        if done:
            doing = _join([action_phrase(r["order"], past=True) for r in done])
            parts.append(_sentence("I " + doing))
        problem = next(
            (r for r in steps_rec if r.get("outcome") not in ("executed", "no_action")), None
        )
        if problem is not None:
            order = problem["order"]
            outcome = problem.get("outcome")
            phrase = action_phrase(order, past=False)
            if outcome == "pending_approval":
                parts.append(
                    f"To {phrase} I need your approval: it is waiting in Approvals."
                    if done
                    else f"I prepared an order to {phrase}. It is waiting for your approval "
                    "in Approvals."
                )
            elif outcome == "failed":
                parts.append(
                    f"I tried to {phrase}, but {_error(problem)}. Nothing was lost."
                    if not done
                    else f"Then I tried to {phrase}, but {_error(problem)}."
                )
            else:
                lead = "I did not do the rest" if done else "I did not act"
                parts.append(f"{lead}: {_why(problem)}.")
            skipped = [r for r in steps_rec if not r.get("order")]
            if skipped and not _cancelled(skipped):
                parts.append(
                    "The next step was not attempted."
                    if len(skipped) == 1
                    else f"The next {len(skipped)} steps were not attempted."
                )
        cancelled = [r for r in steps_rec if not r.get("order") and _cancelled([r])]
        if cancelled:
            parts.append(
                "You cancelled the run, so the next step was not attempted."
                if len(cancelled) == 1
                else f"You cancelled the run, so the next {len(cancelled)} steps were not "
                "attempted."
            )
        hashes = [
            str((r.get("execution") or {}).get("tx_hash"))
            for r in done
            if (r.get("execution") or {}).get("tx_hash")
        ]
        if hashes:
            parts.append("Transactions: " + ", ".join(hashes) + ".")

    if fallback in FALLBACK_NOTES:
        parts.append(FALLBACK_NOTES[fallback])
    text = " ".join(parts)
    if reply:
        text += "\n\n" + AGENT_SAYS + reply
    elif plan_reason and not steps_rec and stop_text is None and not validation_errors:
        text += "\n\n" + AGENT_SAYS + plan_reason
    return text[:TEXT_MAX_CHARS]


def _cancelled(records: Sequence[Mapping[str, Any]]) -> bool:
    """True when every given skipped record was skipped by the owner's cancel."""
    return bool(records) and all(_reason_of(r).endswith(f": {CANCEL_REASON}") for r in records)


def _reason_of(rec: Mapping[str, Any]) -> str:
    return str((rec.get("no_action") or {}).get("reason") or "")


def text_for_error(error_code: str | None) -> str:
    """The agent message when the job ended before or without a plan (gas, rpc, crash)."""
    why = ERROR_TEXT.get(str(error_code), "something went wrong")
    return f"I did not act: {why}. Nothing was sent."


def user_facing_stop(code: str) -> str:
    return CODE_TEXT.get(code, "the agent is stopped")
