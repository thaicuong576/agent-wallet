"""Audit chain check tests for python and node verifiers."""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import subprocess

from tests.ui.conftest import ROOT, needs_node

CHAIN = ROOT / "contracts" / "examples" / "decision-record.chain.json"


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


@needs_node
def test_js_chain_check_accepts_example_chain() -> None:
    result = run_js_check(CHAIN)
    assert result["firstBad"] is None
    assert len(result["rows"]) == 3
    assert all(r["linkOk"] and r["hashOk"] for r in result["rows"])


@needs_node
def test_js_chain_check_names_first_bad_seq(tmp_path: pathlib.Path) -> None:
    records = copy.deepcopy(json.loads(CHAIN.read_text(encoding="utf-8")))
    records[1]["decision"]["reason"] = "tampered"
    bad = tmp_path / "tampered.json"
    bad.write_text(json.dumps(records), encoding="utf-8")
    result = run_js_check(bad)
    assert result["firstBad"] == 1
    assert result["rows"][1]["hashOk"] is False
    assert result["rows"][1]["linkOk"] is True
