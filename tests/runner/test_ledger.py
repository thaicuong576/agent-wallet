"""Tests for AuditLedger reading spend and count limits from the audit log."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.audit.sink import SqliteAuditSink
from core.policy.engine import evaluate
from core.runner.ledger import AuditLedger

DEFAULT_IPS = {
    "schema_version": 1,
    "universe": {
        "tokens": ["USDC"],
        "protocols": ["aave-v3"],
        "functions": ["supply", "withdraw", "approve"],
        "recipients": ["self"],
    },
    "limits": {
        "per_tx_usd": "100",
        "per_day_usd": "300",
        "max_tx_per_hour": 5,
    },
    "risk": {
        "max_borrow_apr": "0.15",
        "min_health_factor": "1.5",
    },
}

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


def _make_record(
    outcome: str = "executed",
    function: str = "supply",
    value: str = "50000000",
    decimals: int = 6,
    token: str = "USDC",
    created_at: str = "2026-10-14T08:00:00Z",
) -> dict[str, Any]:
    oid = str(uuid.uuid4())
    order = {
        "schema_version": 1,
        "order_id": oid,
        "created_at": created_at,
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": function,
        "token": token,
        "amount": {"value": value, "decimals": decimals},
        "recipient": "self",
    }
    decision = {
        "schema_version": 1,
        "order_id": oid,
        "decision": "allow" if outcome == "executed" else "deny",
        "rules_hit": [] if outcome == "executed" else [{"code": "LIMIT.PER_TX"}],
        "reason": "OK",
        "ips_hash": "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1",
        "decided_at": created_at,
    }
    execution = (
        {"status": "executed", "tx_hash": "0x" + "a" * 64} if outcome == "executed" else None
    )
    return {
        "schema_version": 1,
        "seq": 0,
        "order_id": oid,
        "order": order,
        "decision": decision,
        "no_action": None,
        "outcome": outcome,
        "execution": execution,
        "approval": None,
        "tier": "T2",
        "emergency_stop": False,
        "ips_hash": "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1",
        "created_at": created_at,
    }


def test_per_day_denied_from_seeded_log(tmp_path: Path) -> None:
    """Verify LIMIT.PER_DAY is denied when seeded audit log spend + order exceeds limit."""
    db_path = tmp_path / "audit.sqlite"
    sink = SqliteAuditSink(db_path)

    # Seed 150 USDC at 05:00 UTC and 100 USDC at 08:00 UTC (total = 250 USDC)
    sink.append(
        _make_record(outcome="executed", value="150000000", created_at="2026-10-14T05:00:00Z")
    )
    sink.append(
        _make_record(outcome="executed", value="100000000", created_at="2026-10-14T08:00:00Z")
    )
    sink.close()

    now = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
    ledger = AuditLedger(db_path)
    spent_24h = ledger.spent_usd_since(now - timedelta(hours=24))
    assert spent_24h == Decimal("250.00")

    # Wire into state and evaluate engine
    state = copy.deepcopy(BASE_STATE)
    state["spent_24h_usd"] = str(spent_24h)

    # Order of 60 USDC (60 USD). 250 + 60 = 310 > 300 per_day_usd limit
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-14T10:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "60000000", "decimals": 6},
        "recipient": "self",
    }
    decision = evaluate(DEFAULT_IPS, state, order, now=now)
    assert decision["decision"] == "deny"
    rules_hit_codes = [r["code"] for r in decision["rules_hit"]]
    assert "LIMIT.PER_DAY" in rules_hit_codes

    evidence = [r for r in decision["rules_hit"] if r["code"] == "LIMIT.PER_DAY"][0]["evidence"]
    assert evidence["spent_24h_usd"] == "250.00"
    assert evidence["projected_24h_usd"] == "310.00"
    assert evidence["per_day_usd"] == "300"
    ledger.close()


def test_tx_per_hour_denied_from_seeded_log(tmp_path: Path) -> None:
    """Verify LIMIT.TX_PER_HOUR is denied when seeded audit log has max_tx_per_hour in 1h window."""
    db_path = tmp_path / "audit.sqlite"
    sink = SqliteAuditSink(db_path)

    # 1 tx 2 hours ago (outside 1h window)
    sink.append(_make_record(outcome="executed", created_at="2026-10-14T08:00:00Z"))

    # 5 txs within the last hour (between 09:05 and 09:45)
    for minute in (5, 15, 25, 35, 45):
        sink.append(
            _make_record(
                outcome="executed",
                created_at=f"2026-10-14T09:{minute:02d}:00Z",
            )
        )
    sink.close()

    now = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
    ledger = AuditLedger(db_path)
    count_1h = ledger.executed_count_since(now - timedelta(hours=1))
    assert count_1h == 5

    # Wire into state and evaluate engine
    state = copy.deepcopy(BASE_STATE)
    state["tx_count_1h"] = count_1h

    # Proposing a new order should hit LIMIT.TX_PER_HOUR
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
    decision = evaluate(DEFAULT_IPS, state, order, now=now)
    assert decision["decision"] == "deny"
    rules_hit_codes = [r["code"] for r in decision["rules_hit"]]
    assert "LIMIT.TX_PER_HOUR" in rules_hit_codes
    ledger.close()


def test_only_executed_counts(tmp_path: Path) -> None:
    """Verify that only executed records count for spend and count, not denied/failed/pending."""
    db_path = tmp_path / "audit.sqlite"
    sink = SqliteAuditSink(db_path)

    # 1 executed (50 USDC)
    sink.append(
        _make_record(outcome="executed", value="50000000", created_at="2026-10-14T09:05:00Z")
    )
    # 1 denied (100 USDC)
    sink.append(
        _make_record(outcome="denied", value="100000000", created_at="2026-10-14T09:10:00Z")
    )
    # 1 failed (100 USDC)
    sink.append(
        _make_record(outcome="failed", value="100000000", created_at="2026-10-14T09:20:00Z")
    )
    # 1 pending_approval (100 USDC)
    sink.append(
        _make_record(
            outcome="pending_approval", value="100000000", created_at="2026-10-14T09:30:00Z"
        )
    )

    # 1 no_action
    no_action_rec = {
        "schema_version": 1,
        "seq": 0,
        "order_id": None,
        "order": None,
        "decision": None,
        "no_action": {"rules_hit": [], "reason": "market in band"},
        "outcome": "no_action",
        "execution": None,
        "approval": None,
        "tier": "T2",
        "emergency_stop": False,
        "ips_hash": "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1",
        "created_at": "2026-10-14T09:40:00Z",
    }
    sink.append(no_action_rec)
    sink.close()

    now = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
    ledger = AuditLedger(db_path)
    # Only the 50 USDC executed record counts
    assert ledger.spent_usd_since(now - timedelta(hours=24)) == Decimal("50.00")
    # Only the 1 executed record counts
    assert ledger.executed_count_since(now - timedelta(hours=1)) == 1
    ledger.close()


def test_approve_not_in_spend(tmp_path: Path) -> None:
    """Verify that approve is excluded from spend calculation but included in tx count."""
    db_path = tmp_path / "audit.sqlite"
    sink = SqliteAuditSink(db_path)

    # Executed approve of 1,000,000 USDC
    sink.append(
        _make_record(
            outcome="executed",
            function="approve",
            value="1000000000000",
            created_at="2026-10-14T09:05:00Z",
        )
    )
    # Executed supply of 40 USDC
    sink.append(
        _make_record(
            outcome="executed",
            function="supply",
            value="40000000",
            created_at="2026-10-14T09:10:00Z",
        )
    )
    sink.close()

    now = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
    ledger = AuditLedger(db_path)
    # Spend should only be 40 USD; approve (1M USD) is excluded
    assert ledger.spent_usd_since(now - timedelta(hours=24)) == Decimal("40.00")
    # Count should include both approve and supply (2 executed txs)
    assert ledger.executed_count_since(now - timedelta(hours=1)) == 2
    ledger.close()


def test_mint_transfer_not_in_spend_but_in_count(tmp_path: Path) -> None:
    """Verify mint and transfer are excluded from 24h spend but included in tx count."""
    db_path = tmp_path / "audit.sqlite"
    sink = SqliteAuditSink(db_path)

    # Executed mint of 1,000,000 USDC
    sink.append(
        _make_record(
            outcome="executed",
            function="mint",
            value="1000000000000",
            created_at="2026-10-14T09:05:00Z",
        )
    )
    # Executed transfer of 500 USDC
    sink.append(
        _make_record(
            outcome="executed",
            function="transfer",
            value="500000000",
            created_at="2026-10-14T09:08:00Z",
        )
    )
    # Executed supply of 40 USDC
    sink.append(
        _make_record(
            outcome="executed",
            function="supply",
            value="40000000",
            created_at="2026-10-14T09:10:00Z",
        )
    )
    sink.close()

    now = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
    ledger = AuditLedger(db_path)
    # Spend should only be 40 USD; mint and transfer are excluded
    assert ledger.spent_usd_since(now - timedelta(hours=24)) == Decimal("40.00")
    # Count should include mint, transfer, and supply (3 executed txs)
    assert ledger.executed_count_since(now - timedelta(hours=1)) == 3
    ledger.close()
