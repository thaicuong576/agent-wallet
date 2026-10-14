"""An emergency stop engaged through the API denies the runner's next run_once."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.agent import Planner, Reader, ScriptedClient
from core.audit.sink import SqliteAuditSink
from core.gateway.gateway import Gateway
from core.gateway.policy import EnginePolicy
from core.policy.ips import load_ips
from core.runner.ledger import AuditLedger
from core.runner.portfolio import PortfolioReader
from core.runner.runner import Runner
from core.signer import FakeSigner
from core.simulate import SimulationResult
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from core.tiers.view import TierView

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples"
WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"
NOW = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
TOKEN = "e2e-owner-token"


class _Sim:
    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        return SimulationResult(ok=True, error=None, gas_used=50000)

    def simulate_bundle(self, txs: list[dict[str, Any]]) -> SimulationResult:
        return SimulationResult(ok=True, error=None, gas_used=50000)


def _planner_response(system: str, user: str, schema: Any) -> str:
    body = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-14T10:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "10000000", "decimals": 6},
        "recipient": "self",
    }
    return json.dumps({"action": "propose", "order": body})


def test_api_stop_denies_next_run_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WALLET_OWNER_TOKEN", TOKEN)
    state_file = tmp_path / "state.sqlite"
    audit_file = tmp_path / "audit.sqlite"

    # Runner side: its own TierStore instance on the shared file, read through a TierView.
    runner_store = TierStore(state_file)
    OwnerControls(runner_store).set_tier("T2")
    sink = SqliteAuditSink(audit_file)
    signer = FakeSigner()
    ips = load_ips(EXAMPLES_DIR / "ips.valid.json")
    risk = json.loads((EXAMPLES_DIR / "risk-inputs.valid.json").read_text(encoding="utf-8"))
    portfolio = PortfolioReader(lambda m, p: hex(1_000_000_000), WALLET, AuditLedger(audit_file))
    gateway = Gateway(
        policy=EnginePolicy(
            ips=ips,
            get_state=lambda: portfolio.read(NOW).state,
            get_risk_inputs=lambda: risk,
        ),
        simulator=_Sim(),
        signer=signer,
        audit_sink=sink,
        tx_builder=lambda o: [{"to": "0x1111111111111111111111111111111111111111", "data": "0x"}],
        tier_view=TierView(runner_store),
    )
    runner = Runner(
        Reader(), Planner(ScriptedClient(_planner_response)), portfolio, gateway, ips, lambda: NOW
    )

    first = runner.run_once("steady market", NOW)
    assert [r["outcome"] for r in first] == ["executed"]
    assert len(signer.calls) == 1

    # API side: a second TierStore instance on the same file, engaged through the test client.
    api_store = TierStore(state_file)
    app = create_app(
        gateway=Gateway(tier="T2", audit_sink=sink),
        tier_view=TierView(api_store),
        owner_controls=OwnerControls(api_store),
        audit_sink=sink,
    )
    with TestClient(app) as client:
        resp = client.post("/emergency-stop", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200

    second = runner.run_once("steady market", NOW)
    assert [r["outcome"] for r in second] == ["denied"]
    codes = {h["code"] for h in second[0]["decision"]["rules_hit"]}
    assert "TIER.EMERGENCY_STOP" in codes
    assert len(signer.calls) == 1
