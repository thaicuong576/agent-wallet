"""Static owner UI: served at /ui, offline-safe, contract endpoints only, chain check."""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from api.main import app

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
CHAIN = ROOT / "contracts" / "examples" / "decision-record.chain.json"
UI_FILES = ["index.html", "style.css", "app.js", "chain.js"]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("name", UI_FILES)
def test_static_files_are_served(client: TestClient, name: str) -> None:
    res = client.get(f"/ui/{name}")
    assert res.status_code == 200
    assert res.content == (UI / name).read_bytes()


def test_ui_root_serves_index(client: TestClient) -> None:
    res = client.get("/ui/")
    assert res.status_code == 200
    assert "agent-wallet owner console" in res.text


@pytest.mark.parametrize("name", UI_FILES)
def test_no_local_storage_and_no_external_url(name: str) -> None:
    text = (UI / name).read_text(encoding="utf-8")
    for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert banned not in text
    assert not re.search(r"https?://|//cdn\.|@import|@font-face", text)
    for banned in ("innerHTML", "outerHTML", "document.write", "eval("):
        assert banned not in text


def test_js_endpoints_exist_in_openapi() -> None:
    js = (UI / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const PATHS = \{(.*?)\};", js, re.S)
    assert block, "PATHS block not found in ui/app.js"
    paths = re.findall(r'"(/[^"]*)"', block.group(1))
    assert paths
    spec = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    for path in paths:
        assert path in spec["paths"], path


def test_js_read_endpoints_respond(client: TestClient) -> None:
    assert client.get("/tier").status_code == 200
    assert client.get("/decisions?limit=200").status_code == 200


def py_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def test_example_chain_hashes_in_python() -> None:
    records = json.loads(CHAIN.read_text(encoding="utf-8"))
    assert len(records) == 3
    assert [py_hash(r) for r in records] == [r["hash"] for r in records]


def run_js_check(path: pathlib.Path) -> dict:
    script = ROOT / "tests" / "ui" / "chain_check.js"
    out = subprocess.run(
        ["node", str(script), str(path)], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_js_chain_check_accepts_example_chain() -> None:
    result = run_js_check(CHAIN)
    assert result["firstBad"] is None
    assert len(result["rows"]) == 3
    assert all(r["linkOk"] and r["hashOk"] for r in result["rows"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_js_chain_check_names_first_bad_seq(tmp_path: pathlib.Path) -> None:
    records = copy.deepcopy(json.loads(CHAIN.read_text(encoding="utf-8")))
    records[1]["decision"]["reason"] = "tampered"
    bad = tmp_path / "tampered.json"
    bad.write_text(json.dumps(records), encoding="utf-8")
    result = run_js_check(bad)
    assert result["firstBad"] == 1
    assert result["rows"][1]["hashOk"] is False
    assert result["rows"][1]["linkOk"] is True
