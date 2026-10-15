"""Migration 0005: chat_threads and chat_messages.thread_id, protected like chat_messages
(ticket 123)."""

from __future__ import annotations

import re

from core.storage.migrate import MIGRATIONS_DIR, load_migrations

SQL_0005 = (MIGRATIONS_DIR / "0005_chat_threads.sql").read_text(encoding="utf-8")
BODY = chr(10).join(line.split("--", 1)[0] for line in SQL_0005.splitlines())


def test_migration_is_the_fifth_file() -> None:
    assert (5, "chat_threads") in [(m.version, m.name) for m in load_migrations()]


def test_threads_table_has_rls_with_an_owner_bound_policy() -> None:
    assert re.search(r"ALTER TABLE chat_threads\s+ENABLE ROW LEVEL SECURITY;", BODY)
    policy = re.search(r"CREATE POLICY[^;]*;", BODY)
    assert policy is not None and "app.owner" in policy.group(0)
    assert "DISABLE ROW LEVEL SECURITY" not in BODY


def test_thread_id_is_nullable_and_additive() -> None:
    assert re.search(r"ALTER TABLE chat_messages ADD COLUMN thread_id text;", BODY)
    assert "DROP" not in BODY and "NOT NULL DEFAULT" not in BODY.split("chat_messages ADD")[1][:60]


def test_no_api_role_gets_anything_and_message_text_stays_immutable() -> None:
    assert not re.search(r"(anon|authenticated|PUBLIC|public)", BODY)
    grants = re.findall(r"GRANT[^;]*;", BODY)
    assert grants and all("TO aw_app" in g for g in grants)
    assert not re.search(r"GRANT[^;]*(DELETE|ALL)", BODY)
    assert "UPDATE (thread_id) ON chat_messages" in BODY
    assert not re.search(r"GRANT\s+UPDATE\s+ON", BODY)
