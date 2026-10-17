"""Runner: one cycle of Reader, Planner, portfolio read and gateway proposals.

The runner proposes; the gateway decides. It holds no key, has no signer, no tier mutator and no
audit write path: every record it returns was appended by the gateway.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from core.agent.planner import Planner
from core.agent.reader import Reader
from core.gateway.gateway import Gateway
from core.policy.ips import LoadedIps
from core.runner.portfolio import PortfolioUnavailable, WalletSnapshot

timing_log = logging.getLogger("agent_wallet.timing")

REASON_MAX_LEN = 500
MAX_INSTRUCTION_ORDERS = 4
SKIP_PREFIX = "Skipped"


@dataclass
class InstructionResult:
    """What one owner instruction produced. Every record was appended by the gateway."""

    records: list[dict[str, Any]] = field(default_factory=list)
    reply: str | None = None
    plan_reason: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    proposed: int = 0
    skipped: int = 0
    intent: str = "action"


class PortfolioReaderLike(Protocol):
    """Anything that can produce a WalletSnapshot as of `now`."""

    def read(self, now: datetime) -> WalletSnapshot: ...


def _amount_text(order: dict[str, Any]) -> str:
    amount = order.get("amount") or {}
    try:
        units, decimals = int(amount["value"]), int(amount["decimals"])
    except (KeyError, TypeError, ValueError):
        return "?"
    whole, frac = divmod(units, 10**decimals)
    text = f"{whole}.{frac:0{decimals}d}".rstrip("0").rstrip(".") if decimals else str(whole)
    return f"{text} {order.get('token', '')}".strip()


def _iso(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


class Runner:
    """Composes Reader, Planner, portfolio reader and gateway for one decision cycle."""

    def __init__(
        self,
        reader: Reader,
        planner: Planner,
        portfolio: PortfolioReaderLike,
        gateway: Gateway,
        ips: LoadedIps,
        clock: Callable[[], datetime],
    ) -> None:
        self.reader = reader
        self.planner = planner
        self.portfolio = portfolio
        self.gateway = gateway
        self.ips = ips
        self.clock = clock

    def _no_action(self, reason: str, now: datetime) -> dict[str, Any]:
        return self.gateway.record_no_action(reason[:REASON_MAX_LEN], _iso(now))

    def run_once(self, untrusted_text: str, now: datetime) -> list[dict[str, Any]]:
        """Run one cycle and return the decision records the gateway appended.

        GatewayBusy and ReconcileRequired propagate; earlier records of the cycle are already in
        the audit log.
        """
        try:
            snapshot = self.portfolio.read(now)
        except PortfolioUnavailable:
            return [self._no_action("portfolio unavailable", now)]

        facts = self.reader.read(untrusted_text)
        plan = self.planner.plan(snapshot.state, self.ips, facts)

        if plan.is_no_action:
            reason = str((plan.no_action or {}).get("reason") or "no action")
            if plan.validation_errors:
                reason = f"{reason}; " + "; ".join(plan.validation_errors)
            return [self._no_action(reason, now)]

        records: list[dict[str, Any]] = []
        for index, order in enumerate(plan.orders):
            if index > 0:
                try:
                    self.portfolio.read(now)
                except PortfolioUnavailable:
                    records.append(self._no_action("portfolio unavailable", now))
                    break
            record = self.gateway.propose(order)
            records.append(record)
            if record.get("outcome") == "failed":
                break
        return records

    def run_instruction(
        self,
        instruction: str,
        now: datetime,
        conversation_history: Sequence[Mapping[str, Any]] | None = None,
    ) -> InstructionResult:
        """One owner chat instruction: plan, then propose the orders one at a time.

        The instruction reaches the planner on its own labelled channel (not as Reader facts) and
        the planner's orders go through the gateway with `origin="planner"`, so mint and transfer
        stay impossible. Execution stops at the first order that is not `executed`; each order
        left over is recorded as a skipped step with the reason.
        """
        result = InstructionResult()
        t0 = time.perf_counter()
        try:
            snapshot = self.portfolio.read(now)
        except PortfolioUnavailable:
            timing_log.info(
                "phase=portfolio_read elapsed=%.4fs status=failed",
                time.perf_counter() - t0,
            )
            result.records.append(self._no_action("portfolio unavailable", now))
            result.plan_reason = "portfolio unavailable"
            result.intent = "question"
            return result
        timing_log.info(
            "phase=portfolio_read elapsed=%.4fs status=ok",
            time.perf_counter() - t0,
        )

        t0 = time.perf_counter()
        plan = self.planner.plan(
            snapshot.state,
            self.ips,
            None,
            owner_instruction=instruction,
            conversation_history=conversation_history,
        )
        timing_log.info("phase=planner elapsed=%.4fs", time.perf_counter() - t0)

        result.reply = getattr(plan, "reply", None)
        result.warnings = list(plan.warnings)
        result.validation_errors = list(plan.validation_errors)
        result.intent = getattr(plan, "intent", "action" if plan.orders else "question")
        if plan.is_no_action:
            result.plan_reason = str((plan.no_action or {}).get("reason") or "no action")
            result.records.append(
                self._no_action("Chat request: the planner proposed no order.", now)
            )
            return result
        orders = list(plan.orders)
        if len(orders) > MAX_INSTRUCTION_ORDERS:
            result.validation_errors.append(
                f"the plan has {len(orders)} orders, at most {MAX_INSTRUCTION_ORDERS} are accepted"
            )
            result.records.append(self._no_action("Chat request: the plan was too long.", now))
            return result

        result.proposed = len(orders)
        blocker: str | None = None
        for order in orders:
            if blocker is not None:
                what = f"{order.get('function', 'step')} ({_amount_text(order)})"
                reason = f"{SKIP_PREFIX} {what}: {blocker}"
                result.records.append(self._no_action(reason, now))
                result.skipped += 1
                continue
            if result.records:
                t0 = time.perf_counter()
                try:
                    self.portfolio.read(now)
                except PortfolioUnavailable:
                    timing_log.info(
                        "phase=portfolio_read elapsed=%.4fs status=failed",
                        time.perf_counter() - t0,
                    )
                    blocker = "the portfolio could not be read."
                    what = f"{order.get('function', 'step')} ({_amount_text(order)})"
                    result.records.append(self._no_action(f"{SKIP_PREFIX} {what}: {blocker}", now))
                    result.skipped += 1
                    continue
                timing_log.info(
                    "phase=portfolio_read elapsed=%.4fs status=ok",
                    time.perf_counter() - t0,
                )
            t0 = time.perf_counter()
            record = self.gateway.propose(order)
            timing_log.info(
                "phase=gateway order_id=%s elapsed=%.4fs outcome=%s",
                order.get("order_id"),
                time.perf_counter() - t0,
                record.get("outcome"),
            )
            result.records.append(record)
            outcome = str(record.get("outcome"))
            if outcome != "executed":
                words = {
                    "denied": "the previous step was blocked.",
                    "failed": "the previous step failed.",
                    "pending_approval": "the previous step is waiting for your approval.",
                }
                blocker = words.get(outcome, "the previous step did not go through.")
        return result
