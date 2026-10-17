"""Funds and withdraw dialogs: static checks on the new files, plus the node check."""

from __future__ import annotations

import functools
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
JS_FILES = [UI / "views" / "dialog.js", UI / "views" / "funds.js", UI / "views" / "withdraw.js"]
CSS = UI / "css" / "overview.css"
CHECK = ROOT / "tests" / "ui" / "dialogs_check.mjs"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_dialog_module_has_no_unsafe_api_or_external_url(path: pathlib.Path) -> None:
    src = path.read_text(encoding="utf-8")
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
def dialogs() -> dict:
    out = subprocess.run(
        [NODE, str(CHECK)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


@needs_node
def test_max_withdraw_values() -> None:
    d = dialogs()
    assert d["maxUsdc"] == "98"
    assert d["maxEth"] == "0.015"
    assert d["maxGarbage"] == "0"
    assert d["maxNull"] == "0"


@needs_node
def test_lent_note_sums_positions_outside_the_wallet() -> None:
    d = dialogs()
    assert d["lent"] == {
        "usd": "2.00",
        "text": "$2.00 is lent or in vaults and cannot be withdrawn directly. "
        "Ask the agent to pull it out first.",
    }
    assert d["lentWalletOnly"] is None
    assert d["lentNull"] is None


@needs_node
def test_add_funds_has_two_tabs_and_selects_the_asked_one() -> None:
    d = dialogs()
    assert d["fundsTitle"] == "Add funds"
    assert d["fundsRole"] == "dialog"
    assert d["fundsTabs"] == ["Send ETH for gas", "Get test USDC"]
    assert d["fundsSelected"] == ["true", "false"]
    assert d["fundsUsdcPanelHidden"] is True
    assert d["usdcSelected"] == ["false", "true"]


@needs_node
def test_gas_tab_sends_the_typed_amount_to_the_agent() -> None:
    d = dialogs()
    assert d["gasDefault"] == "0.1"
    assert d["sendLabel"] == "Send 0.01 ETH from my wallet"
    assert d["gasTx"]["valueWei"] == "10000000000000000"
    assert d["gasTx"]["to"] == "0x" + "2" * 40
    assert d["gasStatus"] == "Added 0.01 ETH of gas."
    assert d["gasStatusClass"] == "msg ok"
    assert d["gasLinkHref"].startswith("https://sepolia.basescan.org/tx/")
    assert d["gasChanged"] == 1
    assert d["gasEnabledAgain"] is True


@needs_node
def test_gas_tab_rejects_a_bad_amount_without_calling_the_wallet() -> None:
    d = dialogs()
    assert d["badAmount"] == "Enter an amount such as 0.01."
    assert d["badAmountCalls"] == 0


@needs_node
def test_mint_button_calls_the_faucet_with_the_default_amount() -> None:
    d = dialogs()
    assert d["mintLabel"] == "Get test USDC"
    assert d["mintDefault"] == "100"
    assert d["mintCalls"] == ["100"]
    assert d["mintStatus"] == "Test USDC added."
    assert d["mintFooter"] == 1


@needs_node
def test_withdraw_signs_and_sends_the_same_amount_object() -> None:
    d = dialogs()
    assert d["withdrawTitle"] == "Withdraw"
    assert d["withdrawMax"] == "Max 98"
    assert d["stepUp"] == [
        "withdraw",
        {"token": "USDC", "amount": {"value": "25000000", "decimals": 6}},
    ]
    assert d["withdrawArgs"] == [
        "USDC",
        {"value": "25000000", "decimals": 6},
        "HDR",
    ]
    assert d["sameObject"] is True
    assert d["withdrawStatus"] == "25 USDC is back in your wallet."
    assert d["withdrawChanged"] == 1
    assert d["ethMax"] == "Max 0.015"


@needs_node
def test_withdraw_over_the_idle_amount_calls_nothing() -> None:
    d = dialogs()
    assert d["overIdle"] == "That is more than the agent holds idle."
    assert d["overIdleCalls"] == 0


@needs_node
def test_open_chat_closes_the_dialog_and_routes_to_chat() -> None:
    d = dialogs()
    assert d["lentShown"] == 1
    assert d["chatGo"] == ["#/chat"]
    assert d["chatClosed"] is True


@needs_node
def test_escape_closes_and_returns_focus_to_the_opener() -> None:
    d = dialogs()
    assert d["focusStart"] == "Close"
    assert d["tabOrder"] == [
        "funds-tab-gas",
        "funds-tab-usdc",
        "funds-eth",
        "Send 0.1 ETH from my wallet",
        "Close",
    ]
    assert d["shiftTab"] == "Send 0.1 ETH from my wallet"
    assert d["escapeRemoved"] is True
    assert d["focusReturned"] is True
    assert d["closedResolved"] is True


@needs_node
def test_no_node_sets_innerhtml() -> None:
    # The fake DOM throws on any innerHTML write, so a finished run proves none happened.
    assert dialogs()["innerHTMLGuard"] == "set-throws"
