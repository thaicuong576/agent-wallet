"""Small capped connection pool (design 5.2). No psycopg_pool, no session state.

Every use is one transaction: the pool commits on success and rolls back on error, so nothing
depends on a connection keeping state between uses. That is what makes it safe behind a
transaction-mode pooler (Supabase port 6543): no prepared statements (``prepare_threshold=None``),
no ``SET`` outside a transaction, no session advisory locks, no ``LISTEN``. The tenant is bound
per transaction with ``set_config('app.owner', owner, true)`` so row-level security applies.
"""

from __future__ import annotations

import contextvars
import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from core.storage.interfaces import StorageUnavailable

DEFAULT_POOL_SIZE = 12
DEFAULT_CONNECT_TIMEOUT_S = 10

db_calls: contextvars.ContextVar[int] = contextvars.ContextVar("db_calls", default=0)
db_ms: contextvars.ContextVar[int] = contextvars.ContextVar("db_ms", default=0)
request_db_acc: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "request_db_acc", default=None
)


def parse_pool_size(raw: str | None, default: int = DEFAULT_POOL_SIZE) -> int:
    """Parse pool size from string or environment.

    Rejects non-integer, below 1, or above 50, falling back to default.
    """
    if raw is None:
        return default
    try:
        val = int(raw.strip())
        if 1 <= val <= 50:
            return val
    except (ValueError, TypeError):
        pass
    return default


def db_stats() -> tuple[int, int]:
    """Return (calls, ms) for the current context."""
    acc = request_db_acc.get()
    if acc is not None:
        return (acc[0], acc[1])
    return (db_calls.get(), db_ms.get())


class PgPool:
    """At most ``max_size`` connections; callers wait up to ``acquire_timeout_s`` for one."""

    def __init__(
        self,
        dsn: str,
        *,
        max_size: int = DEFAULT_POOL_SIZE,
        acquire_timeout_s: float = 15.0,
        connect_timeout_s: int = DEFAULT_CONNECT_TIMEOUT_S,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self._dsn = dsn
        self._max_size = max_size
        self._acquire_timeout_s = acquire_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._idle: queue.LifoQueue[psycopg.Connection] = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(max_size)
        self._closed = False

    @property
    def max_size(self) -> int:
        return self._max_size

    def warm(self, n: int = 4) -> None:
        """Open up to n connections in a background daemon thread at startup.

        Failures are ignored, never raise, and never block startup.
        """
        target = min(max(n, 0), self._max_size)
        if target <= 0 or self._closed:
            return

        def _do_warm() -> None:
            conns: list[psycopg.Connection] = []
            try:
                for _ in range(target):
                    if self._closed:
                        break
                    if self._idle.qsize() >= target:
                        break
                    try:
                        conn = psycopg.connect(
                            self._dsn,
                            autocommit=False,
                            prepare_threshold=None,
                            connect_timeout=self._connect_timeout_s,
                        )
                        if self._closed:
                            self._discard(conn)
                            break
                        conns.append(conn)
                    except Exception:
                        break
                for conn in conns:
                    if self._closed:
                        self._discard(conn)
                    else:
                        self._idle.put(conn)
                        if self._closed:
                            self._discard(conn)
            except Exception:
                pass

        thread = threading.Thread(target=_do_warm, name="pg-pool-warm", daemon=True)
        thread.start()

    @property
    def dsn(self) -> str:
        return self._dsn

    def connect(self) -> psycopg.Connection:
        """Open one new connection outside the pool (used for the gateway lock)."""
        try:
            return psycopg.connect(
                self._dsn,
                autocommit=False,
                prepare_threshold=None,
                connect_timeout=self._connect_timeout_s,
            )
        except psycopg.OperationalError as exc:
            raise StorageUnavailable("cannot connect to the database") from exc

    def _checkout(self) -> psycopg.Connection:
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                return self.connect()
            if not conn.closed and not conn.broken:
                return conn

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """One transaction on a pooled connection. Backend outages raise StorageUnavailable."""
        t0 = time.perf_counter()
        try:
            if self._closed:
                raise StorageUnavailable("the pool is closed")
            if not self._slots.acquire(timeout=self._acquire_timeout_s):
                raise StorageUnavailable("no database connection available")
            conn: psycopg.Connection | None = None
            try:
                conn = self._checkout()
                try:
                    yield conn
                    conn.commit()
                except psycopg.OperationalError as exc:
                    self._discard(conn)
                    conn = None
                    raise StorageUnavailable("database error") from exc
                except BaseException:
                    if conn is not None and not conn.closed:
                        try:
                            conn.rollback()
                        except psycopg.Error:
                            self._discard(conn)
                            conn = None
                    raise
            finally:
                if conn is not None:
                    if conn.closed or conn.broken:
                        self._discard(conn)
                    else:
                        self._idle.put(conn)
                self._slots.release()
        finally:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            try:
                db_calls.set(db_calls.get() + 1)
                db_ms.set(db_ms.get() + elapsed_ms)
                acc = request_db_acc.get()
                if acc is not None:
                    acc[0] += 1
                    acc[1] += elapsed_ms
            except Exception:
                pass

    @contextmanager
    def tenant(self, owner: str) -> Iterator[psycopg.Cursor]:
        """A cursor in one transaction bound to ``owner`` for row-level security."""
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT set_config('app.owner', %s, true)", (owner,))
            yield cur

    @contextmanager
    def cursor(self) -> Iterator[psycopg.Cursor]:
        """A cursor in one transaction for the global tables (no tenant bound)."""
        with self.connection() as conn:
            yield conn.cursor()

    @staticmethod
    def _discard(conn: psycopg.Connection) -> None:
        try:
            conn.close()
        except psycopg.Error:
            pass

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                return
            self._discard(conn)
