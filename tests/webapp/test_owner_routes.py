"""Owner routes in multi mode: tier, stop, decisions, portfolio, ips and approvals per session.

Every route authenticates with the session cookie only and acts on the session owner's tenant
(design 6.2, 12.2). No network: in-test eth_account wallets, the fake chain and the recording
sender (the fake signer hook) from `tests/webapp/test_jobs.py`.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import get_validator
from api.main import reference_risk_inputs
from core.policy.ips import load_ips
from tests.webapp.conftest import HEADERS, ORIGIN, step_up
from tests.webapp.test_jobs import IPS_PATH, FakeChain
from tests.webapp.test_routes import Owner, World, slug

NOW_ISO = "2026-10-15T12:00:00Z"


class LiveHeadChain(FakeChain):
    """A chain the tx builder can use: a fresh head (it checks the wall clock), nonce and gas."""

    def __call__(self, method: str, params: list[Any]) -> Any:
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(int(time.time())), "baseFeePerGas": hex(self.base_fee)}
        if method == "eth_getTransactionCount":
            return "0x0"
        if method == "eth_estimateGas":
            return hex(100_000)
        return super().__call__(method, params)


@pytest.fixture
def world(tmp_path, auth_env) -> World:
    built = World(tmp_path, auth_env)
    built.env.chain.__class__ = LiveHeadChain
    return built


@pytest.fixture
def alice(world) -> Owner:
    return world.owner(fake_gateway=False)


@pytest.fixture
def bob(world) -> Owner:
    return world.owner(fake_gateway=False)


def put_tier(owner: Owner, tier: str, header: str | None = None):
    headers = dict(HEADERS)
    if header is not None:
        headers["X-Step-Up"] = header
    return owner.client.put("/tier", json={"schema_version": 1, "tier": tier}, headers=headers)


def raise_tier(owner: Owner, tier: str):
    header = step_up(owner.client, owner.wallet, "tier_raise", {"tier": tier})
    resp = put_tier(owner, tier, header)
    assert resp.status_code == 200, resp.text
    return resp


def escalating_policy(tmp_path: Path, owner: Owner) -> None:
    """Per-tx limit above the approval threshold, so a 150 USDC supply escalates."""
    doc = json.loads(IPS_PATH.read_text(encoding="utf-8"))
    doc["limits"] = {"per_tx_usd": "1000", "per_day_usd": "3000", "max_tx_per_hour": 5}
    doc["approval"] = {"escalate_above_usd": "100"}
    path = tmp_path / f"ips-{owner.tenant.owner[2:10]}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    owner.world.env.registry._settings.risk_inputs = reference_risk_inputs
    owner.tenant._ips_for = lambda addr, preset: load_ips(path)
    owner.tenant.reload_policy("conservative")


def supply_order(whole_usdc: int = 150) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": NOW_ISO,
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": str(whole_usdc * 10**6), "decimals": 6},
        "recipient": "self",
    }


def pending_order(owner: Owner, whole_usdc: int = 150) -> str:
    order = supply_order(whole_usdc)
    rec = owner.tenant.gateway.propose(order)
    assert rec["outcome"] == "pending_approval", rec["decision"]
    return order["order_id"]


# ---------------------------------------------------------------- authentication


ANON_ROUTES = [
    ("GET", "/tier", None),
    ("GET", "/decisions", None),
    ("GET", "/portfolio", None),
    ("GET", "/ips", None),
    ("PUT", "/tier", {"schema_version": 1, "tier": "T1"}),
    ("POST", "/emergency-stop", None),
    ("POST", "/emergency-stop/release", None),
    ("POST", f"/orders/{uuid.uuid4()}/approve", None),
    ("POST", f"/orders/{uuid.uuid4()}/reject", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ANON_ROUTES)
def test_owner_routes_reject_anonymous_callers(world, method, path, body):
    client = TestClient(world.app, base_url=ORIGIN)
    resp = client.request(method, path, headers=HEADERS, json=body)
    assert resp.status_code == 401 and slug(resp) == "unauthenticated"


@pytest.mark.parametrize(("method", "path", "body"), ANON_ROUTES)
def test_shared_owner_token_is_never_accepted_in_multi_mode(world, monkeypatch, method, path, body):
    monkeypatch.setenv("AGENT_WALLET_OWNER_TOKEN", "shared-secret")
    client = TestClient(world.app, base_url=ORIGIN)
    headers = {**HEADERS, "Authorization": "Bearer shared-secret", "X-Owner-Token": "shared-secret"}
    resp = client.request(method, path, headers=headers, json=body)
    assert resp.status_code == 401 and slug(resp) == "unauthenticated"


def test_cookie_writes_need_csrf_header(world, alice):
    resp = alice.client.post("/emergency-stop")  # cookie, no X-AW-CSRF and no Origin
    assert resp.status_code == 403 and slug(resp) == "csrf"
    assert alice.get("/tier").json()["emergency_stop"] is False


# ---------------------------------------------------------------- tier


def test_get_tier_returns_the_session_owners_state(world, alice):
    resp = alice.get("/tier")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["tier"], body["emergency_stop"], body["changed_by"]) == ("T0", False, "owner")
    assert not list(get_validator("tier-state").iter_errors(body))


def test_raising_the_tier_needs_a_step_up_signature(world, alice):
    missing = put_tier(alice, "T1")
    assert missing.status_code == 401 and slug(missing) == "step-up-required"
    assert alice.get("/tier").json()["tier"] == "T0"

    header = step_up(alice.client, alice.wallet, "tier_raise", {"tier": "T1"})
    ok = put_tier(alice, "T1", header)
    assert ok.status_code == 200 and ok.json()["tier"] == "T1"
    assert alice.get("/tier").json()["tier"] == "T1"
    assert not list(get_validator("tier-state").iter_errors(ok.json()))


def test_raise_from_t0_straight_to_t2_and_t1_to_t2_both_need_step_up(world, alice):
    assert put_tier(alice, "T2").status_code == 401
    raise_tier(alice, "T1")
    assert put_tier(alice, "T2").status_code == 401
    assert raise_tier(alice, "T2").json()["tier"] == "T2"


def test_step_up_is_bound_to_the_new_tier_action_and_single_use(world, alice):
    t1 = step_up(alice.client, alice.wallet, "tier_raise", {"tier": "T1"})
    wrong_tier = put_tier(alice, "T2", t1)  # signed for T1, used for T2
    assert wrong_tier.status_code == 401 and slug(wrong_tier) == "step-up-required"
    assert put_tier(alice, "T1", t1).status_code == 401  # the failed try consumed it
    wrong_action = step_up(alice.client, alice.wallet, "release_stop", {})
    assert put_tier(alice, "T1", wrong_action).status_code == 401
    assert alice.get("/tier").json()["tier"] == "T0"


def test_lowering_the_tier_needs_the_session_only(world, alice):
    raise_tier(alice, "T2")
    down = put_tier(alice, "T1")
    assert down.status_code == 200 and down.json()["tier"] == "T1"
    assert put_tier(alice, "T0").json()["tier"] == "T0"
    assert put_tier(alice, "T0").status_code == 200  # same tier is not a raise


def test_put_tier_body_rules(world, alice):
    for body in ({"schema_version": 1, "tier": "T9"}, {"tier": "T1"}, {"schema_version": 1}):
        resp = alice.client.put("/tier", json=body, headers=HEADERS)
        assert resp.status_code == 422 and slug(resp) == "invalid-request"
    extra = {"schema_version": 1, "tier": "T1", "owner": "0x" + "1" * 40}
    assert alice.client.put("/tier", json=extra, headers=HEADERS).status_code == 422


def test_tier_and_stop_are_isolated_per_owner(world, alice, bob):
    raise_tier(alice, "T2")
    assert alice.post("/emergency-stop").status_code == 200
    assert bob.get("/tier").json()["tier"] == "T0"
    assert bob.get("/tier").json()["emergency_stop"] is False
    assert bob.get("/me").json()["tier"] == "T0"
    # a step-up Alice signed cannot raise Bob's tier
    header = step_up(alice.client, alice.wallet, "tier_raise", {"tier": "T2"})
    assert put_tier(bob, "T2", header).status_code == 401
    assert bob.get("/tier").json()["tier"] == "T0"


def test_owner_address_in_query_or_body_is_ignored(world, alice, bob):
    raise_tier(alice, "T2")
    assert bob.get(f"/tier?owner={alice.wallet.address}").json()["tier"] == "T0"
    body = {"schema_version": 1, "tier": "T1", "owner": alice.wallet.address}
    assert bob.client.put("/tier", json=body, headers=HEADERS).status_code == 422
    assert alice.get("/tier").json()["tier"] == "T2"


# ---------------------------------------------------------------- emergency stop


def test_stop_needs_the_session_only_and_is_audited(world, alice):
    resp = alice.post("/emergency-stop")
    assert resp.status_code == 200
    rec = resp.json()
    assert rec["outcome"] == "no_action" and rec["emergency_stop"] is True
    assert rec["no_action"]["rules_hit"][0]["code"] == "TIER.EMERGENCY_STOP"
    assert rec["ips_hash"] == alice.tenant.gateway.ips_hash
    assert not list(get_validator("decision-record").iter_errors(rec))
    assert alice.get("/tier").json()["emergency_stop"] is True
    assert alice.get("/audit/verify").json() == {"ok": True, "records": 1, "first_bad_seq": None}
    again = alice.post("/emergency-stop")
    assert again.status_code == 409 and slug(again) == "emergency-stop-state"


def test_release_needs_a_step_up_signature(world, alice):
    assert alice.post("/emergency-stop").status_code == 200
    missing = alice.post("/emergency-stop/release")
    assert missing.status_code == 401 and slug(missing) == "step-up-required"
    assert alice.get("/tier").json()["emergency_stop"] is True

    header = step_up(alice.client, alice.wallet, "release_stop", {})
    ok = alice.post("/emergency-stop/release", None, {"X-Step-Up": header})
    assert ok.status_code == 200
    assert ok.json()["no_action"]["rules_hit"][0]["code"] == "TIER.EMERGENCY_STOP_RELEASED"
    assert ok.json()["emergency_stop"] is False
    assert alice.get("/tier").json()["emergency_stop"] is False
    assert alice.get("/audit/verify").json()["records"] == 2


def test_release_with_a_signature_for_another_action_is_refused(world, alice):
    alice.post("/emergency-stop")
    wrong = step_up(alice.client, alice.wallet, "tier_raise", {"tier": "T1"})
    resp = alice.post("/emergency-stop/release", None, {"X-Step-Up": wrong})
    assert resp.status_code == 401 and slug(resp) == "step-up-required"


def test_release_when_not_stopped_is_409(world, alice):
    header = step_up(alice.client, alice.wallet, "release_stop", {})
    resp = alice.post("/emergency-stop/release", None, {"X-Step-Up": header})
    assert resp.status_code == 409 and slug(resp) == "emergency-stop-state"


def test_one_owners_stop_never_touches_another_owners_chain(world, alice, bob):
    alice.post("/emergency-stop")
    assert alice.get("/audit/verify").json()["records"] == 1
    assert bob.get("/audit/verify").json()["records"] == 0
    assert bob.post("/emergency-stop/release", None, {
        "X-Step-Up": step_up(bob.client, bob.wallet, "release_stop", {})
    }).status_code == 409  # fmt: skip


# ---------------------------------------------------------------- decisions, portfolio, ips


def test_decisions_are_the_session_owners_own(world, alice, bob):
    alice.tenant.gateway.record_no_action("alice one", NOW_ISO)
    alice.tenant.gateway.record_no_action("alice two", NOW_ISO)
    bob.tenant.gateway.record_no_action("bob one", NOW_ISO)
    got = alice.get("/decisions")
    assert got.status_code == 200
    body = got.json()
    assert [r["no_action"]["reason"] for r in body["items"]] == ["alice one", "alice two"]
    assert not list(get_validator("decisions-response").iter_errors(body))
    assert [r["no_action"]["reason"] for r in bob.get("/decisions").json()["items"]] == ["bob one"]


def test_decisions_filters_paging_and_validation(world, alice):
    for n in range(3):
        alice.tenant.gateway.record_no_action(f"n{n}", NOW_ISO)
    page = alice.get("/decisions?limit=2").json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = alice.get(f"/decisions?limit=2&cursor={page['next_cursor']}").json()
    assert [r["no_action"]["reason"] for r in rest["items"]] == ["n2"]
    assert alice.get("/decisions?outcome=executed").json()["items"] == []
    assert alice.get("/decisions?outcome=nope").status_code == 422
    assert alice.get("/decisions?limit=0").status_code == 422
    assert alice.get("/decisions?order_id=not-a-uuid").status_code == 422


def test_foreign_order_id_filter_finds_nothing(world, alice, bob):
    order_id = pending_order_after_raise(world, alice)
    assert len(alice.get(f"/decisions?order_id={order_id}").json()["items"]) == 1
    assert bob.get(f"/decisions?order_id={order_id}").json()["items"] == []


def test_portfolio_is_read_live_for_the_tenant(world, alice, bob):
    world.env.chain.usdc[alice.tenant.agent_address.lower()] = 250 * 10**6
    resp = alice.get("/portfolio")
    assert resp.status_code == 200
    body = resp.json()
    assert not list(get_validator("portfolio-state").iter_errors(body))
    assert body["price_source"] == "mainnet_reference"
    usdc = [p for p in body["positions"] if p["token"] == "USDC" and p["location"] == "wallet"]
    assert usdc and usdc[0]["balance"]["value"] == str(250 * 10**6)
    theirs = bob.get("/portfolio").json()["positions"]
    other = [p for p in theirs if p["token"] == "USDC" and p["location"] == "wallet"]
    assert other[0]["balance"]["value"] == str(world.env.chain.default_usdc)  # not Alice's balance


def test_portfolio_rpc_failure_is_503_without_the_error_text(world, alice):
    world.env.chain.down = True
    resp = alice.get("/portfolio")
    assert resp.status_code == 503 and slug(resp) == "service-unavailable"
    assert "node down" not in resp.text


def test_ips_is_the_tenants_policy_with_its_hash(world, alice):
    resp = alice.get("/ips")
    assert resp.status_code == 200
    body = resp.json()
    expected = load_ips(IPS_PATH)
    assert body["ips_hash"] == expected.hash == alice.tenant.gateway.ips_hash
    assert body["ips"]["limits"] == dict(expected.ips["limits"])
    assert not list(get_validator("ips-response").iter_errors(body))


# ---------------------------------------------------------------- approvals


def pending_order_after_raise(world: World, owner: Owner) -> str:
    escalating_policy(world.env.registry._settings.data_dir, owner)
    raise_tier(owner, "T2")
    return pending_order(owner)


def test_approve_needs_a_step_up_bound_to_the_order_id(world, alice):
    order_id = pending_order_after_raise(world, alice)
    missing = alice.post(f"/orders/{order_id}/approve")
    assert missing.status_code == 401 and slug(missing) == "step-up-required"
    assert alice.tenant.tier_view.approval_status(order_id) in (None, "pending")
    assert world.env.sent == []

    other = step_up(alice.client, alice.wallet, "approve", {"order_id": str(uuid.uuid4())})
    assert alice.post(f"/orders/{order_id}/approve", None, {"X-Step-Up": other}).status_code == 401

    header = step_up(alice.client, alice.wallet, "approve", {"order_id": order_id})
    ok = alice.post(f"/orders/{order_id}/approve", None, {"X-Step-Up": header})
    assert ok.status_code == 200, ok.text
    rec = ok.json()
    assert rec["order_id"] == order_id and rec["outcome"] == "executed"
    assert rec["approval"]["status"] == "approved" and rec["approval"]["by"] == "owner"
    assert not list(get_validator("decision-record").iter_errors(rec))
    assert len(world.env.sent) == 1  # one signed transaction through the fake signer
    assert alice.get("/audit/verify").json()["ok"] is True
    again = alice.post(
        f"/orders/{order_id}/approve",
        None,
        {"X-Step-Up": step_up(alice.client, alice.wallet, "approve", {"order_id": order_id})},
    )
    assert again.status_code == 409 and slug(again) == "not-pending"
    assert len(world.env.sent) == 1


def test_approve_that_hits_a_busy_gateway_can_be_retried(world, alice):
    from core.gateway.exceptions import GatewayBusy

    order_id = pending_order_after_raise(world, alice)
    gateway = alice.tenant.gateway
    real_propose = gateway.propose
    calls = {"n": 0}

    def flaky(order, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise GatewayBusy("another job holds the gateway")
        return real_propose(order, **kwargs)

    gateway.propose = flaky
    first = alice.post(
        f"/orders/{order_id}/approve",
        None,
        {"X-Step-Up": step_up(alice.client, alice.wallet, "approve", {"order_id": order_id})},
    )
    assert first.status_code == 409 and world.env.sent == []
    retry = alice.post(
        f"/orders/{order_id}/approve",
        None,
        {"X-Step-Up": step_up(alice.client, alice.wallet, "approve", {"order_id": order_id})},
    )
    assert retry.status_code == 200 and retry.json()["outcome"] == "executed"
    assert len(world.env.sent) == 1
    done = alice.post(
        f"/orders/{order_id}/approve",
        None,
        {"X-Step-Up": step_up(alice.client, alice.wallet, "approve", {"order_id": order_id})},
    )
    assert done.status_code == 409 and slug(done) == "not-pending"
    assert len(world.env.sent) == 1


def test_approve_unknown_order_is_404(world, alice):
    order_id = str(uuid.uuid4())
    header = step_up(alice.client, alice.wallet, "approve", {"order_id": order_id})
    resp = alice.post(f"/orders/{order_id}/approve", None, {"X-Step-Up": header})
    assert resp.status_code == 404 and slug(resp) == "not-found"


def test_approve_is_refused_while_the_owner_stop_is_engaged(world, alice):
    order_id = pending_order_after_raise(world, alice)
    assert alice.post("/emergency-stop").status_code == 200
    header = step_up(alice.client, alice.wallet, "approve", {"order_id": order_id})
    resp = alice.post(f"/orders/{order_id}/approve", None, {"X-Step-Up": header})
    assert resp.status_code == 409 and slug(resp) == "emergency-stop-state"
    assert world.env.sent == []


def test_reject_needs_the_session_only_and_records_a_denial(world, alice):
    order_id = pending_order_after_raise(world, alice)
    resp = alice.post(f"/orders/{order_id}/reject")
    assert resp.status_code == 200
    rec = resp.json()
    assert rec["outcome"] == "denied" and rec["approval"]["status"] == "rejected"
    assert rec["decision"]["rules_hit"][0]["code"] == "TIER.APPROVAL_REJECTED"
    assert not list(get_validator("decision-record").iter_errors(rec))
    assert world.env.sent == []
    again = alice.post(f"/orders/{order_id}/reject")
    assert again.status_code == 409 and slug(again) == "not-pending"


def test_reject_unknown_order_is_404_and_bad_id_is_422(world, alice):
    assert alice.post(f"/orders/{uuid.uuid4()}/reject").status_code == 404
    assert alice.post("/orders/not-a-uuid/reject").status_code == 422
    assert alice.post("/orders/not-a-uuid/approve").status_code == 422


def test_an_order_of_owner_a_is_404_for_owner_b(world, alice, bob):
    order_id = pending_order_after_raise(world, alice)
    header = step_up(bob.client, bob.wallet, "approve", {"order_id": order_id})
    approve = bob.post(f"/orders/{order_id}/approve", None, {"X-Step-Up": header})
    assert approve.status_code == 404 and slug(approve) == "not-found"
    reject = bob.post(f"/orders/{order_id}/reject")
    assert reject.status_code == 404
    assert world.env.sent == []
    assert alice.tenant.tier_view.approval_status(order_id) in (None, "pending")
    # Alice can still decide it
    assert alice.post(f"/orders/{order_id}/reject").status_code == 200


def test_a_step_up_signed_by_another_wallet_cannot_approve(world, alice, bob):
    order_id = pending_order_after_raise(world, alice)
    header = step_up(bob.client, bob.wallet, "approve", {"order_id": order_id})
    resp = alice.post(f"/orders/{order_id}/approve", None, {"X-Step-Up": header})
    assert resp.status_code == 401
    assert world.env.sent == []


# ---------------------------------------------------------------- end to end through the jobs


def test_mint_is_denied_at_t0_then_executes_after_the_owner_raises_to_t2(world, alice):
    first = alice.finish(alice.post("/agent/mint", {"amount_usdc": "50"}))
    assert (first["status"], first["error_code"]) == ("denied", "TIER.READ_ONLY")
    assert world.env.sent == []

    assert put_tier(alice, "T2").status_code == 401  # a session alone cannot raise
    raise_tier(alice, "T2")
    second = alice.finish(alice.post("/agent/mint", {"amount_usdc": "50"}))
    assert (second["status"], second["error_code"]) == ("succeeded", None)
    assert second["records"][0]["outcome"] == "executed"
    assert len(world.env.sent) == 1  # the fake signer received exactly one signed transaction
    assert alice.get("/audit/verify").json()["ok"] is True


def test_a_stop_pressed_with_the_session_only_halts_the_next_run(world, alice):
    raise_tier(alice, "T2")
    assert alice.post("/emergency-stop").status_code == 200
    job = alice.finish(alice.post("/runs", {}))
    assert (job["status"], job["error_code"]) == ("denied", "TIER.EMERGENCY_STOP")
    assert world.env.sent == []
