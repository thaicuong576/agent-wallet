"""Small capped connection pool (design 5.2). No psycopg_pool, no session state.

Every use is one transaction: the pool commits on success and rolls back on error, so nothing
depends on a connection keeping state between uses. That is what makes it safe behind a
transaction-mode pooler (Supabase port 6543): no prepared statements (``prepare_threshold=None``),
no ``SET`` outside a transaction, no session advisory locks, no ``LISTEN``. The tenant is bound
per transaction with ``set_config('app.owner', owner, true)`` so row-level security applies.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from core.storage.interfaces import StorageUnavailable

DEFAULT_POOL_SIZE = 5
DEFAULT_CONNECT_TIMEOUT_S = 10


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
        self._acquire_timeout_s = acquire_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._idle: queue.LifoQueue[psycopg.Connection] = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(max_size)
        self._closed = False

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
