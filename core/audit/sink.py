"""SQLite-backed append-only audit sink implementing AuditSink protocol."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from core.gateway.audit import AuditSink, canonical, record_hash

GENESIS_PREV_HASH = "0" * 64


class SqliteAuditSink(AuditSink):
    """Persistent, append-only audit sink backed by SQLite.

    Maintains a cryptographic SHA-256 hash chain over all stored decision records.
    Every record receives:
      - seq: integer sequence starting at 0, strictly increasing without gaps
      - prev_hash: SHA-256 hash of previous record (64 zeros for genesis)
      - hash: SHA-256 of canonical JSON of the record without its 'hash' field

    Append-only guarantee:
      - No update or delete API is exposed
      - Database triggers block any raw SQL UPDATE or DELETE operations
      - Thread-safe and process-safe via internal RLock and atomic
        SQLite BEGIN IMMEDIATE transactions
    """

    GENESIS_PREV_HASH = GENESIS_PREV_HASH

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()

        if self.db_path != ":memory:":
            path_obj = Path(self.db_path)
            path_obj.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA busy_timeout = 30000;")
            if self.db_path != ":memory:":
                # Use Write-Ahead Logging for concurrency and durability
                self._conn.execute("PRAGMA journal_mode = WAL;")

            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_records (
                        seq INTEGER PRIMARY KEY,
                        prev_hash TEXT NOT NULL,
                        hash TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        order_id TEXT,
                        outcome TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    """
                )
                # Enforce append-only at the database engine level
                self._conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_audit_prevent_update
                    BEFORE UPDATE ON audit_records
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit log is append-only: updates are forbidden');
                    END;
                    """
                )
                self._conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_audit_prevent_delete
                    BEFORE DELETE ON audit_records
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit log is append-only: deletes are forbidden');
                    END;
                    """
                )

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append record to chain, assigning seq, prev_hash, and sha256 hash.

        Thread-safe and atomic. Returns stored record with assigned fields.
        """
        rec = copy.deepcopy(record)

        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE;")
                cursor = self._conn.execute(
                    "SELECT seq, hash FROM audit_records ORDER BY seq DESC LIMIT 1;"
                )
                row = cursor.fetchone()
                if row is None:
                    seq = 0
                    prev_hash = self.GENESIS_PREV_HASH
                else:
                    last_seq, last_hash = row
                    seq = last_seq + 1
                    prev_hash = last_hash

                rec["seq"] = seq
                rec["prev_hash"] = prev_hash
                rec["hash"] = record_hash(rec)

                # Canonical JSON payload ensures reproducible re-verification
                payload = canonical(rec).decode("utf-8")
                created_at = str(rec.get("created_at") or "")
                order_id = rec.get("order_id")
                outcome = str(rec.get("outcome") or "")

                self._conn.execute(
                    """
                    INSERT INTO audit_records (
                        seq, prev_hash, hash, created_at, order_id, outcome, payload
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (seq, prev_hash, rec["hash"], created_at, order_id, outcome, payload),
                )

        return copy.deepcopy(rec)

    def import_records(self, records: list[dict[str, Any]]) -> int:
        """Import pre-existing hashed records (e.g. from JSONL backup).

        Preserves existing seq, prev_hash, and hash, validating that the incoming
        sequence starts at current seq without gaps.
        """
        imported_count = 0
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE;")
                cursor = self._conn.execute(
                    "SELECT seq, hash FROM audit_records ORDER BY seq DESC LIMIT 1;"
                )
                row = cursor.fetchone()
                current_seq = -1 if row is None else row[0]
                expected_prev = self.GENESIS_PREV_HASH if row is None else row[1]

                for rec in records:
                    seq = rec.get("seq")
                    prev_hash = rec.get("prev_hash")
                    rec_h = rec.get("hash")
                    if seq != current_seq + 1:
                        raise ValueError(
                            f"Import sequence gap: expected seq {current_seq + 1}, got {seq}"
                        )
                    if prev_hash != expected_prev:
                        raise ValueError(
                            f"Import chain mismatch at seq {seq}: prev_hash does not match"
                        )
                    if record_hash(rec) != rec_h:
                        raise ValueError(
                            f"Import hash mismatch at seq {seq}: record payload tampered"
                        )

                    payload = canonical(rec).decode("utf-8")
                    created_at = str(rec.get("created_at") or "")
                    order_id = rec.get("order_id")
                    outcome = str(rec.get("outcome") or "")

                    self._conn.execute(
                        """
                        INSERT INTO audit_records (
                            seq, prev_hash, hash, created_at, order_id, outcome, payload
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        (seq, prev_hash, rec_h, created_at, order_id, outcome, payload),
                    )
                    current_seq = seq
                    expected_prev = rec_h
                    imported_count += 1

        return imported_count

    @property
    def records(self) -> list[dict[str, Any]]:
        """Return shallow copies of all stored audit records in seq order."""
        with self._lock:
            cursor = self._conn.execute("SELECT payload FROM audit_records ORDER BY seq ASC;")
            return [json.loads(row[0]) for row in cursor.fetchall()]

    def latest_hash(self) -> str:
        """Return the hash of the latest record, or genesis prev_hash if empty."""
        with self._lock:
            cursor = self._conn.execute("SELECT hash FROM audit_records ORDER BY seq DESC LIMIT 1;")
            row = cursor.fetchone()
            return row[0] if row is not None else self.GENESIS_PREV_HASH

    def __len__(self) -> int:
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM audit_records;")
            return int(cursor.fetchone()[0])

    def __getitem__(self, index: int) -> dict[str, Any]:
        with self._lock:
            total = len(self)
            if index < 0:
                index = total + index
            if index < 0 or index >= total:
                raise IndexError(f"Audit log index {index} out of range (0..{total - 1})")

            cursor = self._conn.execute(
                "SELECT payload FROM audit_records WHERE seq = ?;", (index,)
            )
            row = cursor.fetchone()
            if row is None:
                raise IndexError(f"Audit log record with seq {index} not found")
            return json.loads(row[0])

    def __iter__(self) -> Iterator[dict[str, Any]]:
        yield from self.records

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteAuditSink:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
