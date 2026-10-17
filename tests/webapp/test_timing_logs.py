"""Tests for timing visibility and UI cache headers (Ticket F).

Verifies:
- configure_logging is idempotent and respects AGENT_WALLET_LOG_LEVEL;
- agent_wallet.http logs per-request timing and excludes /health and /ui/*;
- agent_wallet.rpc logs method, elapsed_ms, and status without params or URL;
- contextvars accumulators (rpc_calls, rpc_ms) add up;
- /ui/ responses include Cache-Control: no-cache and retain 200/304 ETag behavior;
- Log records never contain sensitive headers, cookies, or request bodies.
"""

from __future__ import annotations

import http.server
import json
import logging
import sys
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.jobs import JobLimits, JobService
from api.main import (
    app,
    configure_logging,
    http_rpc,
    rpc_calls,
    rpc_ms,
)


def test_configure_logging_idempotent_and_sets_level() -> None:
    root_handlers_before = list(logging.getLogger().handlers)

    # 1. Configures debug level
    logger = configure_logging({"AGENT_WALLET_LOG_LEVEL": "DEBUG"})
    assert logger.level == logging.DEBUG
    assert logger.propagate is False

    stdout_handlers_1 = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
    ]
    assert len(stdout_handlers_1) == 1

    # 2. Idempotent: re-configuring updates level without adding duplicate handlers
    configure_logging({"AGENT_WALLET_LOG_LEVEL": "WARNING"})
    assert logger.level == logging.WARNING

    stdout_handlers_2 = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
    ]
    assert len(stdout_handlers_2) == 1

    # 3. Root logger untouched
    assert logging.getLogger().handlers == root_handlers_before

    # Reset back to default INFO
    configure_logging({"AGENT_WALLET_LOG_LEVEL": "INFO"})


def test_http_timing_middleware_logs_request_and_excludes_health_and_ui(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(app)

    with caplog.at_level(logging.INFO, logger="agent_wallet.http"):
        caplog.clear()

        # Non-excluded request with query parameters
        resp = client.get("/presets?sample=query&test=1")
        assert resp.status_code == 200

        http_records = [r for r in caplog.records if r.name == "agent_wallet.http"]
        assert len(http_records) == 1
        msg = http_records[0].getMessage()
        assert "method=GET" in msg
        assert "path=/presets" in msg
        assert "sample=query" not in msg  # No query string
        assert "status=200" in msg
        assert "elapsed_ms=" in msg
        assert "rpc_calls=0" in msg
        assert "rpc_ms=0" in msg

        # Excluded endpoints: /health, /health/deep, /ui/*
        caplog.clear()
        client.get("/health")
        client.get("/health/deep")
        client.get("/ui/index.html")

        http_records = [r for r in caplog.records if r.name == "agent_wallet.http"]
        assert len(http_records) == 0


def test_http_rpc_timing_logs_and_counters(caplog: pytest.LogCaptureFixture) -> None:
    class FakeRpcHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(content_length)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "keep-alive")
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x1"}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeRpcHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        host, port = server.server_address
        rpc = http_rpc(f"http://{host}:{port}/secret_path_should_not_log?auth=forbidden")

        rpc_calls.set(0)
        rpc_ms.set(0)

        with caplog.at_level(logging.INFO, logger="agent_wallet.rpc"):
            caplog.clear()

            # First call
            res1 = rpc("eth_blockNumber", ["super_secret_parameter_123"])
            assert res1 == "0x1"
            assert rpc_calls.get() == 1
            assert rpc_ms.get() >= 0

            # Second call
            res2 = rpc("eth_chainId", [])
            assert res2 == "0x1"
            assert rpc_calls.get() == 2
            assert rpc_ms.get() >= 0

            rpc_records = [r for r in caplog.records if r.name == "agent_wallet.rpc"]
            assert len(rpc_records) == 2

            # Assert log contents
            msg1 = rpc_records[0].getMessage()
            assert "method=eth_blockNumber" in msg1
            assert "status=ok" in msg1
            assert "elapsed_ms=" in msg1
            assert "super_secret_parameter_123" not in msg1
            assert "secret_path_should_not_log" not in msg1
            assert "forbidden" not in msg1

            msg2 = rpc_records[1].getMessage()
            assert "method=eth_chainId" in msg2
            assert "status=ok" in msg2
    finally:
        server.shutdown()
        server.server_close()


def test_ui_cache_control_and_etag() -> None:
    client = TestClient(app)

    # 1. 200 OK with Cache-Control: no-cache and ETag
    resp = client.get("/ui/index.html")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-cache"
    etag = resp.headers.get("etag")
    assert etag is not None

    # 2. 304 Not Modified with Cache-Control: no-cache on conditional request
    resp_304 = client.get("/ui/index.html", headers={"If-None-Match": etag})
    assert resp_304.status_code == 304
    assert resp_304.headers.get("Cache-Control") == "no-cache"


def test_logs_contain_no_secrets_headers_cookies_or_bodies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(app, cookies={"session": "cookie_secret_xyz789"})

    with (
        caplog.at_level(logging.INFO, logger="agent_wallet.http"),
        caplog.at_level(logging.INFO, logger="agent_wallet.rpc"),
    ):
        caplog.clear()

        client.get(
            "/presets",
            headers={"Authorization": "Bearer top_secret_token_abc123"},
        )

        all_log_text = " ".join(r.getMessage() for r in caplog.records)
        assert "top_secret_token_abc123" not in all_log_text
        assert "cookie_secret_xyz789" not in all_log_text
        assert "Authorization" not in all_log_text
        assert "session" not in all_log_text


