"""Tests for IntentStore and order_intents immutability triggers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.audit.sink import SqliteAuditSink
from core.gateway.intents import IntentStore


def test_triggers_block_update_delete(tmp_path: Path) -> None:
    """Verify order_intents triggers reject raw UPDATE and DELETE operations."""
    db_path = tmp_path / "audit.sqlite"
    store = IntentStore(db_path)

    order_id = "11111111-1111-1111-1111-111111111111"
    store.begin_signing(order_id, "abc123hash", "2026-10-14T10:00:00Z")

    # Verify intent stored
    intent = store.get_intent(order_id)
    assert intent is not None
    assert intent["order_id"] == order_id
    assert intent["body_hash"] == "abc123hash"
    assert intent["state"] == "signing"

    # Attempt raw SQL UPDATE - must be blocked by trigger
    conn = sqlite3.connect(str(db_path))
    with pytest.raises(sqlite3.IntegrityError, match="order_intents is insert-only"):
        conn.execute(
            "UPDATE order_intents SET state = 'completed' WHERE order_id = ?;", (order_id,)
        )

    # Attempt raw SQL DELETE - must be blocked by trigger
    with pytest.raises(sqlite3.IntegrityError, match="order_intents is insert-only"):
        conn.execute("DELETE FROM order_intents WHERE order_id = ?;", (order_id,))

    conn.close()
    store.close()


def test_intent_store_unfinished_and_closed_by_record(tmp_path: Path) -> None:
    """unfinished() is True until an executed or failed record exists, then False."""
    db_path = tmp_path / "audit.sqlite"
    store = IntentStore(db_path)
    sink = SqliteAuditSink(db_path)

    order_id = "22222222-2222-2222-2222-222222222222"

    # No intent yet
    assert store.unfinished(order_id) is False

    # Intent began
    store.begin_signing(order_id, "hash_order_2", "2026-10-14T10:00:00Z")
    assert store.unfinished(order_id) is True

    # Now append an executed record to audit_records in the same DB
    dummy_record = {
        "schema_version": 1,
        "seq": 0,
        "order_id": order_id,
        "order": {"order_id": order_id},
        "decision": {"decision": "allow"},
        "no_action": None,
        "outcome": "executed",
        "execution": {"status": "executed", "tx_hash": "0x" + "a" * 64},
        "approval": None,
        "tier": "T2",
        "emergency_stop": False,
        "ips_hash": "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1",
        "created_at": "2026-10-14T10:01:00Z",
    }
    sink.append(dummy_record)

    # Intent is now closed by the executed record
    assert store.unfinished(order_id) is False

    store.close()
    sink.close()


def test_intent_store_duplicate_insert_fails(tmp_path: Path) -> None:
    """Verify primary key constraint on order_id in order_intents."""
    db_path = tmp_path / "audit.sqlite"
    store = IntentStore(db_path)

    order_id = "33333333-3333-3333-3333-333333333333"
    store.begin_signing(order_id, "hash_1", "2026-10-14T10:00:00Z")

    with pytest.raises(sqlite3.IntegrityError):
        store.begin_signing(order_id, "hash_2", "2026-10-14T10:05:00Z")

    store.close()
