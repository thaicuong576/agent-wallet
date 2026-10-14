"""Gateway process lock backed by SQLite BEGIN IMMEDIATE."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.gateway.exceptions import GatewayBusy

__all__ = ["GatewayBusy", "GatewayLock"]


class GatewayLock:
    """Process-level lock using SQLite BEGIN IMMEDIATE on a dedicated lock file."""

    def __init__(self, path: Path | str, acquire_timeout_s: float = 15.0) -> None:
        self.path = Path(path) if str(path) != ":memory:" else ":memory:"
        self.acquire_timeout_s = float(acquire_timeout_s)
        self._mem_lock = threading.RLock() if str(path) == ":memory:" else None
        if self.path != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self.path), timeout=self.acquire_timeout_s) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS lock (id INTEGER PRIMARY KEY);")

    @contextmanager
    def held(self) -> Iterator[None]:
        """Acquire SQLite RESERVED/EXCLUSIVE lock via BEGIN IMMEDIATE.

        Raises:
            GatewayBusy: if the lock cannot be acquired within acquire_timeout_s.
        """
        if self.path == ":memory:":
            acquired = self._mem_lock.acquire(timeout=self.acquire_timeout_s)  # type: ignore[union-attr]
            if not acquired:
                raise GatewayBusy(
                    f"Gateway lock timeout ({self.acquire_timeout_s}s) on in-memory lock"
                )
            try:
                yield
            finally:
                self._mem_lock.release()  # type: ignore[union-attr]
            return

        conn = sqlite3.connect(
            str(self.path),
            timeout=self.acquire_timeout_s,
            isolation_level=None,
        )
        conn.execute(f"PRAGMA busy_timeout = {int(self.acquire_timeout_s * 1000)};")
        try:
            try:
                conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError as e:
                raise GatewayBusy(
                    f"Gateway lock timeout ({self.acquire_timeout_s}s) on {self.path}: {e}"
                ) from e
            yield
            try:
                conn.execute("COMMIT;")
            except Exception:
                pass
        finally:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            conn.close()