def test_chat_milestones_logged(caplog: pytest.LogCaptureFixture) -> None:
    service = JobService(
        rpc=MagicMock(),
        gas_state=lambda _addr: (10**18, 10**15),
        global_stop=lambda: False,
        limits=JobLimits(),
    )

    tenant = MagicMock()
    tenant.agent_address = "0x" + "1" * 40
    tenant.tier_view.is_stopped.return_value = False
    tenant.gateway.policy.ips = MagicMock()
    tenant.stores.chat.get_thread_id.return_value = "thread-1"
    tenant.stores.chat.list.return_value = []
    store = MagicMock()
    job_id = "a" * 32

    # Mock runner and instruction result
    runner_mock = MagicMock()
    instruction_result = MagicMock()
    instruction_result.records = []
    instruction_result.reply = "Hello"
    instruction_result.plan_reason = None
    instruction_result.validation_errors = []
    instruction_result.intent = "greeting"

    def fake_run_instruction(*args: Any, **kwargs: Any) -> Any:
        on_token = kwargs.get("on_token")
        if on_token:
            on_token("First token")
            on_token("Second token")
        return instruction_result

    runner_mock.run_instruction.side_effect = fake_run_instruction
    service._runner = MagicMock(return_value=runner_mock)  # type: ignore[method-assign]
    service._select_planner = MagicMock(return_value=("mock_planner", MagicMock()))  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="agent_wallet.timing"):
        caplog.clear()
        outcome = service._chat(tenant, store, job_id, {"message": "Hello"})
        assert outcome.status == "succeeded"

        timing_records = [r for r in caplog.records if r.name == "agent_wallet.timing"]
        messages = [r.getMessage() for r in timing_records]

        # Milestone 1: first token
        first_token_msgs = [m for m in messages if "phase=planner_first_token" in m]
        assert len(first_token_msgs) == 1
        assert f"job={job_id}" in first_token_msgs[0]
        assert "elapsed_ms=" in first_token_msgs[0]

        # Milestone 2: chat_total
        total_msgs = [m for m in messages if "phase=chat_total" in m]
        assert len(total_msgs) == 1
        assert f"job={job_id}" in total_msgs[0]
        assert "elapsed_ms=" in total_msgs[0]


def test_http_timing_middleware_logs_db_metrics_and_no_sql_or_owner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="agent_wallet.http"):
        caplog.clear()
        resp = client.get("/presets")
        assert resp.status_code == 200

        records = [r for r in caplog.records if r.name == "agent_wallet.http"]
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "db_calls=" in msg
        assert "db_ms=" in msg
        # Must contain no SQL text, parameters, or owner addresses
        assert "SELECT" not in msg
        assert "FROM" not in msg
        assert "app.owner" not in msg
        assert "0x" not in msg


def test_db_accumulator_per_request_isolation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fastapi import FastAPI

    from api.main import TimingMiddleware
    from core.storage.postgres.pool import _request_db_acc, db_calls, db_ms, db_stats

    db_calls.set(0)
    db_ms.set(0)

    sub_app = FastAPI()

    sub_app.add_middleware(TimingMiddleware)

    @sub_app.get("/req1")
    def req1() -> dict[str, Any]:
        acc = _request_db_acc.get()
        if acc is not None:
            acc[0] += 3
            acc[1] += 45
        return {"ok": True}

    @sub_app.get("/req2")
    def req2() -> dict[str, Any]:
        acc = _request_db_acc.get()
        if acc is not None:
            acc[0] += 1
            acc[1] += 10
        return {"ok": True}

    sub_client = TestClient(sub_app)
    with caplog.at_level(logging.INFO, logger="agent_wallet.http"):
        caplog.clear()
        sub_client.get("/req1")
        sub_client.get("/req2")

        records = [r for r in caplog.records if r.name == "agent_wallet.http"]
        assert len(records) == 2
        msg1 = records[0].getMessage()
        msg2 = records[1].getMessage()
        assert "path=/req1" in msg1
        assert "db_calls=3 db_ms=45" in msg1
        assert "path=/req2" in msg2
        assert "db_calls=1 db_ms=10" in msg2
        assert "SELECT" not in msg1 and "0x" not in msg1
        assert "SELECT" not in msg2 and "0x" not in msg2

    # Outside request, db_stats returns clean defaults
    calls, ms = db_stats()
    assert calls == 0
    assert ms == 0


def test_request_memo_isolation_and_deduplication(tmp_path: Any) -> None:
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from api.main import TimingMiddleware, global_stop_flag, request_memo

    app = FastAPI()
    app.add_middleware(TimingMiddleware)

    settings_mock = MagicMock()
    settings_mock.global_stop.return_value = False

    stop_fn = global_stop_flag(tmp_path, {}, settings=settings_mock)

    observations: list[dict[str, Any]] = []

    @app.get("/memo-test")
    def memo_test() -> dict[str, Any]:
        # Inside request, request_memo is a dict
        memo = request_memo()
        assert isinstance(memo, dict)

        # Call stop_fn twice inside the same request
        res1 = stop_fn()
        res2 = stop_fn()

        observations.append(
            {"res1": res1, "res2": res2, "calls": settings_mock.global_stop.call_count}
        )
        return {"ok": True}

    client = TestClient(app)

    # Outside request, request_memo() is None
    assert request_memo() is None

    # Request 1: calls twice, underlying check called only once
    client.get("/memo-test")
    assert len(observations) == 1
    assert observations[0]["res1"] is False
    assert observations[0]["res2"] is False
    assert observations[0]["calls"] == 1

    # Outside request, request_memo() is None again
    assert request_memo() is None

    # Request 2: underlying check called fresh (never cached across requests)
    client.get("/memo-test")
    assert len(observations) == 2
    assert observations[1]["calls"] == 2

