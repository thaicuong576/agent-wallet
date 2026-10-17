"""PgGatewayLockProvider: implements GatewayLockProvider protocol (core.storage.interfaces)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

import psycopg

from core.gateway.exceptions import GatewayBusy
from core.storage.postgres.common import LOCK_POLL_INTERVAL_S
from core.storage.postgres.pool import PgPool


class PgGatewayLockProvider:
    """One holder per owner: a transaction-scoped advisory lock polled every 100 ms.

    A dedicated connection (outside the pool) holds the transaction for as long as the lock is
    held; commit, rollback or a dropped connection releases it. Timeout raises GatewayBusy before
    the body runs; an unreachable database raises StorageUnavailable.
    """

    def __init__(self, pool: PgPool, owner: str, default_timeout_s: float = 15.0) -> None:
        self._pool = pool
        self._owner = owner
        self._default_timeout_s = float(default_timeout_s)

    def held(self, timeout_s: float | None = None) -> AbstractContextManager[None]:
        timeout = self._default_timeout_s if timeout_s is None else float(timeout_s)
        return self._held(timeout)

    @contextmanager
    def _held(self, timeout: float) -> Iterator[None]:
        conn = self._pool.connect()
        try:
            deadline = time.monotonic() + timeout
            while True:
                row = conn.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("aw:gateway:" + self._owner,),
                ).fetchone()
                if row is not None and row[0]:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GatewayBusy(f"Gateway lock timeout ({timeout}s) for owner {self._owner}")
                time.sleep(min(LOCK_POLL_INTERVAL_S, remaining))
            yield
        finally:
            try:
                conn.rollback()
            except psycopg.Error:
                pass
            conn.close()
