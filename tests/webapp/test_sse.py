"""Server-Sent Events tests: formatting, route, ping, done payload."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.jobs import _Live, _ts
from api.sse import (
    DEFAULT_SEPARATOR,
    ServerSentEvent,
    format_sse,
    stream_chat_events,
)
from core.agent.client import ReplyExtractor
from tests.webapp.conftest import ORIGIN
from tests.webapp.test_routes import Owner, World

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def world(tmp_path, auth_env) -> World:
    return World(tmp_path, auth_env)


@pytest.fixture
def alice(world) -> Owner:
    return world.owner()


@pytest.fixture
def bob(world) -> Owner:
    return world.owner()


# ---------------------------------------------------------------- formatting tests


def test_sse_format_basic_event_and_data() -> None:
    event = ServerSentEvent(data="hello world", event="phase", id="1")
    raw = event.encode().decode("utf-8")
    assert f"id: 1{DEFAULT_SEPARATOR}" in raw
    assert f"event: phase{DEFAULT_SEPARATOR}" in raw
    assert f"data: hello world{DEFAULT_SEPARATOR}" in raw
    assert raw.endswith(f"{DEFAULT_SEPARATOR}{DEFAULT_SEPARATOR}")


def test_sse_format_json_data() -> None:
    payload = {"phase": "rules_check", "elapsed_seconds": 1.5}
    raw = format_sse(data=payload, event="phase", id="2").decode("utf-8")
    expected_data = json.dumps(payload, separators=(",", ":"))
    assert f"data: {expected_data}{DEFAULT_SEPARATOR}" in raw


def test_sse_format_multiline_data() -> None:
    raw = ServerSentEvent(data="line1\nline2\r\nline3").encode().decode("utf-8")
    part1 = f"data: line1{DEFAULT_SEPARATOR}"
    part2 = f"data: line2{DEFAULT_SEPARATOR}"
    part3 = f"data: line3{DEFAULT_SEPARATOR}"
    assert f"{part1}{part2}{part3}" in raw


def test_sse_format_comment_ping() -> None:
    raw = ServerSentEvent(comment="ping").encode().decode("utf-8")
    assert raw == f": ping{DEFAULT_SEPARATOR}{DEFAULT_SEPARATOR}"


def test_sse_format_retry() -> None:
    raw = ServerSentEvent(retry=1500).encode().decode("utf-8")
    assert raw == f"retry: 1500{DEFAULT_SEPARATOR}{DEFAULT_SEPARATOR}"

    with pytest.raises(TypeError, match="retry argument must be int"):
        ServerSentEvent(retry="invalid").encode()  # type: ignore[arg-type]


def test_sse_format_cleans_newlines_in_id_and_event() -> None:
    raw = ServerSentEvent(event="phase\r\nevil", id="1\nevil", data="ok").encode().decode("utf-8")
    assert f"id: 1evil{DEFAULT_SEPARATOR}" in raw
    assert f"event: phaseevil{DEFAULT_SEPARATOR}" in raw


# ---------------------------------------------------------------- route tests


def parse_sse_events(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {"event": "message", "data": "", "id": None, "comments": []}
    lines = raw_text.splitlines()

    def _has_content(c: dict[str, Any]) -> bool:
        return (
            bool(c["data"]) or c["event"] != "message" or c["id"] is not None or bool(c["comments"])
        )

    for line in lines:
        if not line:
            if _has_content(current):
                events.append(current)
                current = {"event": "message", "data": "", "id": None, "comments": []}
            continue
        if line.startswith(":"):
            current["comments"].append(line[1:].strip())
        elif ":" in line:
            field, _, val = line.partition(":")
            val = val.lstrip(" ")
            if field == "event":
                current["event"] = val
            elif field == "id":
                current["id"] = val
            elif field == "data":
                current["data"] = (current["data"] + "\n" + val) if current["data"] else val
    if _has_content(current):
        events.append(current)
    return events


def test_chat_stream_unauthenticated(world: World) -> None:
    anon = TestClient(world.app, base_url=ORIGIN)
    resp = anon.get("/chat/stream/" + "a" * 32)
    assert resp.status_code == 401


def test_chat_stream_unknown_job_404(alice: Owner) -> None:
    resp = alice.get("/chat/stream/" + "f" * 32)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["status"] == 404


def test_chat_stream_foreign_job_404(alice: Owner, bob: Owner) -> None:
    post_resp = alice.post("/chat", json={"message": "Put 10 USDC to work in Aave"})
    assert post_resp.status_code == 202
    job_id = post_resp.json()["job_id"]

    # Bob attempts to read Alice's job stream
    bob_resp = bob.get(f"/chat/stream/{job_id}")
    assert bob_resp.status_code == 404
    assert bob_resp.headers["content-type"].startswith("application/problem+json")


def test_chat_stream_headers_and_done_payload_equals_polling(alice: Owner) -> None:
    post_resp = alice.post("/chat", json={"message": "What is my balance?"})
    assert post_resp.status_code == 202
    job_id = post_resp.json()["job_id"]

    alice.world.drain()
    poll_job = alice.get(f"/jobs/{job_id}").json()

    stream_resp = alice.get(f"/chat/stream/{job_id}")
    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"].startswith("text/event-stream")
    assert stream_resp.headers["cache-control"] == "no-cache, no-transform"
    assert stream_resp.headers["x-accel-buffering"] == "no"

    events = parse_sse_events(stream_resp.text)
    done_events = [e for e in events if e.get("event") == "done"]
    assert len(done_events) == 1
    done_data = json.loads(done_events[0]["data"])
    assert done_data == poll_job


@pytest.mark.anyio
async def test_stream_generator_emits_ping(alice: Owner) -> None:
    jobs = alice.world.env.jobs
    job_id = "b" * 32
    store = jobs.store(alice.tenant)
    store.create(job_id, "chat", datetime.now(UTC))
    store.update(job_id, status="running")

    req = MagicMock()
    req.is_disconnected = AsyncMock(side_effect=[False, False, False, True])
    req.app.state.webapp = alice.world.app.state.webapp

    chunks = []
    async for chunk in stream_chat_events(
        req, alice.tenant, job_id, ping_interval=0.01, poll_interval=0.01
    ):
        chunks.append(chunk)

    raw = b"".join(chunks).decode("utf-8")
    assert ": ping\r\n\r\n" in raw
    assert "event: phase" in raw


@pytest.mark.anyio
async def test_disconnect_never_cancels_job(alice: Owner) -> None:
    jobs = alice.world.env.jobs
    job_id = "c" * 32
    store = jobs.store(alice.tenant)
    store.create(job_id, "chat", datetime.now(UTC))
    store.update(job_id, status="running")

    req = MagicMock()
    # Simulate client disconnect on second check
    req.is_disconnected = AsyncMock(side_effect=[False, True])
    req.app.state.webapp = alice.world.app.state.webapp

    async for _ in stream_chat_events(
        req, alice.tenant, job_id, ping_interval=10.0, poll_interval=0.01
    ):
        pass

    # Job row must still be "running" and not cancelled
    row = store.get(job_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["error_code"] is None


@pytest.mark.anyio
async def test_stream_scripted_job_emits_phase_tokens_done_and_polling_unchanged(
    alice: Owner,
) -> None:
    import anyio

    jobs = alice.world.env.jobs
    job_id = "d" * 32
    store = jobs.store(alice.tenant)
    store.create(job_id, "chat", datetime.now(UTC))
    store.update(job_id, status="running")

    live = _Live()
    with jobs._live_lock:
        jobs._live[job_id] = live

    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=False)
    req.app.state.webapp = alice.world.app.state.webapp

    chunks: list[bytes] = []

    async def run_stream():
        async for chunk in stream_chat_events(
            req, alice.tenant, job_id, ping_interval=10.0, poll_interval=0.01
        ):
            chunks.append(chunk)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_stream)
        await anyio.sleep(0.02)

        # Append tokens while job is running
        with jobs._live_lock:
            live.tokens.append("Hello ")
            live.tokens.append("world!")
        await anyio.sleep(0.03)

        # Finish job
        store.update(
            job_id,
            status="executed",
            finished_at=_ts(datetime.now(UTC)),
        )
        await anyio.sleep(0.03)

    raw = b"".join(chunks).decode("utf-8")
    events = parse_sse_events(raw)

    event_types = [e["event"] for e in events]
    assert "phase" in event_types
    assert "token" in event_types
    assert "done" in event_types
    assert event_types.index("phase") < event_types.index("token") < event_types.index("done")

    # Tokens in order
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 2
    assert json.loads(token_events[0]["data"]) == {"text": "Hello "}
    assert json.loads(token_events[1]["data"]) == {"text": "world!"}

    # Increasing ids
    ids = [int(e["id"]) for e in events if e.get("id") is not None]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)

    # Done payload equals polling GET /jobs/{job_id}
    done_event = [e for e in events if e["event"] == "done"][0]
    done_payload = json.loads(done_event["data"])
    poll_job = alice.get(f"/jobs/{job_id}").json()
    assert done_payload == poll_job


@pytest.mark.anyio
async def test_stream_reset_after_fallback(alice: Owner) -> None:
    import anyio

    jobs = alice.world.env.jobs
    job_id = "e" * 32
    store = jobs.store(alice.tenant)
    store.create(job_id, "chat", datetime.now(UTC))
    store.update(job_id, status="running")

    live = _Live()
    with jobs._live_lock:
        jobs._live[job_id] = live

    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=False)
    req.app.state.webapp = alice.world.app.state.webapp

    chunks: list[bytes] = []

    async def run_stream():
        async for chunk in stream_chat_events(
            req, alice.tenant, job_id, ping_interval=10.0, poll_interval=0.01
        ):
            chunks.append(chunk)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_stream)
        await anyio.sleep(0.02)

        # Send partial tokens
        with jobs._live_lock:
            live.tokens.append("partial ")
            live.tokens.append("token ")
        await anyio.sleep(0.03)

        # Fallback clears tokens
        with jobs._live_lock:
            live.tokens.clear()
        await anyio.sleep(0.03)

        # Fallback finishes job
        store.update(
            job_id,
            status="executed",
            finished_at=_ts(datetime.now(UTC)),
        )
        await anyio.sleep(0.03)

    raw = b"".join(chunks).decode("utf-8")
    events = parse_sse_events(raw)

    event_types = [e["event"] for e in events]
    assert "token" in event_types
    assert "reset" in event_types
    assert "done" in event_types

    # Reset event emitted with data={}
    reset_events = [e for e in events if e["event"] == "reset"]
    assert len(reset_events) == 1
    assert json.loads(reset_events[0]["data"]) == {}

    # Reset comes after the tokens that were cleared
    first_token_idx = event_types.index("token")
    reset_idx = event_types.index("reset")
    done_idx = event_types.index("done")
    assert first_token_idx < reset_idx < done_idx

    # Event IDs strictly increase
    ids = [int(e["id"]) for e in events if e.get("id") is not None]
    assert ids == sorted(ids)


@pytest.mark.anyio
async def test_stream_chat_events_emoji_token_encodable(alice: Owner) -> None:
    import anyio

    jobs = alice.world.env.jobs
    job_id = "f" * 32
    store = jobs.store(alice.tenant)
    store.create(job_id, "chat", datetime.now(UTC))
    store.update(job_id, status="running")

    live = _Live()
    with jobs._live_lock:
        jobs._live[job_id] = live

    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=False)
    req.app.state.webapp = alice.world.app.state.webapp

    chunks: list[bytes] = []

    async def run_stream():
        async for chunk in stream_chat_events(
            req, alice.tenant, job_id, ping_interval=10.0, poll_interval=0.01
        ):
            chunks.append(chunk)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_stream)
        await anyio.sleep(0.02)

        # Extract surrogate pair and append to live tokens
        ext = ReplyExtractor()
        token = ext.feed(r'{"reply":"hi \uD83D\uDE00 ok"}')
        with jobs._live_lock:
            live.tokens.append(token)
        await anyio.sleep(0.03)

        store.update(
            job_id,
            status="executed",
            finished_at=_ts(datetime.now(UTC)),
        )
        await anyio.sleep(0.03)

    raw = b"".join(chunks).decode("utf-8")
    events = parse_sse_events(raw)
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert json.loads(token_events[0]["data"]) == {"text": "hi 😀 ok"}
