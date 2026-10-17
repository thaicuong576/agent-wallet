"""Strict schema validation for planner orders."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Final

import jsonschema
import jsonschema.protocols
import jsonschema.validators

ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_ORDER_SCHEMA_PATH = ROOT / "contracts" / "schemas" / "order.schema.json"

PLANNER_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {"approve", "supply", "withdraw", "exactInputSingle"}
)


class OrderValidationError(ValueError):
    """Raised when an order fails schema validation."""


@functools.lru_cache(maxsize=4)
def _get_strict_order_validator(schema_path_str: str) -> jsonschema.protocols.Validator:
    path = Path(schema_path_str)
    if not path.is_file():
        raise FileNotFoundError(f"Order schema file not found: {path}")
    with open(path, encoding="utf-8") as f:
        schema = json.load(f)
    type_checker = jsonschema.Draft202012Validator.TYPE_CHECKER.redefine(
        "integer",
        lambda _checker, instance: isinstance(instance, int) and not isinstance(instance, bool),
    )
    strict_cls = jsonschema.validators.extend(
        jsonschema.Draft202012Validator,
        type_checker=type_checker,
    )
    return strict_cls(schema)
