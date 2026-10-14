"""Shared Postgres test helpers (no tests here).

Each test gets its own schema in the ``TEST_DATABASE_URL`` database, migrated from the real
migration files and dropped afterwards, so tests never see each other's rows. The append-only
triggers forbid TRUNCATE, which is why isolation is by schema rather than by cleaning tables.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from core.storage.migrate import apply_migrations
from core.storage.postgres import PgTenantStores, PostgresBackend

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
SKIP_REASON = "TEST_DATABASE_URL is not set (Postgres tests run in CI and with docker compose)"
requires_pg = pytest.mark.skipif(not TEST_DATABASE_URL, reason=SKIP_REASON)

PRICES = {"USDC": Decimal("1.00"), "WETH": Decimal("2000.00")}


def _price(token: str) -> Decimal:
    return PRICES[token]


def schema_dsn(schema: str) -> str:
    """The test URL with ``search_path`` pinned to ``schema``."""
    return make_conninfo(TEST_DATABASE_URL, options=f"-c search_path={schema}")


class PgTestSchema:
    """A fresh migrated schema plus the backend and raw access used by the tests."""

    def __init__(self) -> None:
        self.schema = "t_" + uuid.uuid4().hex[:16]
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(self._ident()))
        self.dsn = schema_dsn(self.schema)
        apply_migrations(self.dsn)

    def _ident(self) -> psycopg.sql.Identifier:
        return psycopg.sql.Identifier(self.schema)

    def raw(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Run a statement as the (super)user that owns the schema, outside the adapters."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cur = conn.execute(sql, params)  # type: ignore[call-overload]
            return cur.fetchall() if cur.description else []

    def drop(self) -> None:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(self._ident()))


class PgContractBackend:
    """Backend of the contract suite on Postgres (same surface as the SQLite one)."""

    name = "postgres"
    append_only_error: type[Exception] = psycopg.DatabaseError

    def __init__(self) -> None:
        self.pg = PgTestSchema()
        self.backend = PostgresBackend(self.pg.dsn, price_fn=_price)

    def open(self, owner: str, *, lock_timeout_s: float = 15.0) -> PgTenantStores:
        return self.backend.open_tenant(owner, lock_timeout_s=lock_timeout_s)

    def raw_audit_sql(self, stores: PgTenantStores, sql: str) -> None:
        self.pg.raw(sql)

    def close(self) -> None:
        try:
            self.backend.close()
        finally:
            self.pg.drop()


@pytest.fixture
def pg_schema() -> Iterator[PgTestSchema]:
    if not TEST_DATABASE_URL:
        pytest.skip(SKIP_REASON)
    schema = PgTestSchema()
    try:
        yield schema
    finally:
        schema.drop()


@pytest.fixture
def pg_backend() -> Iterator[PgContractBackend]:
    if not TEST_DATABASE_URL:
        pytest.skip(SKIP_REASON)
    be = PgContractBackend()
    try:
        yield be
    finally:
        be.close()
