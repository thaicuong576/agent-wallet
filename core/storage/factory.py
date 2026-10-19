"""Backend selection by ``DATABASE_URL``.

Unset selects SQLite (local, replay, tests); set selects Postgres. The SQLite side is built by the
caller (``sqlite_factory``) because its intent store may only be imported inside ``core.gateway``
(``tests/test_intent_boundary.py``); this module therefore never imports it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from core.storage.interfaces import StorageBackend

SQLITE = "sqlite"
POSTGRES = "postgres"


def selected_backend(env: Mapping[str, str] | None = None) -> str:
    """``postgres`` when DATABASE_URL is set and non-empty, otherwise ``sqlite``."""
    source = os.environ if env is None else env
    return POSTGRES if source.get("DATABASE_URL", "").strip() else SQLITE


def create_backend(
    env: Mapping[str, str] | None = None,
    *,
    sqlite_factory: Callable[[], StorageBackend] | None = None,
) -> StorageBackend:
    """Build the StorageBackend the environment selects."""
    source = os.environ if env is None else env
    if selected_backend(source) == POSTGRES:
        # Imported here so the SQLite path never needs the driver.
        from core.storage.postgres import PostgresBackend

        migrate_url = source.get("DATABASE_MIGRATE_URL", "").strip() or None
        return PostgresBackend(source["DATABASE_URL"].strip(), migrate_dsn=migrate_url)
    if sqlite_factory is None:
        raise RuntimeError("DATABASE_URL is unset and no SQLite backend factory was supplied")
    return sqlite_factory()
