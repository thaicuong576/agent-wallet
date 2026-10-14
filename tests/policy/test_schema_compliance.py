"""Tests verifying output decisions strictly validate against decision.schema.json."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import jsonschema

from core.policy.engine import evaluate
from core.policy.ips import LoadedIps


def test_schema_compliance_allow_decision(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    decision_validator: jsonschema.Draft202012Validator,
) -> None:
    """Allowed decision validates without schema errors."""
    decision = evaluate(loaded_ips, valid_state, valid_order)
    errors = list(decision_validator.iter_errors(decision))
    assert not errors, f"Decision schema errors: {errors}"
    assert decision["decision"] == "allow"
    assert decision["rules_hit"] == []


def test_schema_compliance_single_deny_decision(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    decision_validator: jsonschema.Draft202012Validator,
) -> None:
    """Denied decision with single rule hit validates without schema errors."""
    order = copy.deepcopy(valid_order)
    order["amount"] = {"value": "150000000", "decimals": 6}

    decision = evaluate(loaded_ips, valid_state, order)
    errors = list(decision_validator.iter_errors(decision))
    assert not errors, f"Decision schema errors: {errors}"
    assert decision["decision"] == "deny"
    assert len(decision["rules_hit"]) == 1


def test_schema_compliance_multiple_deny_decision(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    decision_validator: jsonschema.Draft202012Validator,
) -> None:
    """Denied decision with multiple rule hits validates without schema errors."""
    order = copy.deepcopy(valid_order)
    order["token"] = "DISALLOWED_TOKEN"
    order["protocol"] = "disallowed-protocol"
    order["function"] = "disallowedFunction"

    decision = evaluate(loaded_ips, valid_state, order)
    errors = list(decision_validator.iter_errors(decision))
    assert not errors, f"Decision schema errors: {errors}"
    assert decision["decision"] == "deny"
    assert len(decision["rules_hit"]) >= 3


def test_schema_compliance_with_plain_dict_ips(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    decision_validator: jsonschema.Draft202012Validator,
) -> None:
    """Evaluate works and complies with schema when IPS is passed as plain dict."""
    plain_ips = dict(loaded_ips.ips)
    decision = evaluate(plain_ips, valid_state, valid_order)
    errors = list(decision_validator.iter_errors(decision))
    assert not errors, f"Decision schema errors: {errors}"
    assert decision["ips_hash"] == loaded_ips.hash


def test_schema_compliance_timestamp_formatting(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    decision_validator: jsonschema.Draft202012Validator,
) -> None:
    """Decision timestamp formats valid RFC 3339 UTC timestamp with trailing Z."""
    now = datetime(2026, 10, 16, 12, 30, 45, tzinfo=UTC)
    decision = evaluate(loaded_ips, valid_state, valid_order, now=now)
    assert decision["decided_at"] == "2026-10-16T12:30:45Z"

    # Also test naive datetime
    naive_now = datetime(2026, 10, 16, 12, 30, 45)
    decision_naive = evaluate(loaded_ips, valid_state, valid_order, now=naive_now)
    assert decision_naive["decided_at"] == "2026-10-16T12:30:45Z"

    errors = list(decision_validator.iter_errors(decision))
    assert not errors


def test_schema_compliance_reason_length_limit(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    decision_validator: jsonschema.Draft202012Validator,
) -> None:
    """Reason string is capped within schema limit (500 chars)."""
    order = copy.deepcopy(valid_order)
    order["token"] = "A" * 100
    order["protocol"] = "B" * 100
    order["function"] = "C" * 100
    order["recipient"] = "D" * 100

    decision = evaluate(loaded_ips, valid_state, order)
    assert len(decision["reason"]) <= 500
    errors = list(decision_validator.iter_errors(decision))
    assert not errors
