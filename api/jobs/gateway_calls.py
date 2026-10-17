"""Helpers for gateway invocation, order validation, and LLM setup."""

from __future__ import annotations

import inspect
import json
import os
import uuid
from functools import lru_cache
from typing import Any

import jsonschema

from core.agent.client import LLMClient, OpenAIClient

from .constants import ORDER_SCHEMA


@lru_cache(maxsize=1)
def _order_validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads(ORDER_SCHEMA.read_text(encoding="utf-8")))


def _hostile_plan() -> str:
    """What a compromised LLM might emit: a schema-valid supply far above the per-tx limit."""
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-01-01T00:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "1000000000000", "decimals": 6},
        "recipient": "self",
    }
    return json.dumps({"action": "propose", "orders": [order]})


def propose_with_origin(gateway: Any, order: dict[str, Any], *, origin: str) -> dict[str, Any]:
    """Call `Gateway.propose`, passing `origin` when the gateway accepts it.

    If the gateway does not accept `origin`, the call falls back to `propose(order)`, where the
    engine treats the order as planner-origin and denies owner functions: fail closed.
    """
    if "origin" in inspect.signature(gateway.propose).parameters:
        return gateway.propose(order, origin=origin)
    return gateway.propose(order)


def propose_as_owner(gateway: Any, order: dict[str, Any]) -> dict[str, Any]:
    return propose_with_origin(gateway, order, origin="owner")


def _llm_from_env() -> LLMClient | None:
    return OpenAIClient() if os.environ.get("OPENAI_API_KEY", "").strip() else None
