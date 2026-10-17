"""UI tests for ui/lib/sse.js: static scans and node check against fake streams."""

from __future__ import annotations

import functools
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
MODULE = UI / "lib" / "sse.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


def test_sse_module_has_no_unsafe_api_or_external_url() -> None:
    src = MODULE.read_text(encoding="utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "http://",
        "https://",
    ):
        assert banned not in src, banned


def test_sse_module_has_no_bearer_or_auth_tokens() -> None:
    src = MODULE.read_text(encoding="utf-8")
    for banned in ("Authorization", "Bearer", "window.token", "globalThis.token"):
        assert banned not in src, banned


@functools.lru_cache(maxsize=1)
def sse_results() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "sse_check.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_chunk_boundaries_inside_line() -> None:
    res = sse_results()
    assert res["chunkBoundaries"] == [{"data": '{"phase":"queued"}', "event": "phase", "id": ""}]


@needs_node
def test_crlf_line_terminator() -> None:
    res = sse_results()
    assert res["crlf"]["capturedId"] == "101"
    assert res["crlf"]["messages"] == [{"data": "rules_check", "event": "phase", "id": "101"}]


@needs_node
def test_multiline_data() -> None:
    res = sse_results()
    assert res["multiLine"] == [{"data": "line1\nline2\nline3", "event": "", "id": ""}]


@needs_node
def test_retry_with_last_event_id() -> None:
    res = sse_results()
    retry = res["retry"]
    assert retry["fetchCount"] == 2
    assert retry["recordedHeaders"][0].get("accept") == "text/event-stream"
    assert retry["recordedHeaders"][1].get("Last-Event-ID") == "42"
    assert len(retry["received"]) == 2
    assert retry["received"][0]["event"] == "phase"
    assert retry["received"][1]["event"] == "done"


@needs_node
def test_polling_fallback() -> None:
    res = sse_results()
    fb = res["fallback"]
    assert fb["polled"] is True
    assert "SSE failed" in fb["sseError"]
    assert fb["result"] == {"status": "succeeded", "via": "polling"}
