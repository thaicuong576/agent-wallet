"""Tests for SqliteAuditSink, verify_chain, JSONL export/import, and CLI."""

from __future__ import annotations

import concurrent.futures
import copy
import json
import pathlib
import sqlite3
import subprocess
import sys
import uuid
from typing import Any

import jsonschema
import pytest

from core.audit import (
    GENESIS_PREV_HASH,
    AuditSink,
    SqliteAuditSink,
    export_jsonl,
    import_jsonl,
    record_hash,
    verify_chain,
)
from core.audit.verify import main as cli_main

CONTRACTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "contracts"
SCHEMA_PATH = CONTRACTS_DIR / "schemas" / "decision-record.schema.json"
EXAMPLE_CHAIN_PATH = CONTRACTS_DIR / "examples" / "decision-record.chain.json"


def _make_record_template(
    outcome: str = "denied",
    order_id: str | None = None,
    created_at: str = "2026-10-16T08:55:01Z",
) -> dict[str, Any]:
    """Helper to produce a valid decision record matching decision-record.schema.json."""
    oid = order_id or str(uuid.uuid4())
    ips_hash = "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1"

    order = {
        "schema_version": 1,
        "order_id": oid,
        "created_at": created_at,
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "100000000", "decimals": 6},
        "recipient": "self",
    }

    if outcome == "denied":
        decision = {
            "schema_version": 1,
            "order_id": oid,
            "decision": "deny",
            "rules_hit": [
                {
                    "code": "LIMIT.PER_TX",
                    "evidence": {"order_usd": "100.00", "per_tx_usd": "50"},
                }
            ],
            "reason": "Order exceeds transaction limit.",
            "ips_hash": ips_hash,
            "decided_at": created_at,
        }
        execution = None
        approval = None
        no_action = None
    elif outcome == "pending_approval":
        decision = {
            "schema_version": 1,
            "order_id": oid,
            "decision": "escalate",
            "rules_hit": [
                {
                    "code": "TIER.APPROVAL_REQUIRED",
                    "evidence": {"order_usd": "80.00", "escalate_above_usd": "50"},
                }
            ],
            "reason": "Requires owner approval.",
            "ips_hash": ips_hash,
            "decided_at": created_at,
        }
        execution = None
        approval = None
        no_action = None
    elif outcome == "executed":
        decision = {
            "schema_version": 1,
            "order_id": oid,
            "decision": "allow",
            "rules_hit": [],
            "reason": "Order complies with all rules.",
            "ips_hash": ips_hash,
            "decided_at": created_at,
        }
        execution = {
            "schema_version": 1,
            "order_id": oid,
            "status": "executed",
            "tx_hash": "0x4963a3d8e22c9d4a71c013f61aed299aa19ace9a0d8abf79f7aeca220e4d3fe7",
            "chain_id": 84532,
            "block_number": 12345678,
            "gas_used": 150000,
            "error_code": None,
            "detail": None,
            "executed_at": created_at,
        }
        approval = None
        no_action = None
    elif outcome == "no_action":
        return {
            "schema_version": 1,
            "seq": 0,
            "order_id": None,
            "order": None,
            "decision": None,
            "no_action": {
                "rules_hit": [{"code": "TIER.EMERGENCY_STOP", "evidence": {"engaged_by": "owner"}}],
                "reason": "Emergency stop is active.",
            },
            "outcome": "no_action",
            "execution": None,
            "approval": None,
            "tier": "T2",
            "emergency_stop": True,
            "ips_hash": ips_hash,
            "created_at": created_at,
            "prev_hash": "0" * 64,
            "hash": "0" * 64,
        }
    else:
        raise ValueError(f"Unknown outcome: {outcome}")

    return {
        "schema_version": 1,
        "seq": 0,
        "order_id": oid,
        "order": order,
        "decision": decision,
        "no_action": no_action,
        "outcome": outcome,
        "execution": execution,
        "approval": approval,
        "tier": "T2",
        "emergency_stop": False,
        "ips_hash": ips_hash,
        "created_at": created_at,
        "prev_hash": "0" * 64,
        "hash": "0" * 64,
    }


# ---------------------------------------------------------------------------
# Test 1: In-memory and protocol compliance
# ---------------------------------------------------------------------------
def test_sqlite_audit_sink_implements_protocol():
    sink = SqliteAuditSink(":memory:")
    assert isinstance(sink, AuditSink)
    assert len(sink) == 0
    assert sink.latest_hash() == GENESIS_PREV_HASH
    sink.close()


