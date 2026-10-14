"""Tests for TierStore WAL mode, busy timeout, and multi-instance shared file access.

Verifies that two TierStore instances (e.g. API and runner processes) operating
on the same SQLite file see updates immediately via TierView, with WAL mode
and busy_timeout=5000 properly configured.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import jsonschema
import pytest

from core.tiers.errors import EmergencyStopActiveError, InvalidTierError
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from core.tiers.view import TierView


def test_two_stores_stop_engaged_seen_by_other_tierview(tmp_path: Path) -> None:
    """A stop engaged by one TierStore is seen by the other's TierView on its next read."""
    db_file = tmp_path / "shared_state.sqlite"

    store_api = TierStore(db_file)
    store_runner = TierStore(db_file)
    view_runner = TierView(store_runner)

    try:
        # Initially stop is not engaged
        assert view_runner.is_stopped() is False

        # API process engages emergency stop
        OwnerControls(store_api).engage_stop()

        # Runner process sees the stop immediately on its next read
        assert view_runner.is_stopped() is True

        # API process releases emergency stop
        OwnerControls(store_api).release_stop()

        # Runner process sees the release immediately on its next read
        assert view_runner.is_stopped() is False
    finally:
        store_api.close()
        store_runner.close()


def test_two_stores_tier_update_seen_by_other_tierview(tmp_path: Path) -> None:
    """A tier change made by one store is seen by the other store's TierView on its next read."""
    db_file = tmp_path / "shared_state.sqlite"

    store_api = TierStore(db_file)
    store_runner = TierStore(db_file)
    view_runner = TierView(store_runner)

    try:
        assert view_runner.current() == "T0"

        OwnerControls(store_api).set_tier("T1")
        assert view_runner.current() == "T1"

        OwnerControls(store_api).set_tier("T2")
        assert view_runner.current() == "T2"
    finally:
        store_api.close()
        store_runner.close()


def test_two_stores_approval_queue_seen_by_other_tierview(tmp_path: Path) -> None:
    """Approval requests and resolutions made by one store are seen by the other's TierView."""
    db_file = tmp_path / "shared_state.sqlite"

    store_api = TierStore(db_file)
    store_runner = TierStore(db_file)
    view_runner = TierView(store_runner)
    order_id = "00000000-0000-0000-0000-000000000042"

    try:
        assert view_runner.pending_orders() == []
        assert view_runner.approval_status(order_id) is None

        # Request approval via API store
        OwnerControls(store_api).request_approval(order_id)
        assert view_runner.pending_orders() == [order_id]
        assert view_runner.approval_status(order_id) == "pending"

        # Approve via API store
        OwnerControls(store_api).approve(order_id)
        assert view_runner.pending_orders() == []
        assert view_runner.approval_status(order_id) == "approved"
    finally:
        store_api.close()
        store_runner.close()


def test_tier_store_wal_journal_mode_set(tmp_path: Path) -> None:
    """File-backed TierStore enables SQLite WAL journal mode."""
    db_file = tmp_path / "test_wal.sqlite"
    store = TierStore(db_file)
    try:
        cur = store._conn.cursor()
        mode = cur.execute("PRAGMA journal_mode;").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        store.close()


def test_tier_store_busy_timeout_set(tmp_path: Path) -> None:
    """TierStore configures busy timeout to 5000 ms on both file and in-memory databases."""
    db_file = tmp_path / "test_busy.sqlite"

    store_file = TierStore(db_file)
    try:
        cur = store_file._conn.cursor()
        timeout_ms = cur.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert timeout_ms == 5000
    finally:
        store_file.close()

    store_mem = TierStore(":memory:")
    try:
        cur = store_mem._conn.cursor()
        timeout_ms = cur.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert timeout_ms == 5000
    finally:
        store_mem.close()


