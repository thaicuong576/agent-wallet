"""ChatStore contract (ticket 115): runs on SQLite and, with TEST_DATABASE_URL, on Postgres."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.storage.contract.conftest import NOW, OWNER_A, OWNER_B


def test_append_returns_the_message_and_list_is_oldest_first(stores) -> None:
    first = stores.chat.append("user", "Put 10 USDC to work", "j" * 32, NOW)
    second = stores.chat.append(
        "agent", "I supplied 10 USDC.", "j" * 32, NOW + timedelta(seconds=2)
    )
    assert first["id"] != second["id"]
    assert first == {
        "id": first["id"],
        "role": "user",
        "text": "Put 10 USDC to work",
        "job_id": "j" * 32,
        "created_at": "2026-10-14T12:00:00Z",
    }
    assert stores.chat.list() == [first, second]


def test_same_second_messages_keep_insertion_order(stores) -> None:
    ids = [stores.chat.append("user", f"m{i}", None, NOW)["id"] for i in range(5)]
    assert [m["id"] for m in stores.chat.list()] == ids


def test_limit_keeps_the_most_recent_oldest_first(stores) -> None:
    for i in range(6):
        stores.chat.append("user" if i % 2 == 0 else "agent", f"m{i}", None, NOW)
    assert [m["text"] for m in stores.chat.list(limit=3)] == ["m3", "m4", "m5"]
    assert stores.chat.list(limit=0) == []
    assert len(stores.chat.list(limit=50)) == 6


def test_job_id_may_be_absent(stores) -> None:
    stores.chat.append("agent", "hello", None, NOW)
    assert stores.chat.list()[0]["job_id"] is None


def test_unknown_role_is_rejected(stores, backend) -> None:
    with pytest.raises(backend.append_only_error):
        stores.chat.append("system", "hi", None, NOW)
    assert stores.chat.list() == []


def test_owners_never_see_each_others_messages(backend) -> None:
    a, b = backend.open(OWNER_A), backend.open(OWNER_B)
    a.chat.append("user", "secret plan", "a" * 32, NOW)
    assert b.chat.list() == []
    b.chat.append("user", "mine", None, NOW)
    assert [m["text"] for m in a.chat.list()] == ["secret plan"]
    assert [m["text"] for m in b.chat.list()] == ["mine"]


def test_messages_survive_reopening_the_store(backend) -> None:
    backend.open(OWNER_A).chat.append("user", "persisted", None, NOW)
    assert [m["text"] for m in backend.open(OWNER_A).chat.list()] == ["persisted"]


def test_one_hourly_limit_can_count_several_kinds(stores) -> None:
    from core.storage.interfaces import RateLimited

    jobs = stores.jobs
    kinds = ("run", "chat")
    for i, kind in enumerate(("run", "chat", "run")):
        job_id = f"{i:032x}"
        jobs.create(job_id, kind, NOW + timedelta(minutes=i), limit=3, count_kinds=kinds)
        jobs.update(job_id, status="succeeded")
    with pytest.raises(RateLimited) as exc:
        jobs.create("f" * 32, "chat", NOW + timedelta(minutes=4), limit=3, count_kinds=kinds)
    assert exc.value.retry_after_s > 0
    # Without count_kinds only the job's own kind is counted, as before.
    jobs.create("e" * 32, "mint", NOW + timedelta(minutes=5), limit=3)
    jobs.update("e" * 32, status="succeeded")
