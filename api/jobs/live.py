"""In-process live job tracking and cancellation gate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.runner.runner import SKIP_PREFIX

from .constants import _SKIP_TAIL, CANCEL_REASON
from .outcome import _now_iso, _order_label


class _CancelGate:
    """The tenant's gateway as the job's runner sees it, with the owner's cancel applied.

    Cancellation points are exactly the calls to `propose`: once the owner has cancelled, no
    further order reaches the real gateway (so nothing is checked, simulated or signed), and each
    one is recorded as a skipped step with reason `owner-cancelled`. An order already inside
    `propose` is never interrupted. Everything else is delegated untouched.
    """

    def __init__(
        self,
        gateway: Any,
        cancelled: Callable[[], bool],
        on_order: Callable[[Any], None],
        on_skip: Callable[[], None],
        clock: Callable[[], datetime],
    ) -> None:
        self._gateway = gateway
        self._cancelled = cancelled
        self._on_order = on_order
        self._on_skip = on_skip
        self._clock = clock

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gateway, name)

    def propose(self, order: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if self._cancelled():
            self._on_skip()
            reason = f"{SKIP_PREFIX} {_order_label(order)}: {CANCEL_REASON}"
            return self._gateway.record_no_action(reason, _now_iso(self._clock()))
        self._on_order(order.get("order_id"))
        return self._gateway.propose(order, **kwargs)

    def record_no_action(self, reason: str, now: str) -> dict[str, Any]:
        # The runner words the steps after a blocked one itself; after a cancel they say why.
        if self._cancelled():
            reason = _SKIP_TAIL.sub(lambda m: m.group(1) + ": " + CANCEL_REASON, reason)
        return self._gateway.record_no_action(reason, now)


@dataclass
class _Live:
    """In-process state of one job that is queued or running (never stored, never an order body)."""

    cancel: bool = False
    skipped_by_cancel: bool = False
    order_id: str | None = None
    tokens: list[str] = field(default_factory=list)
