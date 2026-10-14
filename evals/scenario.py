"""Scenario and ScenarioResult data definitions for the evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Scenario:
    """A single evaluation scenario definition."""

    id: str
    category: str
    description: str
    inputs: dict[str, Any]
    expected_outcome: str
    expected_rule_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert scenario definition to dictionary."""
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "inputs": self.inputs,
            "expected_outcome": self.expected_outcome,
            "expected_rule_codes": list(self.expected_rule_codes),
        }


@dataclass
class ScenarioResult:
    """Execution result of a single evaluation scenario."""

    scenario_id: str
    category: str
    description: str
    passed: bool
    expected_outcome: str
    actual_outcome: str
    expected_rule_codes: list[str]
    actual_rule_codes: list[str]
    details: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert scenario result to dictionary for JSON reports."""
        return {
            "scenario_id": self.scenario_id,
            "category": self.category,
            "description": self.description,
            "passed": self.passed,
            "expected_outcome": self.expected_outcome,
            "actual_outcome": self.actual_outcome,
            "expected_rule_codes": list(self.expected_rule_codes),
            "actual_rule_codes": list(self.actual_rule_codes),
            "details": self.details,
            "error": self.error,
        }
