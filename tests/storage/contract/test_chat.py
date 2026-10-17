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


def test_job_kind_can_be_updated_from_action_to_inquiry(stores) -> None:
    jobs = stores.jobs
    job_id = "a" * 32
    jobs.create(job_id, "chat_action", NOW)
    assert jobs.get(job_id)["kind"] == "chat_action"
    jobs.update(job_id, kind="chat_inquiry", status="succeeded")
    assert jobs.get(job_id)["kind"] == "chat_inquiry"


def test_inquiry_limit_enforced_by_create(stores) -> None:
    from core.storage.interfaces import RateLimited

    jobs = stores.jobs
    for i in range(2):
        jid = f"inq{i:029x}"
        jobs.create(jid, "chat_inquiry", NOW + timedelta(minutes=i))
        jobs.update(jid, status="succeeded")
    with pytest.raises(RateLimited):
        jobs.create("b" * 32, "chat_action", NOW + timedelta(minutes=3), inquiry_limit=2)


# ---------------------------------------------------------------- threads (ticket 123)


def test_thread_starts_empty_and_titled_new_chat(stores) -> None:
    thread = stores.chat.create_thread(NOW)
    assert thread["title"] == "New chat"
    assert stores.chat.thread_exists(thread["id"])
    assert stores.chat.thread_exists("default")
    assert not stores.chat.thread_exists("nope")
    assert stores.chat.list(thread_id=thread["id"]) == []
    assert stores.chat.list_threads() == [
        {
            "id": thread["id"],
            "title": "New chat",
            "last_message_at": "2026-10-14T12:00:00Z",
            "status": "done",
        }
    ]


def test_attach_job_moves_both_messages_and_titles_the_thread(stores) -> None:
    thread = stores.chat.create_thread(NOW)
    stores.chat.append("user", "x" * 60, "j" * 32, NOW)
    stores.chat.attach_job(thread["id"], "j" * 32)
    agent = stores.chat.append("agent", "done", "j" * 32, NOW + timedelta(seconds=1))
    assert [m["text"] for m in stores.chat.list(thread_id=thread["id"])][-1] == "done"
    assert stores.chat.list() == []  # nothing left in the default thread
    assert agent["job_id"] == "j" * 32
    item = stores.chat.list_threads()[0]
    assert item["title"] == "x" * 40
    assert item["last_message_at"] == "2026-10-14T12:00:01Z"
    # A later message does not rename the thread.
    stores.chat.append("user", "second", "k" * 32, NOW + timedelta(seconds=5))
    stores.chat.attach_job(thread["id"], "k" * 32)
    assert stores.chat.list_threads()[0]["title"] == "x" * 40


def test_default_thread_lists_only_when_it_has_messages(stores) -> None:
    assert stores.chat.list_threads() == []
    stores.chat.append("user", "hello there", None, NOW)
    item = stores.chat.list_threads()[0]
    assert item["id"] == "default" and item["title"] == "hello there"


def test_threads_are_newest_first_and_limited(stores) -> None:
    ids = [stores.chat.create_thread(NOW + timedelta(seconds=i))["id"] for i in range(3)]
    assert [t["id"] for t in stores.chat.list_threads()] == ids[::-1]
    assert len(stores.chat.list_threads(limit=2)) == 2


def test_owners_never_see_each_others_threads(backend) -> None:
    a, b = backend.open(OWNER_A), backend.open(OWNER_B)
    thread = a.chat.create_thread(NOW)
    a.chat.append("user", "secret", "a" * 32, NOW)
    a.chat.attach_job(thread["id"], "a" * 32)
    assert b.chat.list_threads() == []
    assert not b.chat.thread_exists(thread["id"])
    assert b.chat.list(thread_id=thread["id"]) == []
    b.chat.attach_job(thread["id"], "a" * 32)  # a foreign id changes nothing
    assert [m["text"] for m in a.chat.list(thread_id=thread["id"])] == ["secret"]


def test_newest_empty_thread_returns_empty_and_ignores_non_empty(stores) -> None:
    assert stores.chat.newest_empty_thread() is None
    thread1 = stores.chat.create_thread(NOW)
    assert stores.chat.newest_empty_thread() == {"id": thread1["id"], "title": "New chat"}
    stores.chat.append("user", "hello", "a" * 32, NOW)
    stores.chat.attach_job(thread1["id"], "a" * 32)
    assert stores.chat.newest_empty_thread() is None
    thread2 = stores.chat.create_thread(NOW + timedelta(seconds=1))
    assert stores.chat.newest_empty_thread() == {"id": thread2["id"], "title": "New chat"}


def test_newest_empty_thread_isolated_between_owners(backend) -> None:
    a, b = backend.open(OWNER_A), backend.open(OWNER_B)
    t = a.chat.create_thread(NOW)
    assert a.chat.newest_empty_thread() == {"id": t["id"], "title": "New chat"}
    assert b.chat.newest_empty_thread() is None


def test_just_created_empty_thread_sorts_first(stores) -> None:
    stores.chat.append("user", "legacy hello", None, NOW)
    thread = stores.chat.create_thread(NOW)
    threads = stores.chat.list_threads()
    assert len(threads) == 2
    assert threads[0]["id"] == thread["id"]
    assert threads[0]["last_message_at"] == "2026-10-14T12:00:00Z"
    assert threads[1]["id"] == "default"
    assert threads[1]["last_message_at"] == "2026-10-14T12:00:00Z"


def test_append_with_intent_roundtrips_and_user_message_defaults_without_intent(stores) -> None:
    first = stores.chat.append("user", "Hello", "j1", NOW)
    second = stores.chat.append(
        "agent", "I did it", "j1", NOW + timedelta(seconds=1), intent="action"
    )
    third = stores.chat.append(
        "agent", "What did you mean?", "j2", NOW + timedelta(seconds=2), intent="clarify"
    )

    assert "intent" not in first or first["intent"] is None
    assert second["intent"] == "action"
    assert third["intent"] == "clarify"

    messages = stores.chat.list()
    assert len(messages) == 3
    assert messages[0].get("intent") is None
    assert messages[1]["intent"] == "action"
    assert messages[2]["intent"] == "clarify"


def test_get_thread_id_returns_thread_of_user_message(stores) -> None:
    assert stores.chat.get_thread_id("j_missing") is None
    stores.chat.append("user", "Hello in default", "j_def", NOW)
    assert stores.chat.get_thread_id("j_def") is None

    thread = stores.chat.create_thread(NOW)
    stores.chat.append("user", "Hello in thread", "j_th", NOW, thread_id=thread["id"])
    assert stores.chat.get_thread_id("j_th") == thread["id"]
