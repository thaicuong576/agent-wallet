"""Runner: one cycle of Reader, Planner, portfolio read and gateway proposals.

The runner proposes; the gateway decides. It holds no key, has no signer, no tier mutator and no
audit write path: every record it returns was appended by the gateway.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from core.agent.planner import Planner
from core.agent.reader import Reader
from core.gateway.gateway import Gateway
from core.policy.ips import LoadedIps
from core.runner.portfolio import PortfolioUnavailable, WalletSnapshot

REASON_MAX_LEN = 500


class PortfolioReaderLike(Protocol):
    """Anything that can produce a WalletSnapshot as of `now`."""

    def read(self, now: datetime) -> WalletSnapshot: ...


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
