"""F3 (ticket 131): Rules from GET /limits, emergency-stop confirm, skeleton loaders, log out.

The node flows (tests/ui/f3_flows.mjs) run the real ui modules against a fake DOM; the static
checks guard the stylesheet and the shell markup.
"""

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
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


@functools.lru_cache(maxsize=1)
def f3() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "f3_flows.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_rules_page_has_one_row_per_limit_in_board_order() -> None:
    r = f3()["rules"]
    assert r["rows"] == ["per-order", "per-day", "ask-first", "protocols", "stop"]
    for phrase in (
        "Per order",
        "Per day",
        "Used today $10",
        "Ask me first above",
        "Where money may go",
        "Aave v3",
        "Emergency stop",
        "Read-only here. Changing limits will be a separate, signed step.",
    ):
        assert phrase in r["text"], phrase
    assert r["chips"] == ["On", "On", "On", "On", "Ready"]


@needs_node
def test_money_is_formatted_from_decimal_strings() -> None:
    assert f3()["usd"] == ["$50", "$1,000", "$0.50", "$12.30", "not set", "not set"]


@needs_node
def test_rules_stop_row_and_empty_protocols_are_stated_plainly() -> None:
    rows = f3()["rulesStopped"]
    assert "stop:Stopped:bad" in rows and "protocols:Off:warn" in rows


@needs_node
def test_rules_error_is_honest_and_shows_no_numbers() -> None:
    e = f3()["rulesError"]
    assert "could not be loaded" in e["text"] and "rather than a guess" in e["text"]
    assert e["rows"] == 0 and "$" not in e["text"]


@needs_node
def test_limits_route_is_get_limits_with_cookie_and_csrf() -> None:
    assert f3()["realLimits"] == [["GET", "/limits", "same-origin", "1"]]


@needs_node
def test_stop_confirm_traps_focus_closes_on_escape_and_returns_focus() -> None:
    d = f3()["stopDialog"]
    assert d["role"] == "alertdialog" and d["modal"] == "true" and d["title"] is True
    assert d["buttons"] == [":Keep running", "solid-danger:Stop agent"]
    assert d["openFocus"] == "Keep running"  # the safe choice has focus
    assert d["tabOrder"] == ["Stop agent", "Keep running", "Stop agent"]
    assert d["escape"] == {"result": False, "removed": True, "focusBack": True}
    assert d["confirm"] == {"result": True, "focusBack": True}
    assert d["keep"] is False


def test_stop_confirm_copy_and_flow_are_wired_in_the_shell() -> None:
    main = (UI / "main.js").read_text(encoding="utf-8")
    for text in (
        "Stop the agent now?",
        "It will not move any funds until you resume. Orders already confirmed on-chain are "
        "not undone. Resuming needs one signature from your wallet.",
        "Keep running",
        "Stop agent",
    ):
        assert text in main, text
    # Only engaging the stop asks; the resume path goes straight to its signature.
    assert 'stopBtn.dataset.stopped !== "1"' in main
    assert main.index("confirmDialog(") < main.index("await api.stop();")


@needs_node
def test_skeletons_have_the_final_layout_and_a_status_for_screen_readers() -> None:
    s = f3()["skeleton"]
    assert s["portfolioBusy"] == "true" and "Loading balances" in s["portfolioText"]
    assert s["portfolioCards"] == 5 and s["holdRows"] == 3  # 2 top + holdings + 2 below
    assert s["activityRows"] == 3 and "Loading decisions" in s["activityText"]


def test_skeleton_motion_stops_under_reduced_motion() -> None:
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
    assert "@keyframes shimmer" in css
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)") :]
    assert ".sk { animation: none" in reduced


def test_modal_follows_design_md() -> None:
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
    scrim = re.search(r"\.modal-scrim \{(.*?)\}", css, re.S).group(1)
    assert "rgba(22, 21, 19, 0.45)" in scrim
    card = re.search(r"\.modal \{(.*?)\}", css, re.S).group(1)
    assert "var(--r-card)" in card and "box-shadow" not in card and "var(--border)" in card
    assert re.search(r"button\.solid-danger \{[^}]*var\(--error\)", css)


@needs_node
def test_log_out_returns_to_connect_screen_and_no_protected_fetch_is_retried() -> None:
    lo = f3()["logout"]
    assert lo["before"] is True and lo["label"] == "Log out"
    assert lo["after"] is False and lo["changed"] == 1 and lo["hash"] == "#/chat"
    assert lo["connectScreen"] is True and lo["hasConnectButton"] is True
    # The only request after the click is the logout itself: /me and /agent are not called.
    assert lo["calls"] == ["POST /auth/logout"]
    assert lo["again"] is True  # a new sign-in lifts the block


def test_header_carries_the_account_slot_and_setup_keeps_chip_and_log_out() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    header = html[html.index("<header") : html.index("</header>")]
    assert 'id="account-slot"' in header and header.index("btn-stop") < header.index("account-slot")
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
    assert ":not(#addr-chip):not(#account-slot)" in css  # shown on the Setup screen too
    main = (UI / "main.js").read_text(encoding="utf-8")
    assert "renderAccountControl" in main and "state.loggedOut" in main
    assert "Log out" in (UI / "views" / "account.js").read_text(encoding="utf-8")
