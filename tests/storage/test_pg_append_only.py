"""Append-only tables and the chain under concurrency."""

from __future__ import annotations

import threading

import psycopg
import pytest
from psycopg import sql

from core.audit.verify import verify_chain
from core.storage.postgres import PostgresBackend
from tests.storage.contract.conftest import OWNER_A, OWNER_B, make_record
from tests.storage.test_pg_support import pg_schema, requires_pg  # noqa: F401

pytestmark = requires_pg

APPEND_ONLY = ("audit_records", "order_intents", "policy_selection")


def _seed(pg) -> None:
    be = PostgresBackend(pg.dsn)
    try:
        t = be.open_tenant(OWNER_A)
        t.audit.append(make_record(order_id="o-1"))
        t.intents.begin_signing("o-1", "h", "2026-10-14T10:00:00Z")
        t.policy_selection.append("balanced", "ips", "sig")
    finally:
        be.close()


def test_app_role_cannot_update_delete_truncate(pg_schema) -> None:  # noqa: F811
    _seed(pg_schema)
    with psycopg.connect(pg_schema.dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO aw_app").format(sql.Identifier(pg_schema.schema))
        )
        conn.execute("SET ROLE aw_app")
        conn.execute("SELECT set_config('app.owner', %s, false)", (OWNER_A,))
        for table in APPEND_ONLY:
            ident = sql.Identifier(table)
            for stmt in (
                sql.SQL("UPDATE {} SET owner = owner").format(ident),
                sql.SQL("DELETE FROM {}").format(ident),
                sql.SQL("TRUNCATE {}").format(ident),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(stmt)
        # the role can still read what it appended
        assert conn.execute("SELECT count(*) FROM audit_records").fetchone()[0] == 1


def test_triggers_reject_even_the_table_owner(pg_schema) -> None:  # noqa: F811
    _seed(pg_schema)
    for table in APPEND_ONLY:
        for stmt in (
            f"UPDATE {table} SET owner = owner",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                pg_schema.raw(stmt)
    assert pg_schema.raw("SELECT count(*) FROM audit_records")[0][0] == 1


def test_concurrent_appends_serialised_per_owner(pg_schema) -> None:  # noqa: F811
    # Two backends stand in for two processes: separate pools, separate sessions.
    backends = [PostgresBackend(pg_schema.dsn, pool_size=4) for _ in range(2)]
    per_thread, threads_per_backend = 10, 4
    errors: list[BaseException] = []

    def work(be: PostgresBackend, owner: str) -> None:
        try:
            tenant = be.open_tenant(owner)
            for _ in range(per_thread):
                tenant.audit.append(make_record())
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    try:
        threads = [
            threading.Thread(target=work, args=(be, owner))
            for be in backends
            for _ in range(threads_per_backend)
            for owner in (OWNER_A, OWNER_B)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert errors == []
        expected = per_thread * threads_per_backend * len(backends)
        for owner in (OWNER_A, OWNER_B):
            records = list(backends[0].open_tenant(owner).audit.iter_all())
            assert [r["seq"] for r in records] == list(range(expected))
            assert verify_chain(records).valid
    finally:
        for be in backends:
            be.close()


def test_forked_chain_is_a_unique_violation(pg_schema) -> None:  # noqa: F811
    _seed(pg_schema)
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg_schema.raw(
            "INSERT INTO audit_records (owner, seq, outcome, created_at, prev_hash, hash, payload)"
            " VALUES (%s, 0, 'x', now(), %s, %s, '{}')",
            (OWNER_A, "0" * 64, "f" * 64),
        )
