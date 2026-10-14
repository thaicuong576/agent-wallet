"""Offline fixture risk provider for deterministic replay (the judges' mode)."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import jsonschema

from core.risk.provider import (
    RiskDataUnavailableError,
    RiskProviderError,
    UnsupportedProtocolError,
    UnsupportedTokenError,
)

SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent
    / "contracts"
    / "schemas"
    / "risk-inputs.schema.json"
)

with open(SCHEMA_PATH, encoding="utf-8") as _f:
    RISK_INPUTS_SCHEMA: Final[dict[str, Any]] = json.load(_f)

RISK_INPUTS_VALIDATOR: Final[jsonschema.Draft202012Validator] = jsonschema.Draft202012Validator(
    RISK_INPUTS_SCHEMA
)


class FixtureRiskProvider:
    """Offline risk provider that replays a fixture file without restamping.

    This provider is used for deterministic evaluations and judges' replay mode.
    It loads a static JSON file conforming to risk-inputs.schema.json and returns
    the exact data as-is: 'as_of' and 'data_age_seconds' are never restamped or
    reset to zero, so stale fixtures stay stale.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise RiskDataUnavailableError(f"Fixture file not found: {self.path}")

        try:
            with open(self.path, encoding="utf-8") as f:
                self._data: dict[str, Any] = json.load(f)
        except Exception as exc:
            raise RiskDataUnavailableError(
                f"Failed to read or parse fixture JSON from {self.path}: {exc}"
            ) from exc

        errors = list(RISK_INPUTS_VALIDATOR.iter_errors(self._data))
        if errors:
            raise RiskProviderError(
                f"Fixture at {self.path} fails risk-inputs schema: {errors[0].message}"
            )

    @property
    def fixture(self) -> dict[str, Any]:
        """Return a copy of the loaded fixture data."""
        return copy.deepcopy(self._data)

    def get(
        self,
        protocol: str,
        token: str,
        order_usd_amount: Decimal | str | int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Replay fixture data deterministically without restamping.

        Args:
            protocol: Expected protocol matching fixture ('aave-v3', etc.).
            token: Expected token matching fixture ('USDC', etc.).
            order_usd_amount: Optional order amount (ignored in replay mode).
            now: Optional current timestamp (ignored; fixture is not restamped).

        Returns:
            Exact copy of the fixture dictionary.
        """
        expected_protocol = self._data.get("protocol")
        if expected_protocol and protocol != expected_protocol:
            raise UnsupportedProtocolError(
                f"Fixture is for protocol '{expected_protocol}', but requested '{protocol}'"
            )

        expected_token = self._data.get("token")
        if expected_token and token != expected_token:
            raise UnsupportedTokenError(
                f"Fixture is for token '{expected_token}', but requested '{token}'"
            )

        # Return deep copy so callers cannot mutate the internal fixture state
        return copy.deepcopy(self._data)