# ---------------------------------------------------------------------------
# Test 2: Valid chain for N records
# ---------------------------------------------------------------------------
def test_valid_chain_for_n_records():
    sink = SqliteAuditSink(":memory:")
    n = 25
    for i in range(n):
        rec = _make_record_template(outcome="denied")
        stored = sink.append(rec)
        assert stored["seq"] == i
        if i == 0:
            assert stored["prev_hash"] == GENESIS_PREV_HASH
        else:
            assert stored["prev_hash"] == sink[i - 1]["hash"]
        assert stored["hash"] == record_hash(stored)

    assert len(sink) == n
    report = verify_chain(sink)
    assert report.valid is True
    assert report.ok is True
    assert bool(report) is True
    assert report.count == n
    assert report.bad_seq is None
    assert report.error is None
    sink.close()


# ---------------------------------------------------------------------------
# Test 3: Reopening the same file continues the chain seamlessly
# ---------------------------------------------------------------------------
def test_reopen_same_file_continues_chain(tmp_path: pathlib.Path):
    db_file = tmp_path / "audit_chain.db"

    # Stage 1: Write 3 records and close
    sink1 = SqliteAuditSink(db_file)
    for _ in range(3):
        sink1.append(_make_record_template())
    assert len(sink1) == 3
    last_hash_stage1 = sink1[-1]["hash"]
    sink1.close()

    # Stage 2: Reopen the existing DB file with a new instance
    sink2 = SqliteAuditSink(db_file)
    assert len(sink2) == 3
    assert sink2.latest_hash() == last_hash_stage1

    new_rec = sink2.append(_make_record_template(outcome="pending_approval"))
    assert new_rec["seq"] == 3
    assert new_rec["prev_hash"] == last_hash_stage1
    assert len(sink2) == 4

    report = verify_chain(sink2)
    assert report.valid is True
    assert report.count == 4
    sink2.close()


# ---------------------------------------------------------------------------
# Test 4: Records strictly validate against decision-record.schema.json
# ---------------------------------------------------------------------------
def test_records_validate_against_decision_record_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    sink = SqliteAuditSink(":memory:")
    outcomes = ["denied", "pending_approval", "executed", "no_action"]
    for out in outcomes:
        rec = _make_record_template(outcome=out)
        stored = sink.append(rec)
        errors = list(validator.iter_errors(stored))
        assert not errors, f"Record for {out} failed validation: {errors}"

    sink.close()


# ---------------------------------------------------------------------------
# Test 5: The 3-record example verifies
# ---------------------------------------------------------------------------
def test_three_record_example_verifies():
    assert EXAMPLE_CHAIN_PATH.exists()
    chain = json.loads(EXAMPLE_CHAIN_PATH.read_text(encoding="utf-8"))
    assert len(chain) == 3

    report = verify_chain(chain)
    assert report.valid is True
    assert report.count == 3
    assert report.bad_seq is None


# ---------------------------------------------------------------------------
# Test 6: Tamper detection - Modified field
# ---------------------------------------------------------------------------
def test_tamper_detection_modified_field():
    sink = SqliteAuditSink(":memory:")
    for _ in range(4):
        sink.append(_make_record_template())

    records = sink.records
    assert verify_chain(records).valid is True

    # Tamper a field in record with seq = 1
    tampered = copy.deepcopy(records)
    tampered[1]["decision"]["reason"] = "Tampered reason by malicious actor."

    report = verify_chain(tampered)
    assert report.valid is False
    assert report.bad_seq == 1
    assert report.tamper_kind == "modified_field"
    assert "Modified field" in str(report.error)
    sink.close()


# ---------------------------------------------------------------------------
# Test 7: Tamper detection - Broken prev_hash
# ---------------------------------------------------------------------------
def test_tamper_detection_broken_prev_hash():
    sink = SqliteAuditSink(":memory:")
    for _ in range(4):
        sink.append(_make_record_template())

    records = sink.records
    tampered = copy.deepcopy(records)
    # Break prev_hash of record at seq = 2
    tampered[2]["prev_hash"] = "a" * 64
    # Recompute its hash so hash matches its own body, isolating prev_hash break
    tampered[2]["hash"] = record_hash(tampered[2])

    report = verify_chain(tampered)
    assert report.valid is False
    assert report.bad_seq == 2
    assert report.tamper_kind == "broken_prev_hash"
    assert "Broken prev_hash" in str(report.error)
    sink.close()


