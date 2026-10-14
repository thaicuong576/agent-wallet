"""Intent store for pre-signing order intents in audit.sqlite."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

__all__ = ["IntentStore"]


class IntentStore:
    """Insert-only store for order signing intents, stored in audit.sqlite."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA busy_timeout = 30000;")
            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL;")

            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS order_intents (
                        order_id TEXT PRIMARY KEY,
                        body_hash TEXT NOT NULL,
                        state TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                self._conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_intents_prevent_update
                    BEFORE UPDATE ON order_intents
                    BEGIN
                        SELECT RAISE(ABORT, 'order_intents is insert-only: updates are forbidden');
                    END;
                    """
                )
                self._conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_intents_prevent_delete
                    BEFORE DELETE ON order_intents
                    BEGIN
                        SELECT RAISE(ABORT, 'order_intents is insert-only: deletes are forbidden');
                    END;
                    """
                )

    def begin_signing(self, order_id: str, body_hash: str, now: str) -> None:
        """Record signing intent. Insert-only, never updated."""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO order_intents (order_id, body_hash, state, created_at)
                    VALUES (?, ?, 'signing', ?);
                    """,
                    (order_id, body_hash, now),
                )

    def get_intent(self, order_id: str) -> dict[str, Any] | None:
        """Retrieve stored intent for order_id or None."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT order_id, body_hash, state, created_at
                FROM order_intents WHERE order_id = ?;
                """,
                (order_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "order_id": row[0],
                "body_hash": row[1],
                "state": row[2],
                "created_at": row[3],
            }

    def unfinished(self, order_id: str) -> bool:
        """Return True if an intent exists without an executed/failed audit record."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM order_intents WHERE order_id = ?;",
                (order_id,),
            )
            if cursor.fetchone() is None:
                return False

            try:
                cursor = self._conn.execute(
                    """
                    SELECT 1 FROM audit_records
                    WHERE order_id = ? AND outcome IN ('executed', 'failed')
                    LIMIT 1;
                    """,
                    (order_id,),
                )
                if cursor.fetchone() is not None:
                    return False
            except sqlite3.OperationalError:
                # Table audit_records does not exist yet
                pass

            return True

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> IntentStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
