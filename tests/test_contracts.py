"""Contract examples validate (or fail) against their schemas; the audit chain recomputes."""

import glob
import hashlib
import json
import os
import re

import jsonschema
import pytest
import yaml

C = os.path.join(os.path.dirname(os.path.dirname(__file__)), "contracts")
NAMES = [
    "order",
    "portfolio-state",
    "risk-inputs",
    "ips",
    "decision",
    "execution-result",
    "decision-record",
    "tier-state",
]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("name", NAMES)
def test_schema_is_valid_and_versioned(name):
    schema = load(f"{C}/schemas/{name}.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    assert "schema_version" in json.dumps(schema)


@pytest.mark.parametrize("name", NAMES)
def test_examples_validate_or_fail_as_named(name):
    validator = jsonschema.Draft202012Validator(load(f"{C}/schemas/{name}.schema.json"))
    valid = glob.glob(f"{C}/examples/{name}.valid*.json")
    invalid = glob.glob(f"{C}/examples/{name}.invalid*.json")
    assert valid, f"no valid example for {name}"
    assert invalid, f"no invalid example for {name}"
    for path in valid:
        assert not list(validator.iter_errors(load(path))), path
    for path in invalid:
        assert list(validator.iter_errors(load(path))), path


def test_rule_codes_are_unique_and_well_formed():
    codes = [r["code"] for r in load(f"{C}/rule-codes.json")]
    assert len(codes) == len(set(codes))
    assert all(re.fullmatch(r"[A-Z]+\.[A-Z0-9_]+", c) for c in codes)


def test_openapi_loads_and_has_core_paths():
    with open(f"{C}/openapi.yaml", encoding="utf-8") as f:
        api = yaml.safe_load(f)
    for path in ("/orders", "/decisions", "/portfolio", "/tier", "/emergency-stop", "/ips"):
        assert path in api["paths"], path


def test_audit_chain_example_recomputes():
    chain = load(f"{C}/examples/decision-record.chain.json")
    assert len(chain) >= 3
    prev = "0" * 64
    for i, record in enumerate(chain):
        assert record["prev_hash"] == prev, i
        body = {k: v for k, v in record.items() if k != "hash"}
        canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert record["hash"] == hashlib.sha256(canon.encode("utf-8")).hexdigest(), i
        prev = record["hash"]
