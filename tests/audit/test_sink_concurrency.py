"""Concurrency tests for SqliteAuditSink across multiple processes.

Validates that concurrent writes from separate OS processes using SQLite
BEGIN IMMEDIATE transactions maintain cryptographic hash chain integrity,
strict monotonic sequencing, and zero sequence gaps or IntegrityErrors.
"""

from __future__ import annotations

import multiprocessing as mp
import pathlib
import sqlite3
from typing import Any

from core.audit import SqliteAuditSink, verify_chain


def _worker_append(db_path: str, count: int, worker_prefix: str) -> None:
    """Worker function executed in a separate OS process."""
    sink = SqliteAuditSink(db_path)
    for i in range(count):
        rec: dict[str, Any] = {
            "schema_version": 1,
            "order_id": f"{worker_prefix}-{i}",
            "outcome": "denied",
            "created_at": "2026-10-16T08:55:01Z",
        }
        sink.append(rec)
    sink.close()


def test_two_processes_append_concurrently(tmp_path: pathlib.Path) -> None:
    """Acceptance test for Ticket T1:

    Two processes append 200 records each to one file.
    verify_chain is ok, no gaps, no IntegrityError.
    """
    db_file = tmp_path / "concurrent_two_process.db"

    # Initialize DB schema before spawning workers
    init_sink = SqliteAuditSink(db_file)
    init_sink.close()

    count_per_process = 200
    ctx = mp.get_context("spawn")

    p1 = ctx.Process(
        target=_worker_append,
        args=(str(db_file), count_per_process, "proc1"),
    )
    p2 = ctx.Process(
        target=_worker_append,
        args=(str(db_file), count_per_process, "proc2"),
    )

    p1.start()
    p2.start()

    p1.join(timeout=60)
    p2.join(timeout=60)

    assert p1.exitcode == 0, f"Process 1 failed with exit code {p1.exitcode}"
    assert p2.exitcode == 0, f"Process 2 failed with exit code {p2.exitcode}"

    total_expected = count_per_process * 2
    sink = SqliteAuditSink(db_file)
    try:
        assert len(sink) == total_expected

        # Verify no sequence gaps and strict 0..N-1 ordering
        seqs = [r["seq"] for r in sink.records]
        assert seqs == list(range(total_expected))

        # Verify all records from both processes made it into the sink
        proc1_records = [r for r in sink.records if str(r.get("order_id", "")).startswith("proc1-")]
        proc2_records = [r for r in sink.records if str(r.get("order_id", "")).startswith("proc2-")]
        assert len(proc1_records) == count_per_process
        assert len(proc2_records) == count_per_process

        # Verify cryptographic chain integrity
        report = verify_chain(sink)
        assert report.valid is True
        assert report.ok is True
        assert report.count == total_expected
        assert report.bad_seq is None
        assert report.error is None
    finally:
        sink.close()


def test_append_onto_existing_chain_concurrently(tmp_path: pathlib.Path) -> None:
    """Two processes append to a database that already contains initial records."""
    db_file = tmp_path / "existing_chain.db"

    # Pre-seed with 10 records
    init_sink = SqliteAuditSink(db_file)
    for i in range(10):
        init_sink.append(
            {
                "schema_version": 1,
                "order_id": f"seed-{i}",
                "outcome": "denied",
                "created_at": "2026-10-16T08:50:00Z",
            }
        )
    assert len(init_sink) == 10
    init_sink.close()

    count_per_process = 50
    ctx = mp.get_context("spawn")

    p1 = ctx.Process(
        target=_worker_append,
        args=(str(db_file), count_per_process, "workerA"),
    )
    p2 = ctx.Process(
        target=_worker_append,
        args=(str(db_file), count_per_process, "workerB"),
    )

    p1.start()
    p2.start()

    p1.join(timeout=30)
    p2.join(timeout=30)

    assert p1.exitcode == 0
    assert p2.exitcode == 0

    total_expected = 10 + count_per_process * 2
    sink = SqliteAuditSink(db_file)
    try:
        assert len(sink) == total_expected
        seqs = [r["seq"] for r in sink.records]
        assert seqs == list(range(total_expected))

        report = verify_chain(sink)
        assert report.valid is True
        assert report.ok is True
        assert report.count == total_expected
    finally:
        sink.close()


def test_three_processes_append_concurrently(tmp_path: pathlib.Path) -> None:
    """Three concurrent processes appending 50 records each."""
    db_file = tmp_path / "three_processes.db"

    init_sink = SqliteAuditSink(db_file)
    init_sink.close()

    count_per_process = 50
    ctx = mp.get_context("spawn")

    processes = [
        ctx.Process(
            target=_worker_append,
            args=(str(db_file), count_per_process, f"worker{idx}"),
        )
        for idx in range(3)
    ]

    for p in processes:
        p.start()

    for p in processes:
        p.join(timeout=30)
        assert p.exitcode == 0

    total_expected = count_per_process * 3
    sink = SqliteAuditSink(db_file)
    try:
        assert len(sink) == total_expected
        seqs = [r["seq"] for r in sink.records]
        assert seqs == list(range(total_expected))

        report = verify_chain(sink)
        assert report.valid is True
        assert report.ok is True
        assert report.count == total_expected
    finally:
        sink.close()


def test_begin_immediate_serializes_transactions(tmp_path: pathlib.Path) -> None:
    """Verify that BEGIN IMMEDIATE serializes transactions across connections."""
    db_file = tmp_path / "tx_serialize.db"
    sink = SqliteAuditSink(db_file)
    sink.close()

    # Connection 1 acquires IMMEDIATE lock
    conn1 = sqlite3.connect(db_file, timeout=5.0)
    conn1.execute("BEGIN IMMEDIATE;")

    # Connection 2 attempts IMMEDIATE lock with short timeout (should time out while conn1 holds it)
    conn2 = sqlite3.connect(db_file, timeout=0.1)
    lock_blocked = False
    try:
        conn2.execute("BEGIN IMMEDIATE;")
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc) or "busy" in str(exc):
            lock_blocked = True
    finally:
        conn2.close()

    conn1.execute("ROLLBACK;")
    conn1.close()

    assert lock_blocked is True, "Second connection did not block on BEGIN IMMEDIATE"
