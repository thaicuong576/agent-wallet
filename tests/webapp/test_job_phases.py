"""Job phases (contract C1) and cancel (contract C2): ticket 122.

The executor is manual (`world.drain()`), so a job can be inspected while queued and cancelled at
a precise point. The gateway is the scripted one of the chat tests; "never signs" is checked by
what the gateway was asked to propose.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.agent import ScriptedClient
from tests.webapp.test_chat import (
    ScriptGateway,
    approve_then_supply,
    conversation,
    order,
    plan,
    say,
)
from tests.webapp.test_routes import Owner, World

ROOT = Path(__file__).resolve().parents[2]
PHASES = {"queued", "rules_check", "sent", "confirming", "done"}


@pytest.fixture
def world(tmp_path, auth_env) -> World:
    return World(tmp_path, auth_env)


@pytest.fixture
def alice(world) -> Owner:
    owner = world.owner()
    owner.tenant.gateway = ScriptGateway()
    return owner


@pytest.fixture
def bob(world) -> Owner:
    owner = world.owner()
    owner.tenant.gateway = ScriptGateway()
    return owner


class HookGateway(ScriptGateway):
    """Runs `hook()` right after answering the proposal of `on_function`."""

    def __init__(self, on_function: str, hook: Any, outcomes: dict[str, str] | None = None):
        super().__init__(outcomes)
        self.on_function, self.hook = on_function, hook

    def propose(self, order: dict[str, Any], *, origin: str = "planner") -> dict[str, Any]:
        record = super().propose(order, origin=origin)
        if order["function"] == self.on_function:
            self.hook()
        return record


class WatchGateway(ScriptGateway):
    """Calls `watch()` before each proposal, with a signing intent held for every order."""

    def __init__(self, watch: Any, *, intent: bool) -> None:
        super().__init__()
        self.watch = watch
        self.intent_store = SimpleNamespace(unfinished=lambda order_id: intent)

    def propose(self, order: dict[str, Any], *, origin: str = "planner") -> dict[str, Any]:
        self.watch()
        return super().propose(order, origin=origin)


def cancel(owner: Owner, job_id: str):
    return owner.post(f"/jobs/{job_id}/cancel")


# ---------------------------------------------------------------- C1 phases


def test_phase_follows_the_job_and_is_done_when_terminal(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = alice.post("/chat", {"message": "Put 10 USDC to work"}).json()["job_id"]
    queued = alice.get(f"/jobs/{job_id}").json()
    assert (queued["status"], queued["phase"]) == ("queued", "queued")

    seen: list[str] = []
    alice.tenant.gateway = WatchGateway(
        lambda: seen.append(alice.get(f"/jobs/{job_id}").json()["phase"]), intent=False
    )
    world.drain()
    assert seen == ["rules_check", "rules_check"]
    job = alice.get(f"/jobs/{job_id}").json()
    assert (job["status"], job["phase"]) == ("succeeded", "done")
    assert {i["phase"] for i in alice.get("/jobs").json()["items"]} == {"done"}
    assert job["phase"] in PHASES


def test_phase_is_sent_while_the_gateway_holds_a_signing_intent(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = alice.post("/chat", {"message": "go"}).json()["job_id"]
    seen: list[str] = []
    alice.tenant.gateway = WatchGateway(
        lambda: seen.append(alice.get(f"/jobs/{job_id}").json()["phase"]), intent=True
    )
    world.drain()
    # The first order has no intent id yet when it is read before `propose` starts; once the
    # gateway is inside the call the order is the one in flight.
    assert seen == ["sent", "sent"]


def test_agent_messages_carry_the_phase(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    say(alice, "Put 10 USDC to work")
    user, agent = conversation(alice)
    assert agent["phase"] == "done"
    assert user["phase"] == "done"


# ---------------------------------------------------------------- C2 cancel


def test_cancel_a_queued_job_never_reaches_the_gateway(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = alice.post("/chat", {"message": "go"}).json()["job_id"]
    resp = cancel(alice, job_id)
    assert resp.status_code == 202
    assert resp.json() == {"job_id": job_id, "status": "cancelling"}
    world.drain()
    job = alice.get(f"/jobs/{job_id}").json()
    assert (job["status"], job["error_code"], job["phase"]) == ("denied", "OWNER_CANCELLED", "done")
    assert alice.tenant.gateway.calls == []
    assert world.env.llm.calls == []
    _user, agent = conversation(alice)
    assert "cancelled" in agent["text"] and "Nothing was sent" in agent["text"]


def test_cancel_between_orders_skips_the_rest(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = alice.post("/chat", {"message": "go"}).json()["job_id"]
    gateway = HookGateway("approve", lambda: cancel(alice, job_id))
    alice.tenant.gateway = gateway
    world.drain()
    assert [o["function"] for o, _m in gateway.calls] == ["approve"]  # supply never proposed
    job = alice.get(f"/jobs/{job_id}").json()
    assert (job["status"], job["error_code"]) == ("denied", "OWNER_CANCELLED")
    _user, agent = conversation(alice)
    first, second = agent["steps"]
    assert first["outcome"] == "executed"
    assert second["outcome"] == "skipped"
    assert second["reason"].endswith(": owner-cancelled")
    assert second["explanation"] == "Not attempted: you cancelled this run."
    assert "I approved 10 USDC for the Aave pool." in agent["text"]
    assert "You cancelled the run, so the next step was not attempted." in agent["text"]


def test_cancel_with_three_orders_skips_each_remaining_one(world, alice):
    orders = [
        order("approve", "aave_pool"),
        order("supply", "self"),
        order("approve", "aave_pool", 5_000_000),
    ]
    world.env.llm = ScriptedClient(plan(*orders))
    job_id = alice.post("/chat", {"message": "go"}).json()["job_id"]
    alice.tenant.gateway = HookGateway("approve", lambda: cancel(alice, job_id))
    world.drain()
    _user, agent = conversation(alice)
    assert [s["outcome"] for s in agent["steps"]] == ["executed", "skipped", "skipped"]
    assert {s["reason"].rsplit(": ", 1)[1] for s in agent["steps"][1:]} == {"owner-cancelled"}
    assert "the next 2 steps were not attempted" in agent["text"]


def test_cancel_after_sent_is_409(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = alice.post("/chat", {"message": "go"}).json()["job_id"]
    codes: list[tuple[int, str]] = []

    def attempt() -> None:
        resp = cancel(alice, job_id)
        codes.append((resp.status_code, resp.json().get("error", "")))

    gateway = WatchGateway(attempt, intent=True)
    alice.tenant.gateway = gateway
    world.drain()
    # An order the gateway holds a signing intent for is past the point of no return.
    assert codes == [(409, "not-cancellable")] * 2
    assert [o["function"] for o, _m in gateway.calls] == ["approve", "supply"]
    assert alice.get(f"/jobs/{job_id}").json()["status"] == "succeeded"


def test_cancel_of_a_finished_job_is_409(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = say(alice, "go").json()["job_id"]
    resp = cancel(alice, job_id)
    assert resp.status_code == 409
    assert resp.json()["type"].endswith(":not-cancellable")
    assert resp.json()["error"] == "not-cancellable"


def test_cancel_of_an_unknown_job_is_404(alice):
    assert cancel(alice, "0" * 32).status_code == 404
    assert cancel(alice, "not-a-job").status_code == 404


def test_cancel_needs_the_csrf_header(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = alice.post("/chat", {"message": "go"}).json()["job_id"]
    assert alice.client.post(f"/jobs/{job_id}/cancel").status_code in (401, 403)
    world.drain()
    assert alice.get(f"/jobs/{job_id}").json()["status"] == "succeeded"


def test_another_owner_cannot_cancel_or_see_the_job(world, alice, bob):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    job_id = alice.post("/chat", {"message": "go"}).json()["job_id"]
    assert cancel(bob, job_id).status_code == 404
    assert bob.get(f"/jobs/{job_id}").status_code == 404
    world.drain()
    assert alice.get(f"/jobs/{job_id}").json()["status"] == "succeeded"  # bob changed nothing
    assert [o["function"] for o, _m in alice.tenant.gateway.calls] == ["approve", "supply"]


def test_jobs_module_still_never_names_the_signer():
    attrs = set()
    for path in (ROOT / "api" / "jobs").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        attrs |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "sign_and_send" not in attrs and "signer" not in attrs


def test_openapi_documents_cancel_and_phase():
    spec = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    post = spec["paths"]["/jobs/{job_id}/cancel"]["post"]
    assert set(post["responses"]) >= {"202", "404", "409"}
    assert "phase" in spec["components"]["schemas"]["Job"]["properties"]
    assert "phase" in spec["components"]["schemas"]["ChatMessage"]["properties"]
