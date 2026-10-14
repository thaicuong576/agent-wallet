"""The audit chain written by Runner.run_once verifies."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.agent import Planner, Reader, ScriptedClient
from core.audit.sink import SqliteAuditSink
from core.audit.verify import verify_chain
from core.gateway.gateway import Gateway
from core.gateway.policy import EnginePolicy
from core.policy.ips import load_ips
from core.runner.ledger import AuditLedger
from core.runner.portfolio import PortfolioReader
from core.runner.runner import Runner
from core.signer import FakeSigner
from core.simulate import SimulationResult

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples"
WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"
NOW = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)


class _Sim:
    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        return SimulationResult(ok=True, error=None, gas_used=50000)

    def simulate_bundle(self, txs: list[dict[str, Any]]) -> SimulationResult:
        return SimulationResult(ok=True, error=None, gas_used=50000)


def _order(amount: str) -> dict[str, Any]:
    return {
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


def test_run_once_chain_verifies(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.sqlite"
    sink = SqliteAuditSink(audit_file)
    ips = load_ips(EXAMPLES_DIR / "ips.valid.json")
    risk = json.loads((EXAMPLES_DIR / "risk-inputs.valid.json").read_text(encoding="utf-8"))
    portfolio = PortfolioReader(lambda m, p: hex(1_000_000_000), WALLET, AuditLedger(audit_file))
    gateway = Gateway(
        policy=EnginePolicy(
            ips=ips, get_state=lambda: portfolio.read(NOW).state, get_risk_inputs=lambda: risk
        ),
        simulator=_Sim(),
        signer=FakeSigner(),
        audit_sink=sink,
        tx_builder=lambda o: [{"to": "0x1111111111111111111111111111111111111111", "data": "0x"}],
        tier="T2",
    )
    responses = [
        json.dumps({"action": "propose", "orders": [_order("10000000"), _order("150000000")]}),
        json.dumps({"action": "no_action", "reason": "balanced"}),
    ]
    runner = Runner(
        Reader(), Planner(ScriptedClient(responses)), portfolio, gateway, ips, lambda: NOW
    )

    first = runner.run_once("x", NOW)
    second = runner.run_once("x", NOW)

    assert [r["outcome"] for r in first] == ["executed", "denied"]
    assert [r["outcome"] for r in second] == ["no_action"]
    report = verify_chain(sink)
    assert report.valid
    assert report.count == 3
