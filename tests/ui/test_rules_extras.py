"""Rules page extras: permission card, safety demo and protocol chips."""

from __future__ import annotations

import functools
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
MODULE = UI / "views" / "rules.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


def test_rules_module_has_no_unsafe_api_or_external_url() -> None:
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


def test_rules_module_says_the_demo_is_scripted() -> None:
    src = MODULE.read_text(encoding="utf-8")
    assert "scripted demo, not the live model" in src
    assert '"Run the demo"' in src


@functools.lru_cache(maxsize=1)
def rules() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "rules_check.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_t2_shows_the_permission_card_with_two_lower_buttons() -> None:
    t2 = rules()["t2"]
    assert t2["exists"] is True
    assert t2["text"] == "The agent is active and may act inside these limits."
    assert len(t2["lowerButtons"]) == 2
    assert t2["links"] == 0


@needs_node
def test_t1_shows_the_propose_only_text_and_an_activate_link() -> None:
    t1 = rules()["t1"]
    assert t1["exists"] is True
    assert (
        t1["text"] == "The agent may only propose orders. Nothing runs until you activate it again."
    )
    assert t1["link"] == {"text": "Activate again", "href": "#/setup"}
    assert len(t1["lowerButtons"]) == 1


@needs_node
def test_no_permission_card_for_t0_emergency_stop_or_a_failing_tier_read() -> None:
    no = rules()["noCard"]
    assert no["t0"] is True
    assert no["stopped"] is True
    assert no["tierError"] is True
    assert no["tierErrorRowsStillShown"] == 5


@needs_node
def test_demo_card_runs_the_scripted_hostile_planner_once() -> None:
    demo = rules()["demo"]
    assert "scripted demo, not the live model" in demo["text"]
    assert demo["runCount"] == 1
    assert demo["runsDuringClick"] == 1
    assert demo["body"] == {"planner": "scripted_hostile", "noteStarts": True}


@needs_node
def test_demo_button_is_disabled_while_the_run_is_pending_and_enabled_after() -> None:
    demo = rules()["demo"]
    assert demo["disabledDuringRun"] is True
    assert demo["disabledAfterRun"] is False


@needs_node
def test_demo_card_is_not_rebuilt_by_a_reload() -> None:
    assert rules()["demo"]["sameCardAfterReload"] is True


@needs_node
def test_protocol_chips_carry_the_name_and_a_protocol_avatar() -> None:
    chips = rules()["chips"]
    assert chips["known"] == [
        {"name": "Aave v3", "avatar": 1},
        {"name": "Morpho Blue", "avatar": 1},
    ]


@needs_node
def test_unknown_protocol_id_gets_a_chip_with_the_id_and_no_throw() -> None:
    chips = rules()["chips"]
    assert chips["unknown"] == [
        {"name": "foo-v9", "avatar": 1},
        {"name": "Aave v3", "avatar": 1},
    ]
