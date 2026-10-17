"""JSON schemas and validator loading."""

from __future__ import annotations

import functools
import json
import pathlib
import re

import jsonschema

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT_DIR / "contracts" / "schemas"
EXAMPLES_DIR = ROOT_DIR / "contracts" / "examples"

UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

PROBLEM_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["type", "title", "status"],
    "additionalProperties": True,
    "properties": {
        "type": {"type": "string", "pattern": r"^urn:agent-wallet:problem:[a-z-]+$"},
        "title": {"type": "string"},
        "status": {"type": "integer", "minimum": 400, "maximum": 599},
        "detail": {"type": "string", "maxLength": 500},
        "instance": {"type": "string"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "message"],
                "properties": {
                    "path": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
    },
}

DECISIONS_RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "items", "next_cursor"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "items": {"type": "array"},
        "next_cursor": {"type": ["string", "null"]},
    },
}

IPS_RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "ips", "ips_hash"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "ips": {"type": "object"},
        "ips_hash": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    },
}

EVAL_REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "generated_at", "metrics", "scenarios"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "generated_at": {
            "type": "string",
            "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$",
        },
        "metrics": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        },
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "layer", "passed"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": r"^(AM|SEC|CMP)-[0-9]{2}$"},
                    "layer": {"type": "string", "enum": ["A", "B", "C"]},
                    "passed": {"type": "boolean"},
                    "rule_codes": {
                        "type": "array",
                        "items": {"type": "string", "pattern": r"^[A-Z]+\.[A-Z0-9_]+$"},
                    },
                    "tx_hash": {
                        "type": ["string", "null"],
                        "pattern": r"^0x[0-9a-f]{64}$",
                    },
                },
            },
        },
    },
}


@functools.lru_cache(maxsize=16)
def get_validator(schema_name: str) -> jsonschema.Draft202012Validator:
    """Load and cache Draft 2020-12 schema validator."""
    if schema_name == "problem":
        return jsonschema.Draft202012Validator(PROBLEM_SCHEMA)
    if schema_name == "decisions-response":
        return jsonschema.Draft202012Validator(DECISIONS_RESPONSE_SCHEMA)
    if schema_name == "ips-response":
        return jsonschema.Draft202012Validator(IPS_RESPONSE_SCHEMA)
    if schema_name == "eval-report":
        return jsonschema.Draft202012Validator(EVAL_REPORT_SCHEMA)

    schema_path = SCHEMAS_DIR / f"{schema_name}.schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(data)


__all__ = [
    "DECISIONS_RESPONSE_SCHEMA",
    "EVAL_REPORT_SCHEMA",
    "EXAMPLES_DIR",
    "IPS_RESPONSE_SCHEMA",
    "PROBLEM_SCHEMA",
    "ROOT_DIR",
    "SCHEMAS_DIR",
    "UUID_REGEX",
    "get_validator",
]
