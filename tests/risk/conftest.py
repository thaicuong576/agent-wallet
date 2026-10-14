"""Fixtures for risk provider tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import eth_abi
import jsonschema
import pytest

from core.risk.aave import RESERVE_DATA_TYPES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"


@pytest.fixture
def risk_inputs_schema() -> dict[str, Any]:
    """Load canonical risk-inputs.schema.json."""
    with open(SCHEMAS_DIR / "risk-inputs.schema.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def risk_inputs_validator(risk_inputs_schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    """Validator for RiskInputs contract."""
    return jsonschema.Draft202012Validator(risk_inputs_schema)


@pytest.fixture
def valid_risk_inputs_fixture_path() -> Path:
    """Path to contracts/examples/risk-inputs.valid.json."""
    return EXAMPLES_DIR / "risk-inputs.valid.json"


def encode_aave_reserve_data(
    total_atoken: int = 120_000_000_000_000,  # 120M USDC (6 decimals)
    total_stable_debt: int = 10_000_000_000_000,  # 10M USDC
    total_variable_debt: int = 78_800_000_000_000,  # 78.8M USDC (sum = 88.8M, 74% util)
    last_update_timestamp: int = 1792141110,  # UTC timestamp
    unbacked: int = 0,
    accrued_to_treasury: int = 0,
    liquidity_rate: int = 0,
    variable_borrow_rate: int = 0,
    stable_borrow_rate: int = 0,
    average_stable_borrow_rate: int = 0,
    liquidity_index: int = 10**27,
    variable_borrow_index: int = 10**27,
) -> str:
    """Encode 12 fields of AaveProtocolDataProvider.getReserveData."""
    vals = [
        unbacked,
        accrued_to_treasury,
        total_atoken,
        total_stable_debt,
        total_variable_debt,
        liquidity_rate,
        variable_borrow_rate,
        stable_borrow_rate,
        average_stable_borrow_rate,
        liquidity_index,
        variable_borrow_index,
        last_update_timestamp,
    ]
    encoded = eth_abi.encode(RESERVE_DATA_TYPES, vals)
    return "0x" + encoded.hex()
