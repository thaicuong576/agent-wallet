"""Planning cycle results and dictionary conversion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.agent.reader import ReaderFacts


@dataclass
class PlanResult:
    """Result of a planning cycle."""

    orders: list[dict[str, Any]] = field(default_factory=list)
    no_action: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    raw_response: str = ""
    reply: str | None = None
    intent: str | None = None

    def __post_init__(self) -> None:
        if self.intent is None:
            self.intent = "action" if self.orders else "question"

    @property
    def is_no_action(self) -> bool:
        """True if the plan resulted in no action."""
        return self.no_action is not None or len(self.orders) == 0

    def __getitem__(self, key: str) -> Any:
        if key == "orders":
            return self.orders
        if key == "no_action":
            return self.no_action
        if key == "warnings":
            return self.warnings
        if key == "validation_errors":
            return self.validation_errors
        if key == "intent":
            return self.intent
        raise KeyError(key)


def _to_plain_dict(obj: Any) -> Any:
    if hasattr(obj, "ips") and isinstance(obj.ips, Mapping):
        return _to_plain_dict(obj.ips)
    if isinstance(obj, ReaderFacts):
        return obj.to_dict()
    if isinstance(obj, Mapping):
        return {k: _to_plain_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain_dict(item) for item in obj]
    return obj
