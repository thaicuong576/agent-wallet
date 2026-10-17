"""Migration 0006: chat_messages.intent nullable column."""

from __future__ import annotations

import re

from core.storage.migrate import MIGRATIONS_DIR, load_migrations

SQL_0006 = (MIGRATIONS_DIR / "0006_chat_intent.sql").read_text(encoding="utf-8")
BODY = chr(10).join(line.split("--", 1)[0] for line in SQL_0006.splitlines())


def test_migration_is_the_sixth_file() -> None:
    assert (6, "chat_intent") in [(m.version, m.name) for m in load_migrations()]


def test_intent_column_is_nullable_and_additive() -> None:
    assert re.search(r"ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS intent text;", BODY)
    assert "DROP" not in BODY and "NOT NULL DEFAULT" not in BODY


def test_grants_are_restricted_to_aw_app() -> None:
    assert not re.search(r" (anon|authenticated|PUBLIC|public) ", BODY)
    assert "GRANT UPDATE (intent) ON chat_messages TO aw_app;" in BODY