# ---------------------------------------------------------------------------
# Test 8: Tamper detection - Deleted record (middle and genesis)
# ---------------------------------------------------------------------------
def test_tamper_detection_deleted_middle_record():
    sink = SqliteAuditSink(":memory:")
    for _ in range(4):
        sink.append(_make_record_template())

    records = sink.records
    # Delete record at seq = 2 (remaining: 0, 1, 3)
    tampered = [records[0], records[1], records[3]]

    report = verify_chain(tampered)
    assert report.valid is False
    assert report.bad_seq == 2
    assert report.tamper_kind == "deleted_record"
    assert "Missing record at sequence 2" in str(report.error)
    sink.close()


def test_tamper_detection_deleted_genesis_record():
    sink = SqliteAuditSink(":memory:")
    for _ in range(3):
        sink.append(_make_record_template())

    records = sink.records
    # Delete genesis record (seq 0)
    tampered = records[1:]

    report = verify_chain(tampered)
    assert report.valid is False
    assert report.bad_seq == 0
    assert report.tamper_kind == "deleted_record"
    sink.close()


# ---------------------------------------------------------------------------
# Test 9: Tamper detection - Reordered records
# ---------------------------------------------------------------------------
def test_tamper_detection_reordered_records():
    sink = SqliteAuditSink(":memory:")
    for _ in range(4):
        sink.append(_make_record_template())

    records = sink.records
    # Swap record 1 and record 2 (order: 0, 2, 1, 3)
    tampered = [records[0], records[2], records[1], records[3]]

    report = verify_chain(tampered)
    assert report.valid is False
    assert report.bad_seq == 1
    assert report.tamper_kind == "reordered_record"
    assert "Reordered records" in str(report.error)
    sink.close()


# ---------------------------------------------------------------------------
# Test 10: Concurrent appends keep a valid chain without gaps
# ---------------------------------------------------------------------------
def test_concurrent_appends_keep_valid_chain(tmp_path: pathlib.Path):
    db_file = tmp_path / "concurrent.db"
    sink = SqliteAuditSink(db_file)

    num_threads = 8
    appends_per_thread = 10
    total = num_threads * appends_per_thread

    def _worker(worker_id: int):
        for j in range(appends_per_thread):
            sink.append(
                _make_record_template(
                    created_at="2026-10-16T08:55:01Z",
                    order_id=f"worker-{worker_id}-item-{j}",
                )
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_worker, w) for w in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert len(sink) == total
    seqs = [r["seq"] for r in sink.records]
    assert seqs == list(range(total))

    report = verify_chain(sink)
    assert report.valid is True
    assert report.count == total
    sink.close()


# ---------------------------------------------------------------------------
# Test 11: Append-only enforcement via triggers (no update/delete)
# ---------------------------------------------------------------------------
def test_append_only_sqlite_triggers_block_mutation(tmp_path: pathlib.Path):
    db_file = tmp_path / "append_only.db"
    sink = SqliteAuditSink(db_file)
    sink.append(_make_record_template())
    sink.append(_make_record_template())

    # Ensure no update or delete methods exist on SqliteAuditSink
    assert not hasattr(sink, "update")
    assert not hasattr(sink, "delete")

    # Attempt direct SQL update via raw connection
    raw_conn = sqlite3.connect(db_file)
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="Audit log is append-only"
    ):
        raw_conn.execute("UPDATE audit_records SET outcome = 'executed' WHERE seq = 0;")

    # Attempt direct SQL delete via raw connection
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="Audit log is append-only"
    ):
        raw_conn.execute("DELETE FROM audit_records WHERE seq = 0;")

    raw_conn.close()
    sink.close()


