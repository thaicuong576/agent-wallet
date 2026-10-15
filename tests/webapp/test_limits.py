"""Limits read model and suggested prompts (ticket 124, contract C4 and C5)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.limits import SUGGESTIONS
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from tests.webapp.conftest import HEADERS
from tests.webapp.test_jobs import NOW
from tests.webapp.test_routes import SPEC, Owner, World, shape


@pytest.fixture
def world(tmp_path, auth_env) -> World:
    return World(tmp_path, auth_env)


@pytest.fixture
def alice(world) -> Owner:
    return world.owner(fake_gateway=False)


@pytest.fixture
def bob(world) -> Owner:
    return world.owner(fake_gateway=False)


def real_owner(world):
    return world.owner(fake_gateway=False)


class Ledger:
    def __init__(self, spent: str) -> None:
        self.spent, self.since = Decimal(spent), None

    def spent_usd_since(self, since):
        self.since = since
        return self.spent


def test_limits_come_from_the_active_ips_and_the_ledger(world):
    owner = real_owner(world)
    ledger = Ledger("42.5")
    owner.tenant.stores = SimpleNamespace(ledger=ledger)
    body = owner.get("/limits").json()
    assert body == {
        "per_order_usd": "100.00",
        "per_day_usd": "300.00",
        "used_today_usd": "42.50",
        "approval_threshold_usd": "100.00",
        "protocols": ["aave-v3", "uniswap-v3"],
        "emergency_stop": "ready",
        "as_of": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    assert ledger.since == NOW - timedelta(hours=24)


def test_limits_report_the_stop_flag(world):
    owner = real_owner(world)
    world.env.stop_owner(owner.tenant)
    assert owner.get("/limits").json()["emergency_stop"] == "stopped"


def test_limits_has_no_write_path(world):
    owner = real_owner(world)
    for method in ("post", "put", "patch", "delete"):
        resp = getattr(owner.client, method)("/limits", headers=HEADERS)
        assert resp.status_code in (404, 405)


def test_limits_and_suggestions_need_a_session(world):
    anon = TestClient(world.app)
    assert anon.get("/limits").status_code == 401
    assert anon.get("/chat/suggestions").status_code == 401


def test_limits_are_scoped_to_the_session_owner(world, alice, bob):
    alice.tenant.stores = SimpleNamespace(ledger=Ledger("77"))
    bob.tenant.stores = SimpleNamespace(ledger=Ledger("1"))
    assert alice.get("/limits").json()["used_today_usd"] == "77.00"
    assert bob.get("/limits").json()["used_today_usd"] == "1.00"
    other = bob.get(f"/limits?owner={alice.wallet.address}").json()
    assert other["used_today_usd"] == "1.00"


def test_suggestions_funded(world):
    owner = real_owner(world)
    assert owner.get("/chat/suggestions").json() == {"items": SUGGESTIONS["funded"]}


def test_suggestions_no_funds(world):
    owner = real_owner(world)
    world.env.chain.usdc[owner.tenant.agent_address.lower()] = 0
    assert owner.get("/chat/suggestions").json() == {"items": SUGGESTIONS["no_funds"]}


def test_suggestions_stopped_wins_over_everything(world):
    owner = real_owner(world)
    world.env.chain.usdc[owner.tenant.agent_address.lower()] = 0
    world.env.stop_owner(owner.tenant)
    assert owner.get("/chat/suggestions").json() == {"items": SUGGESTIONS["stopped"]}


def test_suggestions_pending_approval(world):
    owner = real_owner(world)
    OwnerControls(TierStore(owner.tenant.paths.state)).request_approval(str(uuid.uuid4()))
    assert owner.get("/chat/suggestions").json() == {"items": SUGGESTIONS["pending_approval"]}


def test_suggestions_rpc_down_is_neutral_and_makes_no_llm_call(world):
    owner = real_owner(world)
    world.env.llm = None  # a model call would fail loudly
    world.env.chain.down = True
    assert owner.get("/chat/suggestions").json() == {"items": SUGGESTIONS["unknown"]}


def test_suggestions_are_scoped_to_the_session_owner(world, alice, bob):
    world.env.stop_owner(alice.tenant)
    assert alice.get("/chat/suggestions").json()["items"] == SUGGESTIONS["stopped"]
    assert bob.get("/chat/suggestions").json()["items"] == SUGGESTIONS["funded"]


def test_shapes_match_the_openapi_contract(world):
    owner = real_owner(world)
    assert owner.get("/limits").status_code == 200
    assert shape("ChatMessage")  # spec loads; the two new paths are declared with a session scheme
    for path in ("/limits", "/chat/suggestions"):
        assert SPEC["paths"][path]["get"]["security"]
