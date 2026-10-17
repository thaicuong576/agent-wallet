"""Staged progress card: static checks on progress.js and css/progress.css, plus the node check."""

from __future__ import annotations

import functools
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
MODULE = UI / "views" / "progress.js"
CSS = UI / "css" / "progress.css"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


def test_progress_module_has_no_unsafe_api_or_external_url() -> None:
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


def test_progress_css_has_no_import_shadow_transform_or_animation() -> None:
    css = CSS.read_text(encoding="utf-8")
    for banned in ("@import", "text-transform", "box-shadow", "animation"):
        assert banned not in css, banned


def test_progress_css_only_uses_tokens_defined_in_style_css() -> None:
    css = CSS.read_text(encoding="utf-8")
    style = (UI / "css" / "style.css").read_text(encoding="utf-8")
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", css))
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", style))
    assert used, "progress.css uses no tokens"
    assert used <= defined, sorted(used - defined)


@functools.lru_cache(maxsize=1)
def progress() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "progress_check.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_format_elapsed() -> None:
    assert progress()["fmt"] == ["0 s", "7 s", "59 s", "1 min", "1 min 5 s", "2 min"]


@needs_node
def test_pending_card_shows_slow_post_note_after_2_5_s() -> None:
    p = progress()
    assert p["pending_titles"] == ["Sending your message"]
    assert p["pending_current"] == [True]
    assert p["pending_sub_before"] == ""
    assert p["pending_sub_after"] == "Waking up the server. Your message is safe."


@needs_node
def test_stage_rows_follow_phases_without_duplicates() -> None:
    p = progress()
    assert p["seq_titles"] == [
        "Message received",
        "Waiting to start",
        "Working on your request",
        "Sending to Base Sepolia",
    ]
    assert p["seq_done"] == [True, True, True, False]
    assert p["seq_working_sub"] == (
        "Reading your wallet, planning and checking your rules. Nothing has been sent yet."
    )
    assert p["after_sent_titles"] == [
        "Message received",
        "Sending to Base Sepolia",
        "Working on your request",
    ]
    assert p["after_sent_working_sub"] == ("Reading your wallet, planning and checking your rules.")


@needs_node
def test_row_elapsed_time_shows_only_from_2_s() -> None:
    p = progress()
    assert p["time_received"] == "5 s"
    assert p["time_short"] == ""
    assert p["head"] == "Working on it · 6 s"


@needs_node
def test_long_wait_notes_appear_at_their_thresholds() -> None:
    p = progress()
    texts = p["long_notes"]
    assert p["notes"] == ["", texts[0], texts[1], texts[2]]


@needs_node
def test_stop_button_rules() -> None:
    p = progress()
    assert p["stop_without_handler"] == 0
    assert p["actions_hidden_without_handler"] is True
    assert p["stop_with_handler"] == 1
    assert p["stop_disabled_pending"] is True  # nothing to stop until the server has answered
    assert p["stop_disabled_queued"] is False
    assert p["stop_disabled_sent"] is True
    assert p["stop_calls_while_pending"] == 0  # a disabled button does nothing
    assert p["stop_calls_after_click"] == 1
    assert p["stop_calls_when_sent"] == 1
    assert p["cancel_disabled"] is True
    assert p["cancel_note"] == "Stopping. Steps not sent yet will be skipped."


@needs_node
def test_unknown_phase_reads_as_working() -> None:
    assert progress()["mystery_title"] == "Working on your request"


@needs_node
def test_destroy_stops_the_timer_and_tick() -> None:
    p = progress()
    assert p["intervals_default"] == 0
    assert p["intervals_autotick"] == 1
    assert p["cleared_after_destroy"] == 1
    assert p["head_before_destroy"] == p["head_after_destroy"] == "Working on it · 4 s"
    assert p["cleared_after_detach"] == 1