# ---------------------------------------------------------------------------
# Test 12: JSON Lines export and import
# ---------------------------------------------------------------------------
def test_jsonl_export_and_import(tmp_path: pathlib.Path):
    sink = SqliteAuditSink(":memory:")
    for _ in range(5):
        sink.append(_make_record_template())

    jsonl_file = tmp_path / "export.jsonl"
    count = export_jsonl(sink, jsonl_file)
    assert count == 5
    assert jsonl_file.exists()

    # Import into memory
    loaded = import_jsonl(jsonl_file)
    assert len(loaded) == 5
    assert loaded == sink.records

    # Import into a new SQLite sink via import_records
    new_db_file = tmp_path / "imported.db"
    new_sink = SqliteAuditSink(new_db_file)
    new_sink.import_records(loaded)
    assert len(new_sink) == 5
    assert new_sink.records == sink.records

    # Direct verify_chain on the JSONL file path
    report = verify_chain(jsonl_file)
    assert report.valid is True
    assert report.count == 5

    sink.close()
    new_sink.close()


# ---------------------------------------------------------------------------
# Test 13: CLI verification on DB and JSONL (success and tamper exit codes)
# ---------------------------------------------------------------------------
def test_cli_verify_db_valid_and_tampered(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
):
    db_file = tmp_path / "cli_test.db"
    sink = SqliteAuditSink(db_file)
    for _ in range(3):
        sink.append(_make_record_template())
    sink.close()

    # Valid DB exits 0
    exit_code = cli_main([str(db_file)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "OK: verified 3 records" in captured.out

    # Tamper database by dropping trigger and updating record
    raw_conn = sqlite3.connect(db_file)
    raw_conn.execute("DROP TRIGGER IF EXISTS trg_audit_prevent_update;")
    raw_conn.execute("UPDATE audit_records SET payload = '{\"bad\": true}' WHERE seq = 1;")
    raw_conn.commit()
    raw_conn.close()

    # Tampered DB exits non-zero (1)
    exit_code_tamper = cli_main([str(db_file)])
    assert exit_code_tamper == 1
    captured_tamper = capsys.readouterr()
    assert "TAMPER DETECTED" in captured_tamper.err


def test_cli_verify_jsonl_valid_and_tampered(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
):
    sink = SqliteAuditSink(":memory:")
    for _ in range(3):
        sink.append(_make_record_template())

    jsonl_file = tmp_path / "cli_test.jsonl"
    export_jsonl(sink, jsonl_file)
    sink.close()

    # Valid JSONL exits 0
    assert cli_main([str(jsonl_file)]) == 0
    captured = capsys.readouterr()
    assert "OK: verified 3 records" in captured.out

    # Tamper a line in JSONL file
    lines = jsonl_file.read_text(encoding="utf-8").splitlines()
    doc = json.loads(lines[1])
    doc["outcome"] = "tampered"
    lines[1] = json.dumps(doc)
    jsonl_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Tampered JSONL exits non-zero (1)
    assert cli_main([str(jsonl_file)]) == 1
    captured_tamper = capsys.readouterr()
    assert "TAMPER DETECTED" in captured_tamper.err


def test_cli_missing_file_exits_nonzero(capsys: pytest.CaptureFixture[str]):
    assert cli_main(["nonexistent_file_xyz.db"]) == 2
    captured = capsys.readouterr()
    assert "file not found" in captured.err


# ---------------------------------------------------------------------------
# Test 14: Indexing, iteration and context manager
# ---------------------------------------------------------------------------
def test_indexing_iteration_and_context_manager(tmp_path: pathlib.Path):
    db_file = tmp_path / "ctx.db"
    with SqliteAuditSink(db_file) as sink:
        sink.append(_make_record_template())
        sink.append(_make_record_template())

        assert len(sink) == 2
        assert sink[0]["seq"] == 0
        assert sink[1]["seq"] == 1
        assert sink[-1]["seq"] == 1
        assert sink[-2]["seq"] == 0

        with pytest.raises(IndexError):
            _ = sink[2]
        with pytest.raises(IndexError):
            _ = sink[-3]

        recs = list(sink)
        assert len(recs) == 2
        assert recs[0]["seq"] == 0
        assert recs[1]["seq"] == 1


# ---------------------------------------------------------------------------
# Test 15: CLI invoked as a subprocess
# ---------------------------------------------------------------------------
def test_cli_subprocess_invocation(tmp_path: pathlib.Path):
    db_file = tmp_path / "subproc.db"
    sink = SqliteAuditSink(db_file)
    for _ in range(2):
        sink.append(_make_record_template())
    sink.close()

    res = subprocess.run(
        [sys.executable, "-m", "core.audit.verify", str(db_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "OK: verified 2 records" in res.stdout
