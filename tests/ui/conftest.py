"""Module-level fixtures client, flows, chat_flows, harness, cache_flows,
jobs_flows for UI tests.
"""

from __future__ import annotations

import functools
import json
import pathlib
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from api.main import app

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@functools.lru_cache(maxsize=1)
def flows() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "flows.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@functools.lru_cache(maxsize=1)
def chat_flows() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "chat_flows.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@functools.lru_cache(maxsize=1)
def harness() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "harness.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@functools.lru_cache(maxsize=1)
def cache_flows() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "cache_flows.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@functools.lru_cache(maxsize=1)
def jobs_flows() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "jobs_flows.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)
