"""Row-level security and per-owner isolation on Postgres (design 5.2, 5.4, 19)."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql

from core.storage.postgres import PostgresBackend
from tests.storage.contract.conftest import OWNER_A, OWNER_B, make_record
from tests.storage.test_pg_support import pg_schema, requires_pg  # noqa: F401

pytestmark = requires_pg

TENANT_TABLES = (
    "audit_records",
    "order_intents",
    "policy_selection",
    "tier_state",
    "approval_queue",
    "keystores",
)


def _order(owner: str) -> str:
    return "o-" + owner[2:5]


def _seed(pg) -> None:
    be = PostgresBackend(pg.dsn)
    try:
        for owner in (OWNER_A, OWNER_B):
            t = be.open_tenant(owner)
            t.audit.append(make_record(order_id=_order(owner)))
            t.intents.begin_signing(_order(owner), "h", "2026-10-14T10:00:00Z")
            t.policy_selection.append("balanced", "ips", "sig")
            t.tier_write.set_tier("T1")
            t.tier_write.request_approval(_order(owner))
            t.keystore.put(owner, '{"crypto": {"kdf": "scrypt"}}')
    finally:
        be.close()


def _app_connection(pg) -> psycopg.Connection:
    conn = psycopg.connect(pg.dsn, autocommit=True)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO aw_app").format(sql.Identifier(pg.schema)))
    conn.execute("SET ROLE aw_app")
    return conn


def test_rls_blocks_select_without_owner_setting(pg_schema) -> None:  # noqa: F811
    _seed(pg_schema)
    with _app_connection(pg_schema) as conn:
        for table in TENANT_TABLES:
            query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            assert conn.execute(query).fetchone()[0] == 0, table


def test_rls_blocks_cross_owner_select(pg_schema) -> None:  # noqa: F811
    _seed(pg_schema)
    with _app_connection(pg_schema) as conn:
        conn.execute("SELECT set_config('app.owner', %s, false)", (OWNER_A,))
        for table in TENANT_TABLES:
            query = sql.SQL("SELECT DISTINCT owner FROM {}").format(sql.Identifier(table))
            assert {r[0] for r in conn.execute(query).fetchall()} == {OWNER_A}, table


def test_rls_blocks_cross_owner_insert(pg_schema) -> None:  # noqa: F811
    with _app_connection(pg_schema) as conn:
        conn.execute("SELECT set_config('app.owner', %s, false)", (OWNER_A,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO order_intents (owner, order_id, body_hash, state, created_at) "
                "VALUES (%s, 'x', 'h', 'signing', now())",
                (OWNER_B,),
            )


def test_adapter_owner_cannot_reach_other_owner(pg_schema) -> None:  # noqa: F811
    _seed(pg_schema)
    be = PostgresBackend(pg_schema.dsn)
    try:
        a, b = be.open_tenant(OWNER_A), be.open_tenant(OWNER_B)
        assert a.audit.find_by_order_id(_order(OWNER_B)) is None
        assert a.tier_read.get_approval(_order(OWNER_B)) is None
        assert a.keystore.get(OWNER_A) is not None
        with pytest.raises(ValueError):
            a.keystore.get(OWNER_B)
        assert list(a.keystore.owners()) == [OWNER_A]
        assert sorted(be.keystore_admin().owners()) == sorted([OWNER_A, OWNER_B])
        assert b.keystore.exists(OWNER_B)
    finally:
        be.close()


def test_invalid_owner_is_rejected(pg_schema) -> None:  # noqa: F811
    be = PostgresBackend(pg_schema.dsn)
    try:
        for bad in ("0x" + "A" * 40, "../etc", "0x" + "a" * 39, "", "0x" + "a" * 40 + "' OR 1=1"):
            with pytest.raises(ValueError):
                be.open_tenant(bad)
    finally:
        be.close()
