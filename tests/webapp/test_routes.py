"""The webapp routes over HTTP: auth levels, paths, isolation, jobs, step-up, contract shapes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthService, InMemoryAuthStore
from api.wallet import WebappServices
from tests.webapp.conftest import HEADERS, ORIGIN, TestWallet, login, step_up
from tests.webapp.test_jobs import HOSTILE_NOTE, FakeGateway, make_env

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
TX = "0x" + "c3" * 32
USDC_5 = {"value": "5000000", "decimals": 6}


def shape(name: str) -> jsonschema.Draft202012Validator:
    """Validator for one component schema of contracts/openapi.yaml."""
    return jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": SPEC["components"]}
    )


def slug(resp) -> str:
    assert resp.headers["content-type"].startswith("application/problem+json")
    return resp.json()["type"].rsplit(":", 1)[1]


class World:
    """One app, a fake chain, and any number of signed-in owners."""

    def __init__(self, tmp_path, auth_env) -> None:
        self.env = make_env(tmp_path)
        auth = AuthService(
            InMemoryAuthStore(),
            clock=self.env.clock,
            on_login=lambda addr: self.env.registry.open(addr, create=True),
        )
        services = WebappServices(
            registry=self.env.registry, auth=auth, wallet=self.env.wallet, jobs=self.env.jobs
        )
        self.app = create_app(webapp=services)

    def owner(self, fake_gateway: bool = True) -> Owner:
        owner = Owner(self, TestWallet())
        if fake_gateway:
            owner.tenant.gateway = FakeGateway("executed")
        return owner

    def drain(self) -> None:
        self.env.executor.drain()


class Owner:
    def __init__(self, world: World, wallet: TestWallet) -> None:
        self.world, self.wallet = world, wallet
        self.client = TestClient(world.app, base_url=ORIGIN)
        assert login(self.client, wallet).status_code == 200
        self.tenant = world.env.registry.open(wallet.address.lower())

    def get(self, path: str, **kw: Any):
        return self.client.get(path, **kw)

    def post(self, path: str, json: Any = None, headers: dict | None = None):
        return self.client.post(path, json=json, headers={**HEADERS, **(headers or {})})

    def finish(self, resp) -> dict[str, Any]:
        assert resp.status_code == 202, resp.text
        self.world.drain()
        job = self.get(f"/jobs/{resp.json()['job_id']}")
        assert job.status_code == 200
        return job.json()


@pytest.fixture
def world(tmp_path, auth_env) -> World:
    return World(tmp_path, auth_env)


@pytest.fixture
def alice(world) -> Owner:
    return world.owner()


@pytest.fixture
def bob(world) -> Owner:
    return world.owner()


# ---------------------------------------------------------------- auth levels and paths

SESSION_ROUTES = [
    ("GET", "/me"),
    ("GET", "/agent"),
    ("POST", "/topups"),
    ("GET", "/topups/" + TX),
    ("POST", "/agent/mint"),
    ("POST", "/agent/withdraw"),
    ("POST", "/runs"),
    ("GET", "/jobs"),
    ("GET", "/jobs/" + "0" * 32),
    ("GET", "/audit/verify"),
]


@pytest.mark.parametrize(("method", "path"), SESSION_ROUTES)
def test_session_routes_reject_anonymous_callers(world, method, path):
    client = TestClient(world.app, base_url=ORIGIN)
    resp = client.request(method, path, headers=HEADERS, json={} if method == "POST" else None)
    assert resp.status_code == 401 and slug(resp) == "unauthenticated"


def test_presets_is_public_and_get_only(world):
    client = TestClient(world.app, base_url=ORIGIN)
    resp = client.get("/presets")
    assert resp.status_code == 200 and resp.json()["schema_version"] == 1
    assert resp.json()["items"][0]["preset_id"]
    assert client.put("/presets", json={}, headers=HEADERS).status_code == 405


def test_unconfigured_app_fails_closed_but_serves_public_routes():
    client = TestClient(create_app(), base_url=ORIGIN)
    assert client.get("/health").status_code == 200
    assert client.get("/presets").status_code == 200
    assert client.get("/agent").status_code == 503
    assert client.get("/me").status_code == 503


def test_cookie_requests_need_csrf_header_and_origin(world, alice):
    resp = alice.client.post("/runs", json={})  # no X-AW-CSRF, no Origin
    assert resp.status_code == 403 and slug(resp) == "csrf"
    assert alice.client.get("/me").status_code == 200  # reads need neither


def test_logout_ends_access(world, alice):
    assert alice.post("/auth/logout").status_code == 200
    assert alice.get("/me").status_code == 401


def test_no_route_takes_an_owner_address(world):
    for path, item in SPEC["paths"].items():
        for op in item.values():
            for param in op.get("parameters", []):
                assert "owner" not in param.get("name", "").lower(), (path, param)
            body = (
                (op.get("requestBody") or {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            if path not in ("/auth/nonce", "/auth/verify"):  # sign-in names the signing address
                assert "owner" not in body.get("properties", {}), path
                assert "address" not in body.get("properties", {}), path


# ---------------------------------------------------------------- me, agent, top-ups


def test_me_and_agent_return_the_session_owners_data(world, alice):
    me = alice.get("/me")
    assert me.status_code == 200
    body = me.json()
    assert body["address"] == alice.wallet.address.lower()
    assert body["agent_address"] == alice.tenant.agent_address
    assert (body["mode"], body["chain_id"], body["tier"]) == ("multi", 84532, "T0")
    assert not list(shape("Me").iter_errors(body))
    agent = alice.get("/agent")
    assert agent.status_code == 200
    assert agent.json()["address"] == alice.tenant.agent_address
    assert not list(shape("Agent").iter_errors(agent.json()))


def test_topup_over_http_pending_then_confirmed(world, alice):
    world.env.chain.txs[TX] = {
        "from": alice.tenant.owner,
        "to": alice.tenant.agent_address,
        "value": hex(10**16),
        "chainId": hex(84532),
    }
    posted = alice.post("/topups", {"tx_hash": TX})
    assert posted.status_code == 202 and posted.json()["status"] == "pending"
    assert not list(shape("Topup").iter_errors(posted.json()))
    assert alice.get(f"/topups/{TX}").json()["status"] == "pending"
    world.env.chain.receipts[TX] = {"status": "0x1", "blockNumber": "0x10"}
    got = alice.get(f"/topups/{TX}").json()
    assert (got["status"], got["block_number"]) == ("confirmed", 16)


def test_topup_rejections_over_http(world, alice, bob):
    world.env.chain.txs[TX] = {
        "from": bob.tenant.owner,  # someone else's transaction
        "to": alice.tenant.agent_address,
        "value": hex(10**16),
        "chainId": hex(84532),
    }
    resp = alice.post("/topups", {"tx_hash": TX})
    assert resp.status_code == 422 and slug(resp) == "invalid-request"
    assert alice.post("/topups", {"tx_hash": "nope"}).status_code == 422
    assert alice.post("/topups", {"tx_hash": TX, "owner": "0x" + "0" * 40}).status_code == 422
    assert alice.get(f"/topups/{TX}").status_code == 404


# ---------------------------------------------------------------- jobs over HTTP


def test_mint_job_over_http(world, alice):
    resp = alice.post("/agent/mint", {"amount_usdc": "100"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued" and len(resp.json()["job_id"]) == 32
    job = alice.finish(resp)
    assert (job["kind"], job["status"], job["planner_client"]) == ("mint", "succeeded", "none")
    assert not list(shape("Job").iter_errors(job))
    (order, meta) = alice.tenant.gateway.calls[0]
    assert meta["origin"] == "owner" and order["amount"]["value"] == "100000000"
    listed = alice.get("/jobs").json()["items"]
    assert [j["job_id"] for j in listed] == [job["job_id"]] and "records" not in listed[0]


@pytest.mark.parametrize(
    "body", [{}, {"amount_usdc": "0"}, {"amount_usdc": 5}, {"amount_usdc": "5", "to": "0x1"}]
)
def test_mint_body_validation(world, alice, body):
    resp = alice.post("/agent/mint", body)
    assert resp.status_code == 422 and slug(resp) == "invalid-request"
    assert world.env.executor.pending == []


def test_second_job_is_409_with_the_active_job_id(world, alice):
    first = alice.post("/agent/mint", {"amount_usdc": "5"}).json()["job_id"]
    resp = alice.post("/runs", {})
    assert resp.status_code == 409 and slug(resp) == "job-in-progress"
    assert resp.json()["job_id"] == first
    world.drain()
    assert alice.post("/runs", {}).status_code == 202


def test_rate_limit_is_429_with_retry_after(world, alice):
    for _ in range(4):
        alice.finish(alice.post("/agent/mint", {"amount_usdc": "5"}))
    resp = alice.post("/agent/mint", {"amount_usdc": "5"})
    assert resp.status_code == 429 and slug(resp) == "rate-limited"
    assert int(resp.headers["Retry-After"]) > 0


def test_run_note_cap_and_unknown_fields(world, alice):
    assert alice.post("/runs", {"note": "x" * 2001}).status_code == 422
    assert alice.post("/runs", {"owner": "0x" + "1" * 40}).status_code == 422
    assert alice.post("/runs", {"planner": "mystery"}).status_code == 422
    assert alice.post("/runs", None).status_code == 202  # the body is optional


def test_run_under_owner_stop_ends_denied_over_http(world):
    carol = world.owner(fake_gateway=False)
    world.env.set_tier(carol.tenant, "T2")
    world.env.stop_owner(carol.tenant)
    job = carol.finish(carol.post("/runs", {}))
    assert (job["status"], job["error_code"]) == ("denied", "TIER.EMERGENCY_STOP")
    assert world.env.sent == []


def test_hostile_note_over_http_is_denied_in_plain_words_and_nothing_is_signed(world):
    carol = world.owner(fake_gateway=False)
    world.env.set_tier(carol.tenant, "T2")
    job = carol.finish(carol.post("/runs", {"note": HOSTILE_NOTE, "planner": "scripted_hostile"}))
    (rec,) = job["records"]
    assert rec["outcome"] == "denied" and "limit" in rec["reason"].lower()
    assert job["planner_client"] == "scripted" and rec["planner_client"] == "scripted"
    assert world.env.sent == []
    verified = carol.get("/audit/verify").json()
    assert verified == {"ok": True, "records": 1, "first_bad_seq": None}


def test_unknown_job_is_404_and_malformed_id_is_404(world, alice):
    assert alice.get("/jobs/" + "f" * 32).status_code == 404
    assert alice.get("/jobs/not-a-job-id").status_code == 404


# ---------------------------------------------------------------- withdraw needs a step-up


def test_withdraw_requires_a_step_up_signature(world, alice):
    body = {"token": "USDC", "amount": USDC_5}
    missing = alice.post("/agent/withdraw", body)
    assert missing.status_code == 401 and slug(missing) == "step-up-required"
    assert world.env.executor.pending == []
    header = step_up(alice.client, alice.wallet, "withdraw", body)
    ok = alice.post("/agent/withdraw", body, {"X-Step-Up": header})
    assert ok.status_code == 202
    job = alice.finish(ok)
    assert (job["kind"], job["status"]) == ("withdraw", "succeeded")
    (order, meta) = alice.tenant.gateway.calls[0]
    assert meta["origin"] == "owner"
    assert (order["function"], order["recipient"], order["protocol"]) == (
        "transfer", "owner", "wallet",
    )  # fmt: skip
    assert order["amount"] == USDC_5 and order["token"] == "USDC"


def test_withdraw_step_up_is_bound_to_the_exact_amount_and_single_use(world, alice):
    signed = {"token": "USDC", "amount": USDC_5}
    header = step_up(alice.client, alice.wallet, "withdraw", signed)
    bigger = {"token": "USDC", "amount": {"value": "9000000", "decimals": 6}}
    resp = alice.post("/agent/withdraw", bigger, {"X-Step-Up": header})
    assert resp.status_code == 401 and slug(resp) == "step-up-required"
    # the failed attempt consumed the challenge: the honest request now needs a fresh one
    again = alice.post("/agent/withdraw", signed, {"X-Step-Up": header})
    assert again.status_code == 401


def test_withdraw_step_up_from_another_wallet_is_refused(world, alice, bob):
    body = {"token": "USDC", "amount": USDC_5}
    header = step_up(bob.client, bob.wallet, "withdraw", body)  # Bob's challenge, Bob's signature
    resp = alice.post("/agent/withdraw", body, {"X-Step-Up": header})
    assert resp.status_code == 401


def test_withdraw_body_rules(world, alice):
    for body in (
        {"token": "DAI", "amount": USDC_5},
        {"token": "USDC", "amount": USDC_5, "to": "0x" + "1" * 40},
        {"token": "USDC", "amount": {"value": "0", "decimals": 6}},
        {"token": "ETH", "amount": {"value": "5", "decimals": 6}},
    ):
        header = step_up(alice.client, alice.wallet, "withdraw", body)
        resp = alice.post("/agent/withdraw", body, {"X-Step-Up": header})
        assert resp.status_code == 422, body
    assert world.env.executor.pending == []


# ---------------------------------------------------------------- isolation (design 5.4)


def test_session_a_cannot_read_owner_b_state(world, alice, bob):
    job_a = alice.finish(alice.post("/agent/mint", {"amount_usdc": "5"}))
    assert alice.get(f"/jobs/{job_a['job_id']}").status_code == 200
    assert bob.get(f"/jobs/{job_a['job_id']}").status_code == 404
    assert bob.get("/jobs").json() == {"items": []}
    assert (
        bob.get("/agent").json()["address"]
        == bob.tenant.agent_address
        != alice.tenant.agent_address
    )
    assert bob.get("/me").json()["address"] == bob.wallet.address.lower()


def test_owner_query_parameters_are_ignored(world, alice, bob):
    a = alice.tenant.agent_address
    for path in ("/agent", "/me", "/jobs", "/audit/verify"):
        for param in ("owner", "address", "agent"):
            resp = bob.get(f"{path}?{param}={alice.wallet.address}")
            assert resp.status_code == 200
            assert a not in resp.text and alice.wallet.address.lower() not in resp.text


def test_foreign_topup_hash_is_404(world, alice, bob):
    world.env.chain.txs[TX] = {
        "from": alice.tenant.owner,
        "to": alice.tenant.agent_address,
        "value": hex(10**16),
        "chainId": hex(84532),
    }
    assert alice.post("/topups", {"tx_hash": TX}).status_code == 202
    assert bob.get(f"/topups/{TX}").status_code == 404
    assert bob.post("/topups", {"tx_hash": TX}).status_code == 422  # sender is not Bob


def test_each_owner_chain_verifies_alone(world):
    a, b = world.owner(fake_gateway=False), world.owner(fake_gateway=False)
    for note in ("one", "two"):
        a.tenant.gateway.record_no_action(note, "2026-10-15T12:00:00Z")
    assert a.get("/audit/verify").json()["records"] == 2
    assert b.get("/audit/verify").json() == {"ok": True, "records": 0, "first_bad_seq": None}
    # tamper with A's file: A's verification fails at the edited seq, B is unaffected
    import sqlite3

    with sqlite3.connect(a.tenant.paths.audit) as db:
        db.execute("PRAGMA ignore_check_constraints = ON")
        triggers = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
        for name in triggers:
            db.execute(f'DROP TRIGGER "{name}"')
        db.execute(
            "UPDATE audit_records SET payload = replace(payload, 'one', 'ONE') WHERE seq = 0"
        )
    # Cache returns previous valid result until a record is appended (version changes)
    assert a.get("/audit/verify").json()["ok"] is True
    a.tenant.gateway.record_no_action("three", "2026-10-15T12:00:00Z")
    bad = a.get("/audit/verify").json()
    assert bad["ok"] is False and bad["first_bad_seq"] == 0
    assert b.get("/audit/verify").json()["ok"] is True


# ---------------------------------------------------------------- contract shapes


def test_session_started_shape(world):
    client = TestClient(world.app, base_url=ORIGIN)
    resp = login(client, TestWallet())
    assert resp.status_code == 200
    assert not list(shape("SessionStarted").iter_errors(resp.json()))


def test_every_new_route_is_declared_with_a_session_scheme_unless_public():
    public = {"/auth/nonce", "/auth/verify", "/presets"}
    new = {
        "/me", "/agent", "/topups", "/topups/{tx_hash}", "/agent/mint", "/agent/withdraw",
        "/runs", "/jobs", "/jobs/{job_id}", "/audit/verify", "/auth/logout", "/auth/step-up",
    } | public  # fmt: skip
    for path in new:
        ops = SPEC["paths"][path].values()
        for op in ops:
            has = bool(op.get("security"))
            assert has != (path in public), path


def test_multi_mode_wiring_supplies_risk_inputs_and_touches_no_network(tmp_path, monkeypatch):
    from api.main import build_webapp

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: pytest.fail("network used while wiring")
    )
    services = build_webapp({"AGENT_WALLET_DATA_DIR": str(tmp_path)})
    settings = services.registry._settings
    risk = settings.risk_inputs()
    assert risk["data_age_seconds"] == 0 and risk["as_of"].endswith("Z")
    assert settings.keystore_secret is None  # unset: reads work, wallet creation fails closed
    assert services.mode == "multi" and services.jobs.limits.runs_per_hour == 6
