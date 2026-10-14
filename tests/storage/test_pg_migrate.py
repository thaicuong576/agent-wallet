"""Migrations: applied once under an advisory lock, idempotent, checksummed (design 5.2)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from core.storage import migrate
from core.storage.migrate import MigrationError, apply_migrations, check_schema, load_migrations
from tests.storage.test_pg_support import (  # noqa: F401
    TEST_DATABASE_URL,
    pg_schema,
    requires_pg,
    schema_dsn,
)


@contextmanager
def empty_schema() -> Iterator[str]:
    schema = "t_" + uuid.uuid4().hex[:16]
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield schema_dsn(schema)
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_migration_files_are_numbered_without_gaps() -> None:
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert all(len(m.checksum) == 64 for m in migrations)


@requires_pg
def test_migrations_apply_once_under_lock(pg_schema) -> None:  # noqa: F811
    # pg_schema applied once already; a crowd of starters applies nothing again.
    results: list[list[int]] = []
    threads = [
        threading.Thread(target=lambda: results.append(apply_migrations(pg_schema.dsn)))
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert results == [[]] * 6
    rows = pg_schema.raw("SELECT version FROM schema_migrations ORDER BY version")
    assert [r[0] for r in rows] == [m.version for m in load_migrations()]


@requires_pg
def test_concurrent_first_start_applies_each_file_once() -> None:
    applied: list[list[int]] = []
    with empty_schema() as dsn:
        threads = [
            threading.Thread(target=lambda: applied.append(apply_migrations(dsn))) for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        flat = sorted(v for batch in applied for v in batch)
        assert flat == [m.version for m in load_migrations()]
        assert check_schema(dsn) == load_migrations()[-1].version


@requires_pg
def test_migration_is_idempotent(pg_schema) -> None:  # noqa: F811
    assert apply_migrations(pg_schema.dsn) == []
    assert apply_migrations(pg_schema.dsn) == []
    assert check_schema(pg_schema.dsn) == load_migrations()[-1].version


@requires_pg
def test_checksum_mismatch_refuses_start(pg_schema, tmp_path: Path) -> None:  # noqa: F811
    original = (migrate.MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    (tmp_path / "0001_init.sql").write_text(original + "\n-- edited later\n", encoding="utf-8")
    edited = load_migrations(tmp_path)
    with pytest.raises(MigrationError, match="Checksum mismatch"):
        apply_migrations(pg_schema.dsn, edited)
    with pytest.raises(MigrationError, match="Checksum mismatch"):
        check_schema(pg_schema.dsn, edited)


@requires_pg
def test_database_newer_than_code_refuses_start(pg_schema) -> None:  # noqa: F811
    pg_schema.raw(
        "INSERT INTO schema_migrations (version, name, checksum) VALUES (99, 'future', 'x')"
    )
    with pytest.raises(MigrationError, match="newer"):
        apply_migrations(pg_schema.dsn)
    with pytest.raises(MigrationError, match="newer"):
        check_schema(pg_schema.dsn)


@requires_pg
def test_check_fails_closed_when_behind(pg_schema, tmp_path: Path) -> None:  # noqa: F811
    original = (migrate.MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    (tmp_path / "0001_init.sql").write_text(original, encoding="utf-8")
    (tmp_path / "0002_next.sql").write_text("CREATE TABLE next_thing (id int);\n", encoding="utf-8")
    ahead = load_migrations(tmp_path)
    with pytest.raises(MigrationError, match="behind"):
        check_schema(pg_schema.dsn, ahead)
    assert apply_migrations(pg_schema.dsn, ahead) == [2]
    assert check_schema(pg_schema.dsn, ahead) == 2


@requires_pg
def test_check_fails_closed_on_unmigrated_database() -> None:
    with empty_schema() as dsn:
        with pytest.raises(MigrationError, match="not migrated"):
            check_schema(dsn)
