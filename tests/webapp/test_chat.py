"""Chat (ticket 115): POST /chat, GET /chat, the multi-order planner path and its limits.

Everything is in-process: `eth_account` wallets, a fake chain RPC, a scripted LLM and, where the
policy engine is not the subject, a scripted gateway. The real gateway is used for the hostile
message and the T0 denial so that "nothing was signed" is checked at the signer hook.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
import yaml

from core.agent import ScriptedClient
from core.agent.client import LLMError
from core.audit.verify import verify_chain
from core.policy.ips import load_ips
from tests.webapp.conftest import HEADERS, ORIGIN
from tests.webapp.test_jobs import HOSTILE_NOTE
from tests.webapp.test_routes import Owner, World, shape, slug  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
IPS_PATH = ROOT / "contracts" / "examples" / "ips.valid.json"
SPEC = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
TX = "0x" + "c3" * 32


def order(function: str, recipient: str, units: int = 10_000_000, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-15T12:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": function,
        "token": "USDC",
        "amount": {"value": str(units), "decimals": 6},
        "recipient": recipient,
        **extra,
    }


def approve_then_supply(units: int = 10_000_000) -> list[dict[str, Any]]:
    return [order("approve", "aave_pool", units), order("supply", "self", units)]


def plan(*orders: dict[str, Any], reply: str | None = None) -> str:
    body: dict[str, Any] = {"action": "propose", "orders": list(orders)}
    if reply is not None:
        body["reply"] = reply
    return json.dumps(body)


class ScriptGateway:
    """Answers each order with the outcome scripted for its function. Records what it is given."""

    def __init__(self, outcomes: dict[str, str] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.audit_sink: list[dict[str, Any]] = []
        self.policy = SimpleNamespace(ips=load_ips(IPS_PATH))

    def propose(self, order: dict[str, Any], *, origin: str = "planner") -> dict[str, Any]:
        self.calls.append((order, {"origin": origin}))
        outcome = self.outcomes.get(order["function"], "executed")
        record: dict[str, Any] = {
            "seq": len(self.audit_sink),
            "order_id": order["order_id"],
            "order": order,
            "outcome": outcome,
            "decision": None,
            "no_action": None,
            "execution": None,
        }
        if outcome == "executed":
            record["execution"] = {"status": "executed", "tx_hash": TX, "error_code": None}
            record["decision"] = {"decision": "allow", "rules_hit": [], "reason": "ok"}
        elif outcome == "failed":
            record["execution"] = {"status": "failed", "tx_hash": None, "error_code": "TX_REVERTED"}
            record["decision"] = {"decision": "allow", "rules_hit": [], "reason": "ok"}
        elif outcome == "pending_approval":
            record["decision"] = {
                "decision": "escalate",
                "rules_hit": [{"code": "TIER.APPROVAL_REQUIRED"}],
                "reason": "Above the approval threshold",
            }
        else:
            record["decision"] = {
                "decision": "deny",
                "rules_hit": [{"code": "LIMIT.PER_TX"}],
                "reason": "Order exceeds the per-transaction limit",
            }
        self.audit_sink.append(record)
        return record

    def record_no_action(self, reason: str, now: str) -> dict[str, Any]:
        record = {
            "seq": len(self.audit_sink),
            "order_id": None,
            "order": None,
            "decision": None,
            "no_action": {"rules_hit": [], "reason": reason},
            "outcome": "no_action",
            "execution": None,
        }
        self.audit_sink.append(record)
        return record


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


def say(owner: Owner, message: str, *, drain: bool = True):
    resp = owner.post("/chat", {"message": message})
    if drain:
        owner.world.drain()
    return resp


def conversation(owner: Owner) -> list[dict[str, Any]]:
    resp = owner.get("/chat")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    validator = shape("ChatMessage")
    for item in items:
        validator.validate(item)
    return items


# ---------------------------------------------------------------- happy path


def test_multi_order_request_runs_approve_then_supply(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply(), reply="Putting it to work."))
    resp = say(alice, "Put 10 USDC to work in Aave")
    assert resp.status_code == 202 and set(resp.json()) == {"job_id", "status"}
    job = alice.get(f"/jobs/{resp.json()['job_id']}").json()
    assert job["kind"] == "chat" and job["status"] == "succeeded"
    assert job["planner_client"] == "openai"

    user, agent = conversation(alice)
    assert (user["role"], user["text"], user["steps"]) == (
        "user",
        "Put 10 USDC to work in Aave",
        [],
    )
    assert agent["role"] == "agent" and agent["job_id"] == resp.json()["job_id"]
    assert [s["function"] for s in agent["steps"]] == ["approve", "supply"]
    assert {s["outcome"] for s in agent["steps"]} == {"executed"}
    assert all(s["tx_hash"] == TX for s in agent["steps"])
    assert agent["steps"][0]["explanation"] == "Approved 10 USDC for the Aave pool."
    assert "I approved 10 USDC for the Aave pool and supplied 10 USDC to Aave." in agent["text"]
    assert "Transactions: " + TX in agent["text"]
    assert agent["text"].startswith("Putting it to work.")
    assert "Agent says: " not in agent["text"]
    gateway = alice.tenant.gateway
    assert [meta["origin"] for _o, meta in gateway.calls] == ["planner", "planner"]


def test_message_reaches_the_planner_on_its_own_channel(world, alice):
    client = ScriptedClient(plan(*approve_then_supply()))
    world.env.llm = client
    say(alice, "Put 10 USDC to work in Aave")
    payload = json.loads(client.calls[0]["user"])
    assert payload["owner_instruction"] == {
        "channel": "owner_chat",
        "trust": "untrusted_text",
        "text": "Put 10 USDC to work in Aave",
    }
    assert payload["market_facts"] == []


def test_single_order_form_still_works(world, alice):
    one = {"action": "propose", "order": order("supply", "self", 5_000_000)}
    world.env.llm = ScriptedClient(json.dumps(one))
    say(alice, "supply 5 USDC")
    _user, agent = conversation(alice)
    assert [s["function"] for s in agent["steps"]] == ["supply"]


def test_history_is_oldest_first_and_limited(world, alice):
    world.env.llm = ScriptedClient(
        json.dumps({"action": "no_action", "reason": "x", "reply": "ok"})
    )
    for text in ("one", "two", "three"):
        say(alice, text)
    items = conversation(alice)
    assert [m["role"] for m in items] == ["user", "agent"] * 3
    assert [m["text"] for m in items if m["role"] == "user"] == ["one", "two", "three"]
    last = alice.get("/chat?limit=2").json()["items"]
    assert [m["role"] for m in last] == ["user", "agent"]
    assert last[0]["text"] == "three"
    assert alice.get("/chat?limit=0").status_code == 422
    assert alice.get("/chat?limit=201").status_code == 422


# ---------------------------------------------------------------- stop at the first failure


@pytest.mark.parametrize(
    ("outcome", "phrase", "later"),
    [
        ("denied", "I did not act: order exceeds the per-transaction limit.", "blocked"),
        ("failed", "I tried to approve 10 USDC for the Aave pool", "failed"),
        ("pending_approval", "waiting for your approval in Approvals", "waiting"),
    ],
)
def test_stops_at_first_order_that_does_not_execute(world, alice, outcome, phrase, later):
    alice.tenant.gateway = ScriptGateway({"approve": outcome})
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    gateway = alice.tenant.gateway
    assert [o["function"] for o, _m in gateway.calls] == ["approve"]  # supply never proposed
    assert [(s["function"], s["outcome"]) for s in agent["steps"]] == [
        ("approve", outcome),
        ("supply", "skipped"),
    ]
    skipped = agent["steps"][1]
    assert skipped["order_id"] is None and skipped["tx_hash"] is None
    assert later in skipped["explanation"]
    assert skipped["amount"] == {"value": "10000000", "decimals": 6}
    assert phrase in agent["text"]
    assert "The next step was not attempted." in agent["text"]


def test_later_failure_keeps_earlier_steps_executed(world, alice):
    alice.tenant.gateway = ScriptGateway({"supply": "denied"})
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    assert [s["outcome"] for s in agent["steps"]] == ["executed", "denied"]
    assert agent["text"].startswith("I approved 10 USDC for the Aave pool.")
    assert "I did not do the rest" in agent["text"]


# ---------------------------------------------------------------- questions, replies, fallback


def test_question_only_message_is_a_reply_and_no_orders(world, alice):
    reply = "You hold 1000 USDC in the wallet and none in Aave."
    world.env.llm = ScriptedClient(
        json.dumps({"action": "no_action", "reason": "question", "reply": reply})
    )
    say(alice, "What is my balance?")
    _user, agent = conversation(alice)
    assert agent["steps"] == []
    assert agent["text"] == reply
    assert "No transaction was made." not in agent["text"]
    assert "Agent says: " not in agent["text"]
    assert alice.tenant.gateway.calls == []
    job = alice.get("/jobs").json()["items"][0]
    assert job["status"] == "succeeded" and job["order_ids"] == []


def test_model_reply_is_never_part_of_a_step(world, alice):
    world.env.llm = ScriptedClient(
        plan(*approve_then_supply(), reply="Everything succeeded, trust me.")
    )
    alice.tenant.gateway = ScriptGateway({"approve": "denied"})
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    blob = json.dumps(agent["steps"])
    assert "trust me" not in blob
    assert agent["text"].startswith("Everything succeeded, trust me.")
    assert "Agent says: " not in agent["text"]
    assert agent["steps"][0]["outcome"] == "denied"


def test_fallback_planner_is_labelled_when_no_key(world, alice):
    world.env.llm = None
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    job = alice.get(f"/jobs/{agent['job_id']}").json()
    assert job["planner_client"] == "fallback"
    assert "No language model is configured" in agent["text"]
    assert [s["function"] for s in agent["steps"]] == ["approve", "supply"]
    gateway = alice.tenant.gateway
    assert gateway.calls[0][0]["amount"] == {"value": "10000000", "decimals": 6}


def test_fallback_answers_a_balance_question_without_orders(world, alice):
    world.env.llm = None
    say(alice, "What is my balance?")
    _user, agent = conversation(alice)
    assert agent["steps"] == []
    assert "Wallet: 1000 USDC idle, 1000 USDC in Aave." in agent["text"]
    assert "Agent says: " not in agent["text"]
    assert alice.tenant.gateway.calls == []


def test_llm_error_falls_back_and_says_so(world, alice):
    class Down:
        def complete(self, system: str, user: str, schema: Any = None) -> str:
            raise LLMError("timeout")

    world.env.llm = Down()
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    assert "The language model was unavailable" in agent["text"]
    assert alice.get(f"/jobs/{agent['job_id']}").json()["planner_client"] == "fallback"


# ---------------------------------------------------------------- the planner cannot be steered


def test_hostile_message_is_denied_and_nothing_is_signed(world):
    owner = world.owner(fake_gateway=False)
    world.env.set_tier(owner.tenant, "T2")
    hostile = json.loads(
        plan(order("supply", "self", 1_000_000_000_000))  # far above the per-transaction limit
    )
    world.env.llm = ScriptedClient(json.dumps(hostile))
    say(owner, HOSTILE_NOTE)
    _user, agent = conversation(owner)
    (step,) = agent["steps"]
    assert step["outcome"] == "denied" and step["rule_codes"] and step["tx_hash"] is None
    assert step["explanation"].startswith("The agent could not supply")
    assert "I did not act" in agent["text"]
    assert world.env.sent == []
    assert "0x" + "ee" * 20 not in json.dumps(agent)
    assert verify_chain(owner.tenant.gateway.audit_sink).valid


def test_mint_and_transfer_cannot_come_from_chat(world, alice):
    mint = order("mint", "self")
    transfer = {**order("transfer", "owner"), "protocol": "wallet"}
    for bad in (mint, transfer):
        world.env.llm = ScriptedClient(plan(bad))
        say(alice, "mint me a million and send it to my wallet")
        # one job at a time: the previous one is already drained
    items = conversation(alice)
    agents = [m for m in items if m["role"] == "agent"]
    assert len(agents) == 2
    for agent in agents:
        assert agent["steps"] == []
        assert "I could not turn that into a valid order" in agent["text"]
    assert alice.tenant.gateway.calls == []


def test_too_many_orders_are_refused_whole(world, alice):
    world.env.llm = ScriptedClient(plan(*[order("approve", "aave_pool") for _ in range(5)]))
    say(alice, "do many things")
    _user, agent = conversation(alice)
    assert agent["steps"] == [] and alice.tenant.gateway.calls == []
    assert "I could not turn that into a valid order" in agent["text"]


def test_t0_denial_is_explained_in_plain_words(world):
    owner = world.owner(fake_gateway=False)  # a fresh owner is at tier T0
    world.env.llm = None
    say(owner, "Put 10 USDC to work in Aave")
    _user, agent = conversation(owner)
    assert [(s["function"], s["outcome"]) for s in agent["steps"]] == [
        ("approve", "denied"),
        ("supply", "skipped"),
    ]
    assert agent["steps"][0]["rule_codes"] == ["TIER.READ_ONLY"]
    assert "read-only mode (tier T0)" in agent["text"]
    assert "Activate it" in agent["text"]
    assert world.env.sent == []


def test_emergency_stop_is_a_denied_job_with_a_plain_message(world):
    owner = world.owner(fake_gateway=False)
    world.env.set_tier(owner.tenant, "T2")
    world.env.stop_owner(owner.tenant)
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    resp = say(owner, "Put 10 USDC to work in Aave")
    job = owner.get(f"/jobs/{resp.json()['job_id']}").json()
    assert (job["status"], job["error_code"]) == ("denied", "TIER.EMERGENCY_STOP")
    _user, agent = conversation(owner)
    assert "the emergency stop is on" in agent["text"]
    assert world.env.sent == []


def test_gas_below_floor_gets_a_plain_message(world, alice):
    world.env.chain.eth[alice.tenant.agent_address.lower()] = 0
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    resp = say(alice, "Put 10 USDC to work in Aave")
    job = alice.get(f"/jobs/{resp.json()['job_id']}").json()
    assert (job["status"], job["error_code"]) == ("failed", "GAS_BELOW_FLOOR")
    _user, agent = conversation(alice)
    assert (
        agent["text"]
        == "I did not act: the agent wallet has too little ETH for gas. Nothing was sent."
    )


# ---------------------------------------------------------------- limits, auth, isolation


def test_second_message_while_one_is_active_is_409(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    first = say(alice, "one", drain=False)
    second = alice.post("/chat", {"message": "two"})
    assert second.status_code == 409 and slug(second) == "job-in-progress"
    assert second.json()["job_id"] == first.json()["job_id"]
    assert len(conversation(alice)) == 1  # the refused message was not stored
    world.drain()


def test_chat_and_runs_share_one_hourly_limit(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    for i in range(6):
        if i % 2:
            assert say(alice, f"action {i}").status_code == 202
        else:
            resp = alice.post("/runs", {})
            assert resp.status_code == 202
            world.drain()
    refused = alice.post("/chat", {"message": "one more"})
    assert refused.status_code == 429 and slug(refused) == "rate-limited"
    assert int(refused.headers["Retry-After"]) >= 1
    assert alice.post("/runs", {}).status_code == 429
    assert len(conversation(alice)) == 6  # three chats, user and agent each


def test_ten_consecutive_questions_do_not_return_429(world, alice):
    world.env.llm = ScriptedClient(
        json.dumps(
            {
                "action": "no_action",
                "reason": "question",
                "reply": "Your balance is 1000 USDC.",
            }
        )
    )
    for i in range(10):
        resp = say(alice, f"Question {i}")
        assert resp.status_code == 202
    items = conversation(alice)
    assert len(items) == 20  # 10 user + 10 agent messages


def test_seventh_action_chat_in_an_hour_returns_429(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    for i in range(6):
        resp = say(alice, f"action {i}")
        assert resp.status_code == 202
    seventh = alice.post("/chat", {"message": "action 7"})
    assert seventh.status_code == 429 and slug(seventh) == "rate-limited"
    assert int(seventh.headers["Retry-After"]) >= 1


# ---------------------------------------------------------------- 8 audit situations


def test_audit_situation_1_question(world, alice):
    reply = "You hold 1000 USDC idle and 500 USDC in Aave."
    world.env.llm = ScriptedClient(
        json.dumps({"action": "no_action", "reason": "question", "reply": reply})
    )
    say(alice, "What is my balance?")
    _user, agent = conversation(alice)
    assert agent["steps"] == []
    assert agent["text"] == reply
    assert "Agent says: " not in agent["text"]
    assert "No transaction was made." not in agent["text"]
    assert alice.tenant.gateway.calls == []
    job = alice.get(f"/jobs/{agent['job_id']}").json()
    assert job["status"] == "succeeded" and job["order_ids"] == []


def test_audit_situation_2_action_executed(world, alice):
    world.env.llm = ScriptedClient(plan(*approve_then_supply(), reply="Putting 10 USDC to work."))
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    assert len(agent["steps"]) == 2
    assert all(s["outcome"] == "executed" for s in agent["steps"])
    assert agent["text"].startswith("Putting 10 USDC to work.")
    assert "I approved 10 USDC for the Aave pool and supplied 10 USDC to Aave." in agent["text"]
    assert "Agent says: " not in agent["text"]


def test_audit_situation_3_blocked(world, alice):
    alice.tenant.gateway = ScriptGateway({"approve": "denied"})
    world.env.llm = ScriptedClient(plan(*approve_then_supply(), reply="Supplying now."))
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    assert agent["steps"][0]["outcome"] == "denied"
    assert agent["text"].startswith("Supplying now.")
    assert "I did not act: order exceeds the per-transaction limit." in agent["text"]
    assert "Agent says: " not in agent["text"]


def test_audit_situation_4_needs_approval(world, alice):
    alice.tenant.gateway = ScriptGateway({"approve": "pending_approval"})
    world.env.llm = ScriptedClient(plan(*approve_then_supply(), reply="Starting supply."))
    say(alice, "Put 10 USDC to work in Aave")
    _user, agent = conversation(alice)
    assert agent["steps"][0]["outcome"] == "pending_approval"
    assert agent["text"].startswith("Starting supply.")
    assert "waiting for your approval in Approvals" in agent["text"]
    assert "Agent says: " not in agent["text"]


def test_audit_situation_5_ambiguous(world, alice):
    clarification = "Did you mean deposit into Aave or hold idle USDC?"
    world.env.llm = ScriptedClient(
        json.dumps({"action": "no_action", "reason": "clarify", "reply": clarification})
    )
    say(alice, "invest my money")
    _user, agent = conversation(alice)
    assert agent["steps"] == []
    assert agent["text"] == clarification
    assert "Agent says: " not in agent["text"]
    assert "No transaction was made." not in agent["text"]
    assert alice.tenant.gateway.calls == []


def test_audit_situation_6_hostile_refusal(world):
    owner = world.owner(fake_gateway=False)
    world.env.set_tier(owner.tenant, "T2")
    hostile = json.loads(plan(order("supply", "self", 1_000_000_000_000)))
    world.env.llm = ScriptedClient(json.dumps(hostile))
    say(owner, HOSTILE_NOTE)
    _user, agent = conversation(owner)
    assert agent["steps"][0]["outcome"] == "denied"
    assert "I did not act" in agent["text"]
    assert world.env.sent == []


def test_audit_situation_7_stop(world):
    owner = world.owner(fake_gateway=False)
    world.env.set_tier(owner.tenant, "T2")
    world.env.stop_owner(owner.tenant)
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    resp = say(owner, "Put 10 USDC to work in Aave")
    job = owner.get(f"/jobs/{resp.json()['job_id']}").json()
    assert (job["status"], job["error_code"]) == ("denied", "TIER.EMERGENCY_STOP")
    _user, agent = conversation(owner)
    assert "the emergency stop is on" in agent["text"]
    assert world.env.sent == []


def test_audit_situation_8_error(world, alice):
    world.env.chain.eth[alice.tenant.agent_address.lower()] = 0
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    resp = say(alice, "Put 10 USDC to work in Aave")
    job = alice.get(f"/jobs/{resp.json()['job_id']}").json()
    assert (job["status"], job["error_code"]) == ("failed", "GAS_BELOW_FLOOR")
    _user, agent = conversation(alice)
    assert "the agent wallet has too little ETH for gas" in agent["text"]


@pytest.mark.parametrize("message", ["", "   ", "x" * 501])
def test_message_length_is_validated(world, alice, message):
    assert alice.post("/chat", {"message": message}).status_code == 422
    assert conversation(alice) == []


def test_unknown_field_is_refused(world, alice):
    assert alice.post("/chat", {"message": "hi", "owner": "0x" + "1" * 40}).status_code == 422


def test_message_of_500_characters_is_accepted(world, alice):
    world.env.llm = ScriptedClient(json.dumps({"action": "no_action", "reason": "x"}))
    assert say(alice, "y" * 500).status_code == 202


def test_chat_needs_a_session_and_the_csrf_header(world, alice):
    from fastapi.testclient import TestClient

    anon = TestClient(world.app, base_url=ORIGIN)
    assert anon.get("/chat").status_code == 401
    assert anon.post("/chat", json={"message": "hi"}, headers=HEADERS).status_code == 401
    no_csrf = alice.client.post("/chat", json={"message": "hi"})
    assert no_csrf.status_code == 403 and slug(no_csrf) == "csrf"


def test_owners_never_see_each_others_conversation(world, alice, bob):
    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    resp = say(alice, "Put 10 USDC to work in Aave")
    assert bob.get("/chat").json() == {"items": []}
    assert bob.get(f"/jobs/{resp.json()['job_id']}").status_code == 404
    assert len(conversation(alice)) == 2


def test_owner_comes_from_the_session_only(world, alice, bob):
    world.env.llm = ScriptedClient(json.dumps({"action": "no_action", "reason": "x"}))
    owner_b = bob.wallet.address.lower()
    resp = alice.client.post(
        f"/chat?owner={owner_b}", json={"message": "hi"}, headers={**HEADERS, "X-Owner": owner_b}
    )
    assert resp.status_code == 202
    world.drain()
    assert bob.get("/chat").json() == {"items": []}
    assert len(conversation(alice)) == 2


def test_chat_job_row_and_table_hold_no_order_body(world, alice, tmp_path):
    import sqlite3

    world.env.llm = ScriptedClient(plan(*approve_then_supply()))
    say(alice, "Put 10 USDC to work in Aave")
    db = alice.tenant.paths.root if hasattr(alice.tenant.paths, "root") else None
    path = (db or alice.tenant.paths.state.parent) / "webapp.sqlite"
    with sqlite3.connect(path) as conn:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(chat_messages)")]
    assert cols == ["seq", "id", "role", "text", "job_id", "created_at", "thread_id"]


def test_openapi_documents_the_chat_routes():
    assert set(SPEC["paths"]["/chat"]) == {"post", "get"}
    jsonschema.Draft202012Validator.check_schema(
        {"$ref": "#/components/schemas/ChatMessage", "components": SPEC["components"]}
    )


# ---------------------------------------------------------------- threads (ticket 123, C3)


def threads(owner: Owner) -> list[dict[str, Any]]:
    resp = owner.get("/chat/threads")
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def test_new_thread_is_empty_and_titled_new_chat(world, alice):
    resp = alice.post("/chat/threads", {})
    assert resp.status_code == 201
    created = resp.json()
    assert created["title"] == "New chat" and created["id"]
    assert alice.get(f"/chat?thread_id={created['id']}").json() == {"items": []}
    assert [t["id"] for t in threads(alice)] == [created["id"]]
    assert alice.post("/chat/threads", {"title": "x"}).status_code == 422


def test_messages_go_to_their_thread_and_the_title_is_set_by_code(world, alice):
    world.env.llm = ScriptedClient(json.dumps({"action": "no_action", "reason": "x"}))
    tid = alice.post("/chat/threads", {}).json()["id"]
    long_text = "Please put ten USDC to work in Aave right now, thanks a lot"
    resp = alice.post("/chat", {"message": long_text, "thread_id": tid})
    assert resp.status_code == 202
    world.drain()
    say(alice, "default thread message")
    inside = alice.get(f"/chat?thread_id={tid}").json()["items"]
    assert [m["role"] for m in inside] == ["user", "agent"]
    assert inside[0]["text"] == long_text
    assert [m["text"] for m in conversation(alice) if m["role"] == "user"] == [
        "default thread message"
    ]
    listed = {t["id"]: t for t in threads(alice)}
    assert listed[tid]["title"] == long_text[:40]
    assert listed[tid]["status"] in {"done", "blocked"}
    assert "default" in listed
    assert listed["default"]["title"] == "default thread message"


def test_thread_status_follows_the_latest_job(world, alice):
    world.env.llm = ScriptedClient(json.dumps({"action": "no_action", "reason": "x"}))
    tid = alice.post("/chat/threads", {}).json()["id"]
    alice.post("/chat", {"message": "hello", "thread_id": tid})
    world.drain()
    job_id = alice.get(f"/chat?thread_id={tid}").json()["items"][0]["job_id"]
    assert threads(alice)[0]["status"] == "done"
    job = alice.tenant.stores.jobs
    job.update(job_id, status="failed")
    assert threads(alice)[0]["status"] == "blocked"


def test_threads_are_newest_first_and_limited(world, alice):
    world.env.llm = ScriptedClient(json.dumps({"action": "no_action", "reason": "x"}))
    ids = []
    for i in range(3):
        tid = alice.post("/chat/threads", {}).json()["id"]
        ids.append(tid)
        alice.post("/chat", {"message": f"message in {i}", "thread_id": tid})
        world.drain()
    assert {t["id"] for t in threads(alice)} == set(ids)
    assert len(alice.get("/chat/threads?limit=2").json()["items"]) == 2
    assert alice.get("/chat/threads?limit=0").status_code == 422


def test_post_thread_idempotency_empty_reuse_and_non_empty_creates(world, alice, bob):
    world.env.llm = ScriptedClient(json.dumps({"action": "no_action", "reason": "x"}))
    r1 = alice.post("/chat/threads", {})
    assert r1.status_code == 201
    tid1 = r1.json()["id"]
    assert r1.json()["title"] == "New chat"

    r2 = alice.post("/chat/threads", {})
    assert r2.status_code == 200
    assert r2.json() == {"id": tid1, "title": "New chat"}
    assert [t["id"] for t in threads(alice)] == [tid1]

    alice.post("/chat", {"message": "hello", "thread_id": tid1})
    world.drain()

    r3 = alice.post("/chat/threads", {})
    assert r3.status_code == 201
    tid2 = r3.json()["id"]
    assert tid2 != tid1
    assert r3.json()["title"] == "New chat"

    r4 = alice.post("/chat/threads", {})
    assert r4.status_code == 200
    assert r4.json() == {"id": tid2, "title": "New chat"}

    rbob = bob.post("/chat/threads", {})
    assert rbob.status_code == 201
    tid_bob = rbob.json()["id"]
    assert tid_bob not in (tid1, tid2)
    assert [t["id"] for t in threads(bob)] == [tid_bob]


def test_unknown_thread_is_404_and_nothing_is_started(world, alice):
    resp = alice.post("/chat", {"message": "hi", "thread_id": "f" * 32})
    assert resp.status_code == 404 and slug(resp) == "not-found"
    assert alice.get("/chat?thread_id=" + "f" * 32).status_code == 404
    world.drain()
    assert conversation(alice) == []


def test_another_owners_thread_is_unknown(world, alice, bob):
    world.env.llm = ScriptedClient(json.dumps({"action": "no_action", "reason": "x"}))
    tid = alice.post("/chat/threads", {}).json()["id"]
    alice.post("/chat", {"message": "private", "thread_id": tid})
    world.drain()
    assert threads(bob) == []
    assert bob.get(f"/chat?thread_id={tid}").status_code == 404
    resp = bob.post("/chat", {"message": "hi", "thread_id": tid})
    assert resp.status_code == 404
    assert len(alice.get(f"/chat?thread_id={tid}").json()["items"]) == 2


def test_threads_need_a_session_and_the_csrf_header(world, alice):
    from fastapi.testclient import TestClient

    anon = TestClient(world.app, base_url=ORIGIN)
    assert anon.get("/chat/threads").status_code == 401
    no_csrf = alice.client.post("/chat/threads", json={})
    assert no_csrf.status_code == 403 and slug(no_csrf) == "csrf"
