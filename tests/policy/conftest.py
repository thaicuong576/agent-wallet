"""Fixtures for policy engine tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from core.policy.ips import LoadedIps, load_ips

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"


@pytest.fixture
def loaded_ips() -> LoadedIps:
    """Load canonical valid IPS fixture."""
    return load_ips(EXAMPLES_DIR / "ips.valid.json")


@pytest.fixture
def valid_state() -> dict[str, Any]:
    """Load canonical valid portfolio state fixture."""
    with open(EXAMPLES_DIR / "portfolio-state.valid.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def valid_order() -> dict[str, Any]:
    """Load canonical valid order fixture (supply 80 USDC to Aave)."""
    with open(EXAMPLES_DIR / "order.valid.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def decision_validator() -> jsonschema.Draft202012Validator:
    """Validator for Decision schema."""
    with open(SCHEMAS_DIR / "decision.schema.json", encoding="utf-8") as f:
        schema = json.load(f)
    return jsonschema.Draft202012Validator(schema)
