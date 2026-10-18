"""Core Planner proposing candidate orders validating against order.schema.json."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from core.agent.client import LLMClient, ReplyExtractor
from core.agent.reader import ReaderFacts

from .amount_check import check_amounts_match_instruction
from .history import (
    _CONTROL_RE,
    VALID_INTENTS,
    clean_reply,
    format_conversation_history,
)
from .prompt import PLANNER_OUTPUT_SCHEMA, render_system_prompt
from .result import PlanResult, _to_plain_dict
from .validation import (
    DEFAULT_ORDER_SCHEMA_PATH,
    PLANNER_FUNCTIONS,
    _get_strict_order_validator,
)

log = logging.getLogger("agent_wallet.planner")

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class Planner:
    """Proposes candidate orders or explicit no_action validating against order.schema.json."""

    def __init__(
        self,
        client: LLMClient | None = None,
        schema_path: str | Path | None = None,
    ) -> None:
        self.client = client
        self.schema_path = str(schema_path or DEFAULT_ORDER_SCHEMA_PATH)
        self._validator = _get_strict_order_validator(self.schema_path)

    def validate_order(self, order: dict[str, Any]) -> list[str]:
        """Validate an order dict strictly against order.schema.json and planner restrictions."""
        if not isinstance(order, dict):
            return ["Order must be a dictionary"]
        recip = order.get("recipient")
        if recip == "owner":
            return ["Recipient 'owner' is not permitted for planner orders"]
        func = order.get("function")
        if func not in PLANNER_FUNCTIONS:
            allowed = sorted(PLANNER_FUNCTIONS)
            return [f"Function '{func}' is not permitted for planner (expected one of {allowed})"]
        errors = [err.message for err in self._validator.iter_errors(order)]
        if errors:
            return errors
        return []

    def plan(
        self,
        portfolio_state: Mapping[str, Any],
        ips: Mapping[str, Any],
        facts: ReaderFacts
        | Mapping[str, Any]
        | Sequence[ReaderFacts | Mapping[str, Any]]
        | None = None,
        user_request: str | None = None,
        owner_instruction: str | None = None,
        conversation_history: Sequence[Mapping[str, Any]] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> PlanResult:
        """Execute a planning cycle given portfolio state, IPS, and sanitized facts.

        `owner_instruction` is the owner's chat message. It travels on its own labelled channel,
        never mixed into the Reader facts, and is untrusted text: the output is validated and
        every order still goes through the policy engine.
        """
        plain_state = _to_plain_dict(portfolio_state)
        plain_ips = _to_plain_dict(ips)

        # Normalize facts: only structured fields pass through, never raw instructions
        sanitized_facts: list[dict[str, Any]] = []
        if facts is not None:
            raw_list = facts if isinstance(facts, (list, tuple)) else [facts]
            for f in raw_list:
                item = _to_plain_dict(f)
                if isinstance(item, dict):
                    # Keep only schema-bound factual fields
                    sanitized_facts.append(
                        {
                            "source_type": item.get("source_type", "unknown"),
                            "summary": item.get("summary", ""),
                            "tokens_mentioned": item.get("tokens_mentioned", []),
                            "protocols_mentioned": item.get("protocols_mentioned", []),
                            "metrics": item.get("metrics", {}),
                            "sentiment": item.get("sentiment", "neutral"),
                            "instruction_detected": item.get("instruction_detected", False),
                            "flags": item.get("flags", []),
                        }
                    )

        # Extract universe allowlists for flagging
        universe = plain_ips.get("universe", {})
        allowed_tokens = set(universe.get("tokens", []))
        allowed_protocols = set(universe.get("protocols", []))
        allowed_functions = set(universe.get("functions", []))
        allowed_recipients = set(universe.get("recipients", []))

        # Build user prompt
        prompt_payload = {
            "portfolio_state": {
                "total_value_usd": plain_state.get("total_value_usd", "0"),
                "spent_24h_usd": plain_state.get("spent_24h_usd", "0"),
                "tx_count_1h": plain_state.get("tx_count_1h", 0),
                "positions": plain_state.get("positions", []),
            },
            "ips_summary": {
                "universe": universe,
                "allocation": plain_ips.get("allocation", {}),
                "limits": plain_ips.get("limits", {}),
            },
            "market_facts": sanitized_facts,
            "user_request": user_request or "Maintain target allocation within IPS limits.",
        }
        if owner_instruction is not None:
            # An owner request is judged by the policy engine, not by the model: the limits,
            # allocation weights, and limit counters are withheld so the model cannot refuse in
            # advance on its own (mis)reading of them. Rebalancing runs (no owner instruction)
            # still see them.
            prompt_payload["ips_summary"] = {"universe": universe}
            clean_positions = [
                {k: v for k, v in pos.items() if k != "weight_bps"}
                for pos in plain_state.get("positions", [])
            ]
            prompt_payload["portfolio_state"] = {
                "total_value_usd": plain_state.get("total_value_usd", "0"),
                "positions": clean_positions,
            }
            prompt_payload["user_request"] = user_request or "Fulfill owner instruction."
            prompt_payload["owner_instruction"] = {
                "channel": "owner_chat",
                "trust": "untrusted_text",
                "text": owner_instruction,
            }
            prompt_payload["conversation_history"] = format_conversation_history(
                conversation_history
            )
        elif conversation_history is not None:
            prompt_payload["conversation_history"] = format_conversation_history(
                conversation_history
            )

        user_content = json.dumps(prompt_payload, indent=2)

        if self.client is None:
            return PlanResult(
                orders=[],
                no_action={
                    "reason": "Planner has no LLMClient configured; defaulting to no_action",
                    "rules_hit": [],
                },
                warnings=[],
                validation_errors=[],
                raw_response="",
                intent="question",
            )

        system_prompt = render_system_prompt(sorted(allowed_protocols), ips=plain_ips)
        if on_token is not None and hasattr(self.client, "complete_stream"):
            extractor = ReplyExtractor()

            def _token_cb(chunk: str) -> None:
                piece = extractor.feed(chunk)
                if piece:
                    cleaned = _CONTROL_RE.sub(" ", piece)
                    if cleaned:
                        on_token(cleaned)

            raw_response = self.client.complete_stream(
                system=system_prompt,
                user=user_content,
                schema=PLANNER_OUTPUT_SCHEMA,
                on_token=_token_cb,
            )
        else:
            raw_response = self.client.complete(
                system=system_prompt,
                user=user_content,
                schema=PLANNER_OUTPUT_SCHEMA,
            )

        try:
            parsed = json.loads(raw_response)
        except Exception as exc:
            return PlanResult(
                orders=[],
                no_action={
                    "reason": f"Output rejected: malformed JSON from model ({exc})",
                    "rules_hit": [],
                },
                warnings=[],
                validation_errors=[f"JSONDecodeError: {exc}"],
                raw_response=raw_response,
                intent="question",
            )

        if not isinstance(parsed, dict):
            return PlanResult(
                orders=[],
                no_action={
                    "reason": "Output rejected: response is not a JSON object",
                    "rules_hit": [],
                },
                warnings=[],
                validation_errors=["Root JSON is not an object"],
                raw_response=raw_response,
                intent="question",
            )

        raw_intent = parsed.get("intent")
        action = parsed.get("action")
        reply = clean_reply(parsed.get("reply"))

        # Collect proposed candidate orders
        raw_orders: list[Any] = []
        if "order" in parsed and isinstance(parsed["order"], dict):
            raw_orders.append(parsed["order"])
        if "orders" in parsed and isinstance(parsed["orders"], list):
            raw_orders.extend(parsed["orders"])

        has_orders = bool(raw_orders)
        if isinstance(raw_intent, str) and raw_intent in VALID_INTENTS:
            intent = raw_intent
        else:
            intent = "action" if has_orders else "question"

        # If intent is clarify, question, or refusal, drop any orders returned and log
        if intent in ("clarify", "question", "refusal"):
            if has_orders:
                log.warning(
                    "Planner returned %d order(s) for non-action intent '%s'; dropping orders.",
                    len(raw_orders),
                    intent,
                )
            reason = str(
                parsed.get("reason")
                or (
                    parsed.get("no_action", {}).get("reason")
                    if isinstance(parsed.get("no_action"), dict)
                    else ""
                )
                or f"Intent is '{intent}'"
            )
            return PlanResult(
                orders=[],
                no_action={"reason": reason, "rules_hit": []},
                warnings=[],
                validation_errors=[],
                raw_response=raw_response,
                reply=reply,
                intent=intent,
            )

        # 1. Handle explicit no_action or missing orders
        if action == "no_action" or not raw_orders:
            reason = str(
                parsed.get("reason")
                or (
                    parsed.get("no_action", {}).get("reason")
                    if isinstance(parsed.get("no_action"), dict)
                    else ""
                )
                or "No action proposed by planner"
            )
            return PlanResult(
                orders=[],
                no_action={"reason": reason, "rules_hit": []},
                warnings=[],
                validation_errors=[],
                raw_response=raw_response,
                reply=reply,
                intent=intent,
            )

        valid_orders: list[dict[str, Any]] = []
        validation_errors: list[str] = []
        warnings: list[str] = []

        for candidate in raw_orders:
            # Strip null values that models sometimes output for optional omitted fields
            if isinstance(candidate, dict):
                candidate = {k: v for k, v in candidate.items() if v is not None}
            # The order id is bookkeeping, not an instruction: models often invent ids that are not
            # lowercase hex. Code assigns a fresh one; financial fields stay strictly validated.
            oid = candidate.get("order_id")
            if isinstance(oid, str) and not _UUID4_RE.match(oid):
                candidate["order_id"] = str(uuid.uuid4())
            # Validate strictly against order.schema.json (No repair guessing!)
            schema_errors = self.validate_order(candidate)
            if schema_errors:
                # Any schema validation failure is rejected immediately
                first_err = schema_errors[0]
                validation_errors.extend(schema_errors)
                return PlanResult(
                    orders=[],
                    no_action={
                        "reason": f"Output rejected: failed order schema validation ({first_err})",
                        "rules_hit": [],
                    },
                    warnings=[],
                    validation_errors=validation_errors,
                    raw_response=raw_response,
                    reply=reply,
                    intent=intent,
                )

            # If order is schema-valid, check whether it proposes items outside the IPS universe
            tok = candidate.get("token")
            tok_out = candidate.get("token_out")
            proto = candidate.get("protocol")
            func = candidate.get("function")
            recip = candidate.get("recipient")

            if tok and tok not in allowed_tokens:
                warnings.append(
                    f"Proposed token '{tok}' is outside IPS universe tokens: "
                    f"{sorted(allowed_tokens)}"
                )
            if tok_out and tok_out not in allowed_tokens:
                warnings.append(
                    f"Proposed token_out '{tok_out}' is outside IPS universe tokens: "
                    f"{sorted(allowed_tokens)}"
                )
            if proto and proto not in allowed_protocols:
                warnings.append(
                    f"Proposed protocol '{proto}' is outside IPS universe protocols: "
                    f"{sorted(allowed_protocols)}"
                )
            if func and func not in allowed_functions:
                warnings.append(
                    f"Proposed function '{func}' is outside IPS universe functions: "
                    f"{sorted(allowed_functions)}"
                )
            if recip and recip not in allowed_recipients:
                warnings.append(
                    f"Proposed recipient '{recip}' is outside IPS universe recipients: "
                    f"{sorted(allowed_recipients)}"
                )

            # The order remains a candidate order (policy engine decides!)
            valid_orders.append(candidate)

        if owner_instruction is not None and intent == "action" and valid_orders:
            amount_err = check_amounts_match_instruction(owner_instruction, valid_orders)
            if amount_err:
                return PlanResult(
                    orders=[],
                    no_action={"reason": amount_err, "rules_hit": []},
                    warnings=[amount_err],
                    validation_errors=[],
                    raw_response=raw_response,
                    reply=amount_err,
                    intent="clarify",
                )

        return PlanResult(
            orders=valid_orders,
            no_action=None,
            warnings=warnings,
            validation_errors=[],
            raw_response=raw_response,
            reply=reply,
            intent=intent,
        )
