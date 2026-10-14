"""Shared test fixtures for API tests."""

from __future__ import annotations

import copy
import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.gateway import Gateway, InMemoryAuditSink
from core.tiers import OwnerControls, TierStore, TierView

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
EXAMPLES_DIR = ROOT_DIR / "contracts" / "examples"
SCHEMAS_DIR = ROOT_DIR / "contracts" / "schemas"

TEST_OWNER_TOKEN = "test-owner-secret-token-12345"


@pytest.fixture
def owner_token() -> str:
    return TEST_OWNER_TOKEN


@pytest.fixture
def auth_headers(owner_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {owner_token}"}


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch, owner_token: str) -> None:
    monkeypatch.setenv("AGENT_WALLET_OWNER_TOKEN", owner_token)


@pytest.fixture
def valid_order_payload() -> dict[str, Any]:
    raw = json.loads((EXAMPLES_DIR / "order.valid.json").read_text(encoding="utf-8"))
    return copy.deepcopy(raw)


@pytest.fixture
def test_components() -> dict[str, Any]:
    store = TierStore(":memory:")
    view = TierView(store)
    owner = OwnerControls(store)
    owner.set_tier("T2")
    audit = InMemoryAuditSink()
    gw = Gateway(tier="T2", emergency_stop=False, audit_sink=audit)
    return {
        "store": store,
        "tier_view": view,
        "owner_controls": owner,
        "audit_sink": audit,
        "gateway": gw,
    }


@pytest.fixture
def client(test_components: dict[str, Any], configured_env: None) -> Iterator[TestClient]:
    app = create_app(
        gateway=test_components["gateway"],
        tier_view=test_components["tier_view"],
        owner_controls=test_components["owner_controls"],
        audit_sink=test_components["audit_sink"],
    )
    with TestClient(app) as test_client:
        yield test_client
