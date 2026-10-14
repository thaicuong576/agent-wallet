"""Tests for GatewayLock serialization and timeout handling."""

from __future__ import annotations

import json
import multiprocessing as mp
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.audit.sink import SqliteAuditSink
from core.gateway.exceptions import GatewayBusy
from core.gateway.gateway import Gateway
from core.gateway.lock import GatewayLock
from core.gateway.policy import EnginePolicy
from core.runner.ledger import AuditLedger

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples"


def _load_ips() -> dict[str, Any]:
    ips = json.loads((EXAMPLES_DIR / "ips.valid.json").read_text(encoding="utf-8"))
    ips["limits"]["per_day_usd"] = "100"
    return ips


def _load_risk_inputs() -> dict[str, Any]:
    return json.loads((EXAMPLES_DIR / "risk-inputs.valid.json").read_text(encoding="utf-8"))


BASE_STATE = {
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


class _MockSimulator:
    def simulate(self, call: dict[str, Any]) -> _SimResult:
        return _SimResult()


class _MockSigner:
    def __init__(self) -> None:
        self.calls = 0

    def sign_and_send(self, call: dict[str, Any]) -> str:
        self.calls += 1
        return "0x" + "b" * 64


def _worker_propose_order(
    db_file: str,
    lock_file: str,
    order_id: str,
    amount_val: str,
    queue: mp.Queue,
) -> None:
    """Worker process proposing an order against shared audit and lock files."""
    audit_sink = SqliteAuditSink(db_file)
    ledger = AuditLedger(db_file)

    def get_state() -> dict[str, Any]:
        st = dict(BASE_STATE)
        spent = ledger.spent_usd_since(datetime(2026, 10, 14, 0, 0, 0, tzinfo=UTC))
        st["spent_24h_usd"] = f"{spent:.2f}"
        return st

    policy = EnginePolicy(ips=_load_ips(), get_state=get_state, get_risk_inputs=_load_risk_inputs)
    lock = GatewayLock(lock_file, acquire_timeout_s=15.0)
    gw = Gateway(
        policy=policy,
        simulator=_MockSimulator(),
        signer=_MockSigner(),
        audit_sink=audit_sink,
        tx_builder=lambda o: [{"to": "0x1111111111111111111111111111111111111111", "data": "0x"}],
        lock=lock,
    )

    order = {
        "schema_version": 1,
        "order_id": order_id,
        "created_at": "2026-10-14T10:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": amount_val, "decimals": 6},
        "recipient": "self",
    }
    rec = gw.propose(order)
    queue.put((order_id, rec.get("outcome")))
    audit_sink.close()
    ledger.close()


def test_two_processes_serialised_limit(tmp_path: Path) -> None:
    """Acceptance test: two processes cannot both pass a limit (lock serialisation)."""
    db_file = tmp_path / "audit.sqlite"
    lock_file = tmp_path / "gateway.lock.sqlite"

    # Pre-init files
    SqliteAuditSink(db_file).close()
    GatewayLock(lock_file).held()

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()

    oid1 = str(uuid.uuid4())
    oid2 = str(uuid.uuid4())

    # Each process proposes 60 USDC against a 100 USDC per_day limit.
    # Total 120 USDC > 100 USDC limit: exactly one must execute, one must deny.
    p1 = ctx.Process(
        target=_worker_propose_order,
        args=(str(db_file), str(lock_file), oid1, "60000000", queue),
    )
    p2 = ctx.Process(
        target=_worker_propose_order,
        args=(str(db_file), str(lock_file), oid2, "60000000", queue),
    )

    p1.start()
    p2.start()

    p1.join(timeout=30)
    p2.join(timeout=30)

    assert p1.exitcode == 0, f"p1 exited with {p1.exitcode}"
    assert p2.exitcode == 0, f"p2 exited with {p2.exitcode}"

    results = {}
    while not queue.empty():
        oid, outcome = queue.get_nowait()
        results[oid] = outcome

    assert len(results) == 2
    outcomes = list(results.values())
    assert "executed" in outcomes, f"Expected one executed order: {outcomes}"
    assert "denied" in outcomes, f"Expected one denied order: {outcomes}"


def test_timeout_raises_gateway_busy_signer_not_called(tmp_path: Path) -> None:
    """Acceptance test: lock timeout fails closed with nothing signed."""
    db_file = tmp_path / "audit.sqlite"
    lock_file = tmp_path / "gateway.lock.sqlite"

    holder_lock = GatewayLock(lock_file)
    signer = _MockSigner()
    gw_lock = GatewayLock(lock_file, acquire_timeout_s=0.2)
    audit_sink = SqliteAuditSink(db_file)
    gw = Gateway(
        audit_sink=audit_sink,
        lock=gw_lock,
        signer=signer,
    )

    order = {
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

    # Hold the lock externally to trigger timeout
    with holder_lock.held():
        with pytest.raises(GatewayBusy):
            gw.propose(order)

    # Signer must NEVER have been called
    assert signer.calls == 0
    # Audit log must remain empty
    assert len(audit_sink) == 0
    audit_sink.close()
