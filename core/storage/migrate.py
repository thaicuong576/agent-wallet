"""Plain versioned SQL migrations for the Postgres backend (design 5.2).

Files are ``core/storage/migrations/NNNN_name.sql``. Each is applied in one transaction that first
takes a transaction-scoped advisory lock, so concurrent starters apply a file exactly once and
nothing relies on session state (safe behind a transaction-mode pooler). The ``schema_migrations``
table records version, name and checksum; a checksum mismatch, or a database that holds a version
the code does not know, refuses to start.

``python -m core.storage.migrate`` applies pending files with ``DATABASE_MIGRATE_URL`` (falling
back to ``DATABASE_URL``); ``--check`` only verifies that the schema is current.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from core.storage.interfaces import StorageUnavailable

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_LOCK_KEY = 7240001
_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """The database and the migration files disagree, or the schema is behind the code."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def load_migrations(directory: Path | str | None = None) -> list[Migration]:
    """Read the migration files in version order. Line endings are normalised before hashing."""
    root = Path(directory) if directory is not None else MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(root.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if match is None:
            raise MigrationError(f"Bad migration file name: {path.name}")
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        found.append(Migration(int(match.group(1)), match.group(2), text, checksum))
    versions = [m.version for m in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Migration versions must be 1..N without gaps, got {versions}")
    return found


def _connect(dsn: str) -> psycopg.Connection:
    try:
        return psycopg.connect(dsn, autocommit=True, prepare_threshold=None)
    except psycopg.OperationalError as exc:
        raise StorageUnavailable("cannot connect to the database") from exc


def _validate(applied: dict[int, str], migrations: list[Migration]) -> None:
    known = {m.version: m for m in migrations}
    for version, checksum in applied.items():
        if version not in known:
            raise MigrationError(
                f"Database has migration {version} that this code does not know (database newer)"
            )
        if known[version].checksum != checksum:
            raise MigrationError(f"Checksum mismatch for migration {version}: file was edited")


def _run_migration_file(conn: psycopg.Connection, text: str) -> None:
    # The one place that executes SQL that is not a literal in the source: a migration file
    # shipped with the code, run without parameters. tests/storage/test_pg_no_string_sql.py
    # allows exactly this function.
    conn.execute(text)  # type: ignore[call-overload]


def apply_migrations(dsn: str, migrations: list[Migration] | None = None) -> list[int]:
    """Apply every pending migration; return the versions applied by this call. Idempotent."""
    wanted = migrations if migrations is not None else load_migrations()
    applied_now: list[int] = []
    conn = _connect(dsn)
    try:
        for migration in wanted:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version integer PRIMARY KEY, name text NOT NULL, "
                    "checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
                )
                rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
                applied = {int(r[0]): str(r[1]) for r in rows}
                _validate(applied, wanted)
                if migration.version in applied:
                    continue
                _run_migration_file(conn, migration.sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
                applied_now.append(migration.version)
    finally:
        conn.close()
    return applied_now


def check_schema(dsn: str, migrations: list[Migration] | None = None) -> int:
    """Return the schema version when it equals the code's; raise MigrationError otherwise.

    For an app role without DDL rights: it verifies and fails closed when the schema is behind.
    """
    wanted = migrations if migrations is not None else load_migrations()
    conn = _connect(dsn)
    try:
        try:
            rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        except psycopg.errors.UndefinedTable as exc:
            raise MigrationError("Schema is not migrated (schema_migrations missing)") from exc
    finally:
        conn.close()
    applied = {int(r[0]): str(r[1]) for r in rows}
    _validate(applied, wanted)
    latest = wanted[-1].version if wanted else 0
    behind = [m.version for m in wanted if m.version not in applied]
    if behind:
        raise MigrationError(f"Schema is behind the code: pending migrations {behind}")
    return latest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply or check Postgres migrations.")
    parser.add_argument("--check", action="store_true", help="verify only, change nothing")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_MIGRATE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_MIGRATE_URL or DATABASE_URL must be set", file=sys.stderr)
        return 2
    try:
        if args.check:
            print(f"schema ok at version {check_schema(dsn)}")
        else:
            applied = apply_migrations(dsn)
            print(f"applied: {applied}" if applied else "schema already current")
    except (MigrationError, StorageUnavailable) as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