def test_two_processes_stop_engaged_seen_by_runner_tierview(tmp_path: Path) -> None:
    """Separate OS processes sharing the tier database propagate stop status correctly."""
    db_file = tmp_path / "cross_proc_state.sqlite"

    store_runner = TierStore(db_file)
    view_runner = TierView(store_runner)

    try:
        assert view_runner.is_stopped() is False

        # Simulate API worker in a separate OS process engaging emergency stop
        worker_code = f"""
from pathlib import Path
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore

p = Path({repr(str(db_file))})
store = TierStore(p)
OwnerControls(store).engage_stop()
store.close()
"""
        proc = subprocess.run(
            [sys.executable, "-c", worker_code],
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc.returncode == 0

        # Runner in this process immediately sees the emergency stop
        assert view_runner.is_stopped() is True
    finally:
        store_runner.close()


def test_two_stores_concurrent_readers_and_writer(tmp_path: Path) -> None:
    """WAL mode allows concurrent reads without locking errors while a writer mutates."""
    db_file = tmp_path / "concurrent_state.sqlite"

    store_reader = TierStore(db_file)
    view_reader = TierView(store_reader)
    store_writer = TierStore(db_file)
    owner_writer = OwnerControls(store_writer)

    errors: list[Exception] = []
    stop_event = threading.Event()

    def reader_loop() -> None:
        try:
            while not stop_event.is_set():
                _ = view_reader.is_stopped()
                _ = view_reader.current()
                _ = view_reader.pending_orders()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    reader_thread = threading.Thread(target=reader_loop, daemon=True)
    reader_thread.start()

    try:
        for _ in range(20):
            owner_writer.engage_stop()
            assert view_reader.is_stopped() is True
            owner_writer.release_stop()
            assert view_reader.is_stopped() is False
    finally:
        stop_event.set()
        reader_thread.join(timeout=2.0)
        store_reader.close()
        store_writer.close()

    assert not errors, f"Unexpected concurrency errors encountered: {errors}"


def test_schema_validation_and_existing_tier_rules_unchanged(tmp_path: Path) -> None:
    """Schema validation and tier state invariants remain strictly enforced on shared files."""
    db_file = tmp_path / "shared_invariants.sqlite"
    store = TierStore(db_file)
    owner = OwnerControls(store)

    try:
        # Invalid tier rejected
        with pytest.raises(InvalidTierError):
            owner.set_tier("T99")

        # Invalid schema payload rejected
        with pytest.raises(jsonschema.ValidationError):
            store.validate_tier_state(
                {
                    "schema_version": 1,
                    "tier": "T0",
                    "emergency_stop": "not-a-bool",
                    "updated_at": "2026-10-14T00:00:00Z",
                    "changed_by": "owner",
                }
            )

        # Emergency stop active blocks approval
        order_id = "00000000-0000-0000-0000-000000000099"
        owner.request_approval(order_id)
        owner.engage_stop()
        with pytest.raises(EmergencyStopActiveError):
            owner.approve(order_id)
    finally:
        store.close()


def test_no_change_to_which_methods_are_mutators() -> None:
    """TierView remains purely read-only and TierStore exposes exactly the expected mutators."""
    view_public = {attr for attr in dir(TierView) if not attr.startswith("_")}
    assert view_public == {
        "approval_status",
        "current",
        "get_approval",
        "is_stopped",
        "pending_orders",
    }

    store_mutators = {
        "update_tier_value",
        "update_stop_flag",
        "insert_request",
        "resolve_request",
    }
    for mutator in store_mutators:
        assert hasattr(TierStore, mutator)
        assert callable(getattr(TierStore, mutator))

    # Ensure no unexpected mutators were added to TierStore
    store_public = {
        attr
        for attr in dir(TierStore)
        if not attr.startswith("_") and callable(getattr(TierStore, attr))
    }
    expected_store_public = {
        "validate_tier_state",
        "get_tier_state",
        "update_tier_value",
        "update_stop_flag",
        "insert_request",
        "resolve_request",
        "get_approval",
        "list_pending_orders",
        "close",
    }
    assert store_public == expected_store_public
