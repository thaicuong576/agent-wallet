"""Migration 0003: row-level security on the six tables the Supabase advisor flags.

The static tests read the SQL and always run; the Postgres tests apply the real migrations and run
when ``TEST_DATABASE_URL`` is set (CI service container).
"""

from __future__ import annotations

import re

import psycopg
import pytest
from psycopg import sql

from core.storage.migrate import MIGRATIONS_DIR, load_migrations
from tests.storage.test_pg_support import pg_schema, requires_pg  # noqa: F401

FLAGGED = (
    "schema_migrations",
    "owners",
    "nonces",
    "sessions",
    "stepup_challenges",
    "settings",
)
SQL_0003 = (MIGRATIONS_DIR / "0003_rls_all_tables.sql").read_text(encoding="utf-8")


def _statements() -> str:
    """The migration without comments, one line per statement fragment."""
    return "\n".join(line.split("--", 1)[0] for line in SQL_0003.splitlines())


def test_migration_files_are_contiguous_and_include_0003() -> None:
    names = [(m.version, m.name) for m in load_migrations()]
    assert (3, "rls_all_tables") in names


@pytest.mark.parametrize("table", FLAGGED)
def test_every_flagged_table_enables_rls(table: str) -> None:
    assert re.search(rf"ALTER TABLE {table}\s+ENABLE ROW LEVEL SECURITY;", _statements()), (
        f"{table} must enable row level security"
    )


def test_policies_are_for_the_app_role_only_and_only_when_it_exists() -> None:
    body = _statements()
    policies = re.findall(r"CREATE POLICY[^;]*;", body)
    assert len(policies) == len(FLAGGED)
    for policy in policies:
        assert re.search(r"\bTO aw_app\b", policy), policy
        assert not re.search(r"\b(anon|authenticated|public|PUBLIC)\b", policy), policy
    # Every policy sits inside the block guarded by the existence of the role.
    guard = body.index("IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aw_app')")
    assert all(body.index(p) > guard for p in policies)
    assert not re.search(r"\b(anon|authenticated)\b", body), "no grant or policy for API roles"
    assert "DISABLE ROW LEVEL SECURITY" not in body and "NO FORCE" not in body


@requires_pg
def test_rls_is_enabled_on_the_six_tables(pg_schema) -> None:  # noqa: F811
    rows = pg_schema.raw(
        "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n "
        "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = ANY(%s)",
        (pg_schema.schema, list(FLAGGED)),
    )
    assert {r[0]: r[1] for r in rows} == dict.fromkeys(FLAGGED, True)


@requires_pg
def test_app_role_keeps_working_and_other_roles_get_nothing(pg_schema) -> None:  # noqa: F811
    owner = "0x" + "a" * 40
    pg_schema.raw(
        "INSERT INTO owners (address, agent_address, created_at) VALUES (%s, %s, now())",
        (owner, "0x" + "b" * 40),
    )
    pg_schema.raw("INSERT INTO settings (key, value) VALUES ('global_stop', 'false')")
    with psycopg.connect(pg_schema.dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO aw_app").format(sql.Identifier(pg_schema.schema))
        )
        conn.execute("SET ROLE aw_app")
        for table in ("owners", "settings", "schema_migrations"):
            query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            assert conn.execute(query).fetchone()[0] >= 1, table
        conn.execute(
            "INSERT INTO nonces (address, nonce, expires_at) VALUES (%s, 'n', now())", (owner,)
        )
        conn.execute("RESET ROLE")
        # A role that has table grants but no policy (what an API role would be) sees no rows.
        conn.execute("DROP ROLE IF EXISTS aw_probe")
        conn.execute("CREATE ROLE aw_probe NOLOGIN NOBYPASSRLS")
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO aw_probe").format(sql.Identifier(pg_schema.schema))
        )
        for table in FLAGGED:
            conn.execute(sql.SQL("GRANT SELECT ON {} TO aw_probe").format(sql.Identifier(table)))
        conn.execute("SET ROLE aw_probe")
        for table in FLAGGED:
            query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            assert conn.execute(query).fetchone()[0] == 0, table
        conn.execute("RESET ROLE")
        for table in FLAGGED:
            conn.execute(sql.SQL("REVOKE ALL ON {} FROM aw_probe").format(sql.Identifier(table)))
        conn.execute(
            sql.SQL("REVOKE USAGE ON SCHEMA {} FROM aw_probe").format(
                sql.Identifier(pg_schema.schema)
            )
        )
        conn.execute("DROP ROLE aw_probe")
