"""Overview view: static checks on the new files, plus the node check."""

from __future__ import annotations

import functools
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
JS = UI / "views" / "overview.js"
CSS = UI / "css" / "overview.css"
CHECK = ROOT / "tests" / "ui" / "overview_check.mjs"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


def test_overview_js_has_no_unsafe_api_or_external_url() -> None:
    src = JS.read_text(encoding="utf-8")
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


def test_overview_css_has_no_import_shadow_transform_or_animation() -> None:
    src = CSS.read_text(encoding="utf-8")
    for banned in ("@import", "text-transform", "box-shadow", "animation"):
        assert banned not in src, banned


@functools.lru_cache(maxsize=1)
def overview() -> dict:
    out = subprocess.run(
        [NODE, str(CHECK)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout)


@needs_node
def test_fixture_hero_allocation_and_groups() -> None:
    d = overview()["fixture"]
    assert "$100.00" in d["hero"]
    assert "$2.00 working for you" in d["hero"]
    assert "Idle in the wallet" in d["aria"]
    assert "Lent on Aave v3" in d["aria"]
    assert d["groups"] == 2
    assert d["lendingRow"] is not None and "supplied on Aave" in d["lendingRow"]
    assert d["gasChip"] == "● enough"


@needs_node
def test_rich_data_has_five_groups_in_order() -> None:
    d = overview()["rich"]
    assert d["order"] == [
        "Idle in the wallet",
        "Lent on Aave v3",
        "Aave vaults",
        "Morpho Blue",
        "Swapped assets",
    ]


@needs_node
def test_rich_data_protocol_avatars_on_non_idle_headers() -> None:
    d = overview()["rich"]
    assert d["avatars"] == [0, 1, 1, 1, 1]


@needs_node
def test_rich_data_wrapped_ether_badge_and_legend() -> None:
    d = overview()["rich"]
    assert d["wethBadge"] == "W"
    assert d["legend"] == 5


@needs_node
def test_empty_portfolio_shows_sentence_and_no_holdings() -> None:
    d = overview()["empty"]
    assert d["text"] is True
    assert d["holdingsCard"] == 0
    assert "primary" in d["addClass"].split()
    assert d["withdrawDisabled"] is True


@needs_node
def test_signed_out_shows_connect_sentence() -> None:
    assert overview()["signedOut"] is True


@needs_node
def test_portfolio_failure_shows_error_and_no_numbers() -> None:
    d = overview()["portfolioFails"]
    assert d["error"] is True
    assert d["dollarSigns"] == 0
    assert d["retryFound"] is True
    assert d["callsAfter"] == d["callsBefore"] + 1


@needs_node
def test_agent_failure_drops_gas_row_and_disables_hero_buttons() -> None:
    d = overview()["agentFails"]
    assert d["gasRow"] is False
    assert d["addDisabled"] is True
    assert d["withdrawDisabled"] is True
    assert d["total"] is True


@needs_node
def test_emergency_stop_frozen_card_comes_first() -> None:
    assert overview()["frozen"]["firstIsFrozen"] is True


@needs_node
def test_add_funds_and_withdraw_open_their_dialogs() -> None:
    d = overview()["actions"]
    assert d["add"] == "ok"
    assert d["withdraw"] == "ok"
    assert "Add funds" in d["titles"]
    assert "Withdraw" in d["titles"]


@needs_node
def test_no_innerhtml_touched() -> None:
    assert overview()["innerHTMLTouches"] == 0


@needs_node
def test_waiting_note_leads_the_main_column_and_approvals_follow() -> None:
    d = overview()["waiting"]
    assert d["noteClass"] == "waiting-note"
    assert d["noteText"] == "1 order is waiting for you."
    assert d["review"] is True
    assert d["afterNote"] == "Waiting for your approval"


@needs_node
def test_no_pending_order_means_no_waiting_note_and_no_approvals_card() -> None:
    d = overview()["waiting"]
    assert d["noneNotes"] == 0
    assert d["noneApprovals"] is False


@needs_node
def test_side_column_cards_come_in_order() -> None:
    assert overview()["sideTitles"] == ["Your wallet", "Today's limit", "Recent activity"]


@needs_node
def test_wallet_card_shows_short_address_and_network_and_opens_add_funds() -> None:
    d = overview()["wallet"]
    assert d["short"] == "0x1111…1111"
    assert d["network"] is True
    assert d["dialog"] == "ok"
    assert "Add funds" in d["titles"]


@needs_node
def test_wallet_add_funds_is_disabled_with_a_title_without_agent() -> None:
    d = overview()["walletNoAgent"]
    assert d["disabled"] is True
    assert d["title"] != ""


@needs_node
def test_limit_card_shows_amounts_progress_and_rules_link() -> None:
    d = overview()["limit"]
    assert d["spent"] is True
    assert d["limit"] is True
    assert d["role"] == "progressbar"
    assert d["valuenow"] == "3"
    assert d["rules"] == ["See rules", "#/rules"]


@needs_node
def test_limit_card_says_not_available_when_limits_reject() -> None:
    assert overview()["limitNoData"] is True


@needs_node
def test_recent_activity_lists_three_newest_first() -> None:
    d = overview()["recent"]
    assert d["count"] == 3
    assert d["newestFirst"] is True


@needs_node
def test_recent_activity_empty_and_failed_states() -> None:
    d = overview()
    assert d["recentEmpty"] is True
    assert d["recentNoData"] is True
    assert d["recentNoDataLimitOk"] is True


@needs_node
def test_each_side_card_failing_alone_leaves_the_other_two_rendered() -> None:
    d = overview()["isolation"]
    for case in ("wallet", "limit", "recent"):
        assert d[case]["titles"] == ["Your wallet", "Today's limit", "Recent activity"]
        assert d[case]["fallback"] is False
    assert d["wallet"]["limitShows"] is True
    assert d["wallet"]["recentLines"] == 3
    assert d["limit"]["walletShows"] is True
    assert d["limit"]["limitShows"] is False
    assert d["limit"]["recentLines"] == 3
    assert d["recent"]["walletShows"] is True
    assert d["recent"]["limitShows"] is True
    assert d["recent"]["recentLines"] == 0
