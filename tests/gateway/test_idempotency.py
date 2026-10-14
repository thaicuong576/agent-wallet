"""Audit-backed idempotency: restart, crash between sign and append, body mismatch."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.audit.sink import SqliteAuditSink
from core.gateway.exceptions import OrderIdConflict, ReconcileRequired
from core.gateway.gateway import Gateway
from core.gateway.policy import EnginePolicy

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples"

STATE = {
    "schema_version": 1,
    "positions": [
        {
            "token": "USDC",
            "protocol": "wallet",
            "amount": {"value": "1000000000", "decimals": 6},
            "price_usd": "1.00",
            "usd_value": "1000.00",
        }
    ],
    "total_value_usd": "1000.00",
    "spent_24h_usd": "0",
    "tx_count_1h": 0,
    "as_of": "2026-10-14T10:00:00Z",
}


@dataclass
class _SimResult:
    ok: bool = True
    error: str | None = None
    gas_used: int = 50000


class _Simulator:
    def simulate(self, call: dict[str, Any]) -> _SimResult:
        return _SimResult()


class _Signer:
    def __init__(self) -> None:
        self.calls = 0

    def sign_and_send(self, call: dict[str, Any]) -> str:
        self.calls += 1
        return "0x" + "c" * 64


class _AppendCrashSink(SqliteAuditSink):
    """Audit sink whose append fails, simulating a crash after signing."""

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("simulated crash between sign and append")


def _json(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


def _gateway(sink: SqliteAuditSink, signer: _Signer) -> Gateway:
    policy = EnginePolicy(
        ips=_json("ips.valid.json"),
        get_state=lambda: STATE,
        get_risk_inputs=lambda: _json("risk-inputs.valid.json"),
    )
    return Gateway(
        policy=policy,
        simulator=_Simulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=lambda o: [{"to": "0x1111111111111111111111111111111111111111", "data": "0x"}],
    )


def _order(order_id: str, amount: str = "10000000") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": order_id,
        "created_at": "2026-10-14T10:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": amount, "decimals": 6},
        "recipient": "self",
    }


def test_restart_same_order_id_no_second_sign(tmp_path: Path) -> None:
    db = tmp_path / "audit.sqlite"
    order = _order(str(uuid.uuid4()))

    sink1 = SqliteAuditSink(db)
    signer1 = _Signer()
    first = _gateway(sink1, signer1).propose(order)
    sink1.close()
    assert first["outcome"] == "executed"
    assert signer1.calls == 1

    # "Restart": brand-new sink, gateway and signer on the same files.
    sink2 = SqliteAuditSink(db)
    signer2 = _Signer()
    second = _gateway(sink2, signer2).propose(order)
    assert second["outcome"] == "executed"
    assert second["seq"] == first["seq"]
    assert signer2.calls == 0
    assert len(sink2) == 1
    sink2.close()


def test_append_raises_then_restart_reconcile_required(tmp_path: Path) -> None:
    db = tmp_path / "audit.sqlite"
    order = _order(str(uuid.uuid4()))

    crash_sink = _AppendCrashSink(db)
    signer1 = _Signer()
    with pytest.raises(RuntimeError, match="simulated crash"):
        _gateway(crash_sink, signer1).propose(order)
    crash_sink.close()
    assert signer1.calls == 1

    sink2 = SqliteAuditSink(db)
    signer2 = _Signer()
    with pytest.raises(ReconcileRequired):
        _gateway(sink2, signer2).propose(order)
    assert signer2.calls == 0
    # ReconcileRequired never writes a synthetic record.
    assert len(sink2) == 0
    sink2.close()


def test_body_mismatch_conflict(tmp_path: Path) -> None:
    db = tmp_path / "audit.sqlite"
    order_id = str(uuid.uuid4())

    sink = SqliteAuditSink(db)
    signer = _Signer()
    gw = _gateway(sink, signer)
    assert gw.propose(_order(order_id))["outcome"] == "executed"

    with pytest.raises(OrderIdConflict):
        gw.propose(_order(order_id, amount="20000000"))
    assert signer.calls == 1
    assert len(sink) == 1
    sink.close()
