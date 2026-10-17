"""Migration 0004: chat_messages with row-level security and no grant to API roles."""

from __future__ import annotations

import re

from core.storage.migrate import MIGRATIONS_DIR, load_migrations

SQL_0004 = (MIGRATIONS_DIR / "0004_chat.sql").read_text(encoding="utf-8")
BODY = "\n".join(line.split("--", 1)[0] for line in SQL_0004.splitlines())


def test_migration_is_the_fourth_file() -> None:
    assert (4, "chat") in [(m.version, m.name) for m in load_migrations()]


def test_rls_is_enabled_with_an_owner_bound_policy() -> None:
    assert re.search(r"ALTER TABLE chat_messages\s+ENABLE ROW LEVEL SECURITY;", BODY)
    policy = re.search(r"CREATE POLICY[^;]*;", BODY)
    assert policy is not None
    assert "app.owner" in policy.group(0)
    assert "DISABLE ROW LEVEL SECURITY" not in BODY and "NO FORCE" not in BODY


def test_no_api_role_gets_anything() -> None:
    assert not re.search(r"\b(anon|authenticated|PUBLIC|public)\b", BODY)
    grants = re.findall(r"GRANT[^;]*;", BODY)
    assert grants and all("TO aw_app" in g for g in grants)
    assert not re.search(r"GRANT[^;]*\b(UPDATE|DELETE|ALL)\b", BODY)


def test_table_keeps_ids_and_text_only() -> None:
    columns = re.findall(r"^\s{2}(\w+)\s+(?:text|bigint|timestamptz)", BODY, re.M)
    assert columns == ["owner", "seq", "id", "role", "text", "job_id", "created_at"]
