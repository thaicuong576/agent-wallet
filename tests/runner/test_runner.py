"""Runner.run_once: Reader, Planner, portfolio and gateway composition."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.agent import Planner, Reader, ScriptedClient
from core.audit.sink import SqliteAuditSink
from core.audit.verify import verify_chain
from core.gateway.exceptions import ReconcileRequired
from core.gateway.gateway import Gateway
from core.gateway.policy import EnginePolicy
from core.policy.ips import load_ips
from core.runner.ledger import AuditLedger
from core.runner.portfolio import PortfolioReader
from core.runner.runner import Runner
from core.signer import FakeSigner
from core.simulate import SimulationResult

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples"
CORPUS = Path(__file__).resolve().parents[1] / "agent" / "fixtures" / "injection_corpus.json"
WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"
NOW = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)


class _Sim:
    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        return SimulationResult(ok=True, error=None, gas_used=50000)

    def simulate_bundle(self, txs: list[dict[str, Any]]) -> SimulationResult:
        return SimulationResult(ok=True, error=None, gas_used=50000)


def _rpc(method: str, params: list[Any]) -> str:
    return hex(1_000_000_000)  # 1000 USDC / aUSDC, and a large gas balance


def _broken_rpc(method: str, params: list[Any]) -> str:
    raise RuntimeError("node down")


def make_order(amount: str = "10000000", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-14T10:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": amount, "decimals": 6},
        "recipient": "self",
    }
    body.update(overrides)
    return body


def propose_json(*orders: dict[str, Any]) -> str:
    return json.dumps({"action": "propose", "orders": list(orders)})


class Stack:
    def __init__(self, tmp_path: Path, response: str, rpc: Any = _rpc) -> None:
        self.audit_file = tmp_path / "audit.sqlite"
        self.signer = FakeSigner()
        self.sink = SqliteAuditSink(self.audit_file)
        self.ledger = AuditLedger(self.audit_file)
        self.portfolio = PortfolioReader(rpc, WALLET, self.ledger)
        self.ips = load_ips(EXAMPLES_DIR / "ips.valid.json")
        self.client = ScriptedClient(response)
        risk = json.loads((EXAMPLES_DIR / "risk-inputs.valid.json").read_text(encoding="utf-8"))
        self.policy = EnginePolicy(
            ips=self.ips,
            get_state=lambda: self.portfolio.read(NOW).state,
            get_risk_inputs=lambda: risk,
        )
        self.gateway = self.make_gateway(self.sink)
        self.runner = Runner(
            Reader(), Planner(self.client), self.portfolio, self.gateway, self.ips, lambda: NOW
        )

    def make_gateway(self, sink: SqliteAuditSink) -> Gateway:
        return Gateway(
            policy=self.policy,
            simulator=_Sim(),
            signer=self.signer,
            audit_sink=sink,
            tx_builder=lambda o: [
                {"to": "0x1111111111111111111111111111111111111111", "data": "0x"}
            ],
            tier="T2",
        )


def test_planner_order_executes_through_fake_signer(tmp_path: Path) -> None:
    stack = Stack(tmp_path, propose_json(make_order()))
    records = stack.runner.run_once("USDC supply rates look steady.", NOW)
    assert [r["outcome"] for r in records] == ["executed"]
    assert len(stack.signer.calls) == 1
    assert records[0]["execution"]["tx_hash"].startswith("0x")


def test_two_orders_proposed_in_planner_order(tmp_path: Path) -> None:
    first, second = make_order(), make_order(amount="20000000")
    stack = Stack(tmp_path, propose_json(first, second))
    records = stack.runner.run_once("", NOW)
    assert [r["order_id"] for r in records] == [first["order_id"], second["order_id"]]
    assert len(stack.signer.calls) == 2


def test_no_action_recorded_through_gateway(tmp_path: Path) -> None:
    stack = Stack(tmp_path, json.dumps({"action": "no_action", "reason": "balanced"}))
    records = stack.runner.run_once("quiet market", NOW)
    assert len(records) == 1
    assert records[0]["outcome"] == "no_action"
    assert "balanced" in records[0]["no_action"]["reason"]
    assert stack.signer.calls == []
    assert len(stack.sink) == 1


def test_invalid_planner_output_is_no_action_with_errors(tmp_path: Path) -> None:
    stack = Stack(tmp_path, propose_json(make_order(recipient="0x" + "2" * 40)))
    records = stack.runner.run_once("x", NOW)
    assert [r["outcome"] for r in records] == ["no_action"]
    assert stack.signer.calls == []


def test_policy_denial_recorded_by_gateway_and_returned(tmp_path: Path) -> None:
    stack = Stack(tmp_path, propose_json(make_order(amount="150000000")))
    records = stack.runner.run_once("x", NOW)
    assert [r["outcome"] for r in records] == ["denied"]
    codes = {h["code"] for h in records[0]["decision"]["rules_hit"]}
    assert "LIMIT.PER_TX" in codes
    assert stack.signer.calls == []
    assert len(stack.sink) == 1


def test_portfolio_unavailable_records_no_action_and_proposes_nothing(tmp_path: Path) -> None:
    stack = Stack(tmp_path, propose_json(make_order()), rpc=_broken_rpc)
    records = stack.runner.run_once("x", NOW)
    assert [r["outcome"] for r in records] == ["no_action"]
    assert records[0]["no_action"]["reason"] == "portfolio unavailable"
    assert stack.client.calls == []
    assert stack.signer.calls == []


def test_runner_has_no_audit_write_path(tmp_path: Path) -> None:
    stack = Stack(tmp_path, propose_json(make_order(), make_order(amount="150000000")))
    records = stack.runner.run_once("x", NOW)
    assert len(stack.sink) == len(records) == 2


def test_reconcile_required_propagates(tmp_path: Path) -> None:
    body = make_order()

    class _Crash(SqliteAuditSink):
        def append(self, record: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("crash after sign")

    crashing = Stack(tmp_path, propose_json(body))
    crashing.gateway.audit_sink = _Crash(crashing.audit_file)
    with pytest.raises(RuntimeError):
        crashing.runner.run_once("x", NOW)
    assert len(crashing.signer.calls) == 1

    restarted = Stack(tmp_path, propose_json(body))
    with pytest.raises(ReconcileRequired):
        restarted.runner.run_once("x", NOW)
    assert restarted.signer.calls == []


def test_repeated_order_id_executes_once(tmp_path: Path) -> None:
    stack = Stack(tmp_path, propose_json(make_order()))
    stack.runner.run_once("x", NOW)
    again = stack.runner.run_once("x", NOW)
    assert again[0]["outcome"] == "executed"
    assert len(stack.signer.calls) == 1
    assert len(stack.sink) == 1


def test_chain_written_by_run_verifies(tmp_path: Path) -> None:
    stack = Stack(tmp_path, propose_json(make_order(), make_order(amount="150000000")))
    stack.runner.run_once("x", NOW)
    assert verify_chain(stack.sink).valid


def test_hostile_reader_input_never_executes_outside_universe(tmp_path: Path) -> None:
    # A compromised planner proposes WETH, which has no reference price: the engine fails closed.
    hostile = make_order(token="WETH", amount={"value": "1000000000000000000", "decimals": 18})
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus
    for item in corpus:
        sub = tmp_path / item["id"]
        sub.mkdir()
        stack = Stack(sub, propose_json(hostile))
        records = stack.runner.run_once(item["hostile_text"], NOW)
        assert stack.signer.calls == [], item["id"]
        assert all(r["outcome"] != "executed" for r in records), item["id"]
