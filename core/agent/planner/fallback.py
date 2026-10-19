"""Deterministic fallback planner when LLM is unavailable."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from .planner import Planner
from .result import PlanResult, _to_plain_dict

_OWNER_ONLY_WORDS = re.compile(r"\b(withdraw|send|transfer|mint)\b")
_DEPOSIT_WORDS = re.compile(r"\b(put|supply|deposit|invest|lend|earn|stake|work)\b")


class FallbackPlanner:
    """Deterministic, rule-based planner used when the LLM is unavailable.

    It reads only the portfolio state and the IPS per-transaction limit, never Reader facts, so
    untrusted text cannot steer it. If idle USDC in the wallet is above a floor it proposes
    `approve` then `supply`, capped at the IPS per-tx limit; otherwise `no_action`. Every order
    still goes through the gateway like any other. The label is `fallback`.
    """

    client_label: Final = "fallback"
    IDLE_FLOOR_UNITS: Final = 10_000_000  # 10 USDC (6 decimals)

    def __init__(self, schema_path: str | Path | None = None) -> None:
        self._planner = Planner(None, schema_path)

    def _order(self, function: str, recipient: str, units: int, state: Mapping[str, Any]) -> dict:
        digest = hashlib.sha256(
            f"fallback|{state.get('as_of')}|{function}|{units}".encode()
        ).hexdigest()
        raw = bytearray(bytes.fromhex(digest[:32]))
        raw[6] = (raw[6] & 0x0F) | 0x40  # version 4 layout, lowercase uuid
        raw[8] = (raw[8] & 0x3F) | 0x80
        return {
            "schema_version": 1,
            "order_id": str(uuid.UUID(bytes=bytes(raw))),
            "created_at": str(state.get("as_of")),
            "proposed_by": "planner",
            "protocol": "aave-v3",
            "function": function,
            "token": "USDC",
            "amount": {"value": str(units), "decimals": 6},
            "recipient": recipient,
        }

    def _no_action(
        self, reason: str, reply: str | None = None, intent: str = "question"
    ) -> PlanResult:
        return PlanResult(
            no_action={"reason": reason, "rules_hit": []},
            reply=reply,
            intent=intent,
        )

    def plan(
        self,
        state: Mapping[str, Any],
        ips: Any,
        facts: Any = None,
        user_request: str | None = None,
        owner_instruction: str | None = None,
        conversation_history: Sequence[Mapping[str, Any]] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> PlanResult:
        plain_state = _to_plain_dict(state)
        plain_ips = _to_plain_dict(ips)
        if owner_instruction is not None:
            return self._chat(plain_state, plain_ips, state, owner_instruction)
        idle = self._idle_units(plain_state)
        if idle < self.IDLE_FLOOR_UNITS:
            return self._no_action(
                "Rule-based fallback: idle USDC is below the deposit floor.",
                intent="action",
            )
        try:
            cap = int(Decimal(str(plain_ips["limits"]["per_tx_usd"])) * 10**6)
        except (KeyError, InvalidOperation, TypeError):
            return self._no_action(
                "Rule-based fallback: the IPS has no per-transaction limit.",
                intent="action",
            )
        units = min(idle, cap)
        if units < self.IDLE_FLOOR_UNITS:
            return self._no_action(
                "Rule-based fallback: the per-transaction limit is too small.",
                intent="action",
            )
        return self._deposit(units, state)

    @staticmethod
    def _idle_units(plain_state: Mapping[str, Any]) -> int:
        idle = 0
        for pos in plain_state.get("positions", []):
            if pos.get("token") == "USDC" and pos.get("location") == "wallet":
                idle += int(pos["balance"]["value"])
        return idle

    def _deposit(self, units: int, state: Mapping[str, Any]) -> PlanResult:
        orders = [
            self._order("approve", "aave_pool", units, state),
            self._order("supply", "self", units, state),
        ]
        for order in orders:
            errors = self._planner.validate_order(order)
            if errors:
                return PlanResult(
                    no_action={"reason": f"Rule-based fallback rejected: {errors[0]}"},
                    validation_errors=errors,
                    intent="refusal",
                )
        return PlanResult(orders=orders, intent="action")

    @staticmethod
    def _usdc(units: int) -> str:
        text = f"{Decimal(units) / Decimal(10**6):f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    def _chat(
        self,
        plain_state: Mapping[str, Any],
        plain_ips: Mapping[str, Any],
        state: Mapping[str, Any],
        text: str,
    ) -> PlanResult:
        """Keyword reading of the owner's message. It can only deposit idle USDC into Aave (or
        answer); everything else is declined in words. Orders still go through the gateway."""
        lowered = text.lower()
        idle = self._idle_units(plain_state)
        in_aave = 0
        for pos in plain_state.get("positions", []):
            if pos.get("token") == "USDC" and pos.get("location") == "aave-v3":
                in_aave += int(pos["balance"]["value"])
        summary = f"Wallet: {self._usdc(idle)} USDC idle, {self._usdc(in_aave)} USDC in Aave."
        if _OWNER_ONLY_WORDS.search(lowered):
            return self._no_action(
                "Rule-based fallback: that is an owner action, not a chat action.",
                reply="I cannot do that from chat. Minting and withdrawing need your own "
                "signature, so use the Withdraw control. " + summary,
                intent="refusal",
            )
        if not _DEPOSIT_WORDS.search(lowered):
            return self._no_action(
                "Rule-based fallback: the message asks for information only.",
                reply=summary,
                intent="question",
            )
        match = re.search(r"(\d+(?:\.\d{1,6})?)\s*(?:usdc|usd|dollars?)\b", lowered)
        if match:
            units = int(Decimal(match.group(1)) * 10**6)
        else:
            units = idle
            try:
                units = min(idle, int(Decimal(str(plain_ips["limits"]["per_tx_usd"])) * 10**6))
            except (KeyError, InvalidOperation, TypeError):
                pass
        if units <= 0:
            return self._no_action(
                "Rule-based fallback: there is no idle USDC to put to work.",
                reply="There is no idle USDC in the wallet to put to work. " + summary,
                intent="refusal",
            )
        if units > idle:
            return self._no_action(
                "Rule-based fallback: not enough idle USDC.",
                reply=f"You asked for {self._usdc(units)} USDC but only {self._usdc(idle)} USDC "
                "is idle in the wallet.",
                intent="refusal",
            )
        result = self._deposit(units, state)
        if not result.is_no_action:
            result.reply = (
                f"Putting {self._usdc(units)} USDC to work in Aave: approve, then supply."
            )
        return result
