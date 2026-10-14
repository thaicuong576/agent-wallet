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


def test_embedded_copies_byte_identical():
    def canonical(o):
        return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )

    dr = load(f"{C}/schemas/decision-record.schema.json")
    dr_defs = dr["$defs"]

    for schema_file, def_name in [
        ("order", "Order"),
        ("decision", "Decision"),
        ("execution-result", "ExecutionResult"),
    ]:
        schema = load(f"{C}/schemas/{schema_file}.schema.json")
        core = {k: v for k, v in schema.items() if not k.startswith("$")}
        assert canonical(core) == canonical(dr_defs[def_name]), f"{def_name} core mismatch"
        for d, val in schema.get("$defs", {}).items():
            assert canonical(val) == canonical(dr_defs[d]), f"{def_name} def {d} mismatch"


PREVIOUS_RULE_CODES = [
    "ACCESS.TOKEN_NOT_ALLOWED",
    "ACCESS.PROTOCOL_NOT_ALLOWED",
    "ACCESS.FUNCTION_NOT_ALLOWED",
    "ACCESS.RECIPIENT_NOT_ALLOWED",
    "ACCESS.UNLIMITED_APPROVAL",
    "LIMIT.PER_TX",
    "LIMIT.PER_DAY",
    "LIMIT.TX_PER_HOUR",
    "ALLOC.MAX_WEIGHT_PER_ASSET",
    "ALLOC.MIN_STABLE_WEIGHT",
    "ALLOC.WITHIN_REBALANCE_BAND",
    "RISK.POOL_TVL_TOO_LOW",
    "RISK.REWARD_APY_SHARE_TOO_HIGH",
    "RISK.APY_UNSTABLE",
    "RISK.DEPEG_TOO_WIDE",
    "RISK.UTILIZATION_TOO_HIGH",
    "RISK.SLIPPAGE_TOO_HIGH",
    "RISK.COST_EXCEEDS_BENEFIT",
    "RISK.DATA_STALE",
    "TIER.READ_ONLY",
    "TIER.PROPOSE_ONLY",
    "TIER.EMERGENCY_STOP",
    "TIER.EMERGENCY_STOP_RELEASED",
    "TIER.APPROVAL_REQUIRED",
    "TIER.APPROVAL_REJECTED",
]


def test_rule_codes_are_append_only():
    codes = [r["code"] for r in load(f"{C}/rule-codes.json")]
    assert codes[: len(PREVIOUS_RULE_CODES)] == PREVIOUS_RULE_CODES
    assert codes[len(PREVIOUS_RULE_CODES) :] == [
        "ACCESS.OWNER_ONLY_FUNCTION",
        "TIER.GLOBAL_STOP",
        "TIER.POLICY_CHANGED",
    ]


def test_no_mint_cap_in_schemas_or_rule_codes():
    rule_codes_text = json.dumps(load(f"{C}/rule-codes.json")).lower()
    assert "mint_per" not in rule_codes_text
    assert "mint_cap" not in rule_codes_text
    assert "mint_limit" not in rule_codes_text

    for name in NAMES:
        schema = load(f"{C}/schemas/{name}.schema.json")
        schema_text = json.dumps(schema).lower()
        assert "mint_per" not in schema_text
        assert "mint_cap" not in schema_text
        assert "mint_limit" not in schema_text


NEW_VALID_ORDER_EXAMPLES = [
    "order.valid.mint.json",
    "order.valid.transfer-usdc.json",
    "order.valid.transfer-eth.json",
]

NEW_INVALID_ORDER_EXAMPLES = [
    "order.invalid.mint-recipient-owner.json",
    "order.invalid.transfer-recipient-self.json",
    "order.invalid.eth-decimals.json",
    "order.invalid.mint-token-out.json",
]


@pytest.mark.parametrize("example_file", NEW_VALID_ORDER_EXAMPLES)
def test_new_order_valid_examples(example_file):
    validator = jsonschema.Draft202012Validator(load(f"{C}/schemas/order.schema.json"))
    dr_schema = load(f"{C}/schemas/decision-record.schema.json")
    embedded_order_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **dr_schema["$defs"]["Order"],
        "$defs": dr_schema["$defs"],
    }
    dr_validator = jsonschema.Draft202012Validator(embedded_order_schema)
    example = load(f"{C}/examples/{example_file}")
    assert not list(validator.iter_errors(example)), f"{example_file} failed order.schema.json"
    assert not list(dr_validator.iter_errors(example)), (
        f"{example_file} failed embedded Order schema"
    )


@pytest.mark.parametrize("example_file", NEW_INVALID_ORDER_EXAMPLES)
def test_new_order_invalid_examples(example_file):
    validator = jsonschema.Draft202012Validator(load(f"{C}/schemas/order.schema.json"))
    dr_schema = load(f"{C}/schemas/decision-record.schema.json")
    embedded_order_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **dr_schema["$defs"]["Order"],
        "$defs": dr_schema["$defs"],
    }
    dr_validator = jsonschema.Draft202012Validator(embedded_order_schema)
    example = load(f"{C}/examples/{example_file}")
    assert list(validator.iter_errors(example)), (
        f"{example_file} unexpectedly passed order.schema.json"
    )
    assert list(dr_validator.iter_errors(example)), (
        f"{example_file} unexpectedly passed embedded Order schema"
    )
