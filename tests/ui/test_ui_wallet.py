"""Wallet, sign-in, top-up, mint, withdraw and safety flows for the webapp UI."""

from __future__ import annotations

import json
import re

from tests.ui._helpers import DESIGN_ROUTES
from tests.ui.conftest import UI, flows, harness, needs_node

TX = "0x" + "cd" * 32


def test_setup_text_states_testnet_and_custody() -> None:
    source = (UI / "views" / "setup.js").read_text(encoding="utf-8")
    assert (
        "This is a testnet demo: the server holds the agent's key and no real funds are involved."
        in source
    )


def test_wallet_strip_is_built_by_side_js_and_hidden_on_desktop() -> None:
    side = (UI / "views" / "side.js").read_text(encoding="utf-8")
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
    assert "wallet-strip" in side and "aria-expanded" in side
    assert ".wallet-strip" in css and "display: none" in css


def test_stop_visible_and_session_only() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    header = html[html.index("<header") : html.index("</header>")]
    assert 'id="btn-stop"' in header  # in the header, outside #view: on every route
    assert html.index('id="btn-stop"') < html.index('id="view"')
    main = (UI / "main.js").read_text(encoding="utf-8")
    assert "await api.stop();" in main  # engaging needs no signature
    assert "api.release(await ctx.stepUp(" in main  # only the release path asks for one
    assert 'stopBtn.textContent = frozen ? "Resume (needs signature)"' in (
        UI / "lib" / "gauges.js"
    ).read_text(encoding="utf-8")


@needs_node
def test_setup_is_shown_for_t0_and_not_after_activation() -> None:
    r = harness()["routing"]
    assert r["signedOut"] == "setup" and r["t0"] == "setup" and r["t0Portfolio"] == "setup"
    assert r["t2Default"] == "chat" and r["t2Setup"] == "chat"  # activated: land in Chat
    assert r["t1Setup"] == "setup" and r["t2Portfolio"] == "overview"  # #/portfolio is Overview now
    assert r["alias"] == ["overview", "activity", "overview", "chat"]  # old hashes still work
    assert r["unknown"] == "chat" and r["tierUnknown"] == "rules"
    p = harness()["pills"]
    assert p["t0"] == "Read only" and p["t2"].endswith("Agent active") and p["stopped"] == "Stopped"
    assert p["addr"] == "0x9d9d…9d9d"


@needs_node
def test_wallet_rejection_is_not_shown_as_failure() -> None:
    h = harness()
    assert "You declined in your wallet" in h["rejected"]
    assert "Something went wrong" not in h["rejected"]


@needs_node
def test_api_layer_sends_cookie_and_csrf_and_maps_problems() -> None:
    a = harness()["apiError"]
    assert a["status"] == 429 and a["slug"] == "rate-limited"
    assert a["seen"] == [["POST", "/agent/mint", "same-origin", "1"]]
    assert set(harness()["routes"]) <= DESIGN_ROUTES


@needs_node
def test_sign_in_signs_the_exact_message_and_shows_the_agent_address() -> None:
    f = flows()["signIn"]
    assert f["wallet"] == ["connect", "ensureBaseSepolia", "signMessage"]
    assert f["signedMessage"] == "agent-wallet wants you to sign in.\nNonce: " + "n" * 32
    assert f["verifyBody"] == {
        "address": "0x" + "11" * 20,
        "nonce": "n" * 32,
        "hasSig": True,
    }
    assert "0x" + "22" * 20 in f["text"]
    card = flows()["connectCard"]
    assert "Connect your wallet" in card and "Connect wallet" in card
    assert "no transaction is sent" in card


@needs_node
def test_every_real_call_sends_cookie_credentials_and_csrf_header() -> None:
    assert flows()["requests"] is True


@needs_node
def test_top_up_sends_eth_to_agent_registers_hash_and_polls() -> None:
    f = flows()
    assert "Gas floor: about 0.001 ETH" in f["fundShows"]
    assert f["topup"]["sent"] == [
        {"from": "0x" + "11" * 20, "to": "0x" + "22" * 20, "valueWei": "10000000000000000"}
    ]
    assert f["topup"]["calls"] == [
        f"POST /topups {json.dumps({'tx_hash': TX}, separators=(',', ':'))}",
        f"GET /topups/{TX}",
    ]


@needs_node
def test_mint_job_is_polled_and_its_record_links_to_basescan() -> None:
    m = flows()["mint"]
    assert m["polls"] == 3
    assert m["sleeps"] == [750, 750]  # the fast first polls (see pollDelay)
    assert "Executed" in m["text"] and "Minted 1000 USDC" in m["text"]
    external = [u for u in m["links"] if u.startswith("http")]
    assert external == [f"https://sepolia.basescan.org/tx/{TX}"]


@needs_node
def test_poll_interval_is_fast_at_first_then_backs_off() -> None:
    assert flows()["polling"] == [750, 750, 750, 750, 750, 1500]


@needs_node
def test_the_safety_demo_on_rules_shows_the_blocked_decision() -> None:
    f = flows()
    assert f["hostile"]["body"]["planner"] == "scripted_hostile"
    text = f["hostile"]["text"]
    assert "Blocked" in text and "minting and withdrawing are owner actions" in text
    assert "Scripted demo" in text or "scripted planner" in text
    assert "<img src=x onerror=alert(1)>" in text  # API string shown as inert text
    assert f["hostile"]["injected"] == 0


@needs_node
def test_withdraw_signs_the_exact_body_it_sends() -> None:
    w = flows()["withdraw"]
    body = {"token": "USDC", "amount": {"value": "1000000", "decimals": 6}}
    assert w["body"] == body
    assert w["stepUpBody"] == {"action": "withdraw", "params": body}
    assert w["header"].startswith("f" * 32 + ".0x")


@needs_node
def test_stop_is_session_only_and_release_needs_the_step_up_signature() -> None:
    f = flows()
    assert f["stop"]["stepUpHeaders"] == []
    assert f["stop"]["tier"]["emergency_stop"] is True
    assert f["release"]["header"].startswith("f" * 32 + ".0x")
    assert f["release"]["tier"]["emergency_stop"] is False


@needs_node
def test_verify_result_and_pending_approvals_with_step_up() -> None:
    f = flows()
    assert "Hash chain intact (3 records verified)" in f["verify"]
    assert "OpenAI Planner" in f["feedText"]
    assert "Approve (needs signature)" in f["approvalsText"] and "Reject" in f["approvalsText"]
    assert f["approve"]["header"].startswith("f" * 32 + ".0x")
    assert "Nothing waiting for you" in f["approve"]["listAfter"]


@needs_node
def test_problem_bodies_map_to_plain_language() -> None:
    e = flows()["errors"]
    assert "sign in" not in e["401"]["text"] and "Connect your wallet again" in e["401"]["text"]
    assert e["401"]["meCleared"] is True and e["429"]["meCleared"] is False
    assert "still in progress" in e["409"]["text"] and e["409"]["jobId"] == "a" * 32
    assert "Too many requests" in e["429"]["text"] and e["429"]["retryAfter"] == "30"
    assert "temporarily unavailable" in e["503"]["text"]


@needs_node
def test_real_mode_wallet_rejection_sends_nothing() -> None:
    r = flows()["rejected"]
    assert "You declined in your wallet" in r["text"] and r["posts"] == 0


@needs_node
def test_activate_agent_sends_step_up_then_tier_raise_and_updates_chip() -> None:
    f = flows()
    assert "Meet your agent" in f["t0Dashboard"]
    assert "you can stop it any time" in f["t0Dashboard"]
    assert "One free signature, no transaction" in f["t0Dashboard"]
    a = f["activate"]
    assert a["order"] == ["POST /auth/step-up", "PUT /tier"]
    assert a["stepUpBody"] == {"action": "tier_raise", "params": {"tier": "T2"}}
    assert a["putBody"] == {"schema_version": 1, "tier": "T2"}
    assert a["header"].startswith("f" * 32 + ".0x")
    assert "Action: tier_raise" in a["signedMessage"]
    assert a["tierState"]["tier"] == "T2" and a["serverTier"] == "T2"
    assert "Agent activated: tier T2" in a["text"]
    assert a["landedOn"] == "#/chat"  # activated: the owner lands in Chat
    assert f["activateChip"].endswith("Agent active")
    assert f["afterActivateMe"] == "T2"
    assert "Meet your agent" not in f["activatedDashboard"]
    assert "Lower to read only" in f["activatedDashboard"]


@needs_node
def test_lowering_the_tier_needs_no_signature() -> None:
    low = flows()["lower"]
    assert low["body"] == {"schema_version": 1, "tier": "T0"}
    assert low["stepUpHeader"] is None and low["stepUpCalls"] == 0
    assert low["serverTier"] == "T0"


@needs_node
def test_setup_has_three_steps_and_wallet_prompt_disclosures() -> None:
    text = flows()["onboardActivate"]
    for phrase in (
        "1 · Connected ✓",
        "2 · Add gas",
        "3 · Activate",
        "Activate agent",
        "Wallet prompt: send test ETH (pays gas)",
        "Wallet prompt: one free signature to raise the tier",
        "Get test USDC from Overview",
    ):
        assert phrase in text, phrase


@needs_node
def test_frozen_state_appears_on_stop_and_clears_on_release() -> None:
    fz = flows()["freeze"]
    assert fz["calm"]["cls"] == "masthead" and fz["calm"]["msg"] == ""
    assert fz["calm"]["pill"].endswith("Agent active") and fz["calm"]["btn"] == "Emergency stop"
    assert fz["frozen"]["cls"] == "masthead frozen"
    assert fz["frozen"]["pill"] == "Stopped" and "bad" in fz["frozen"]["pillCls"]
    assert "All orders are blocked" in fz["frozen"]["title"]
    assert fz["frozen"]["btn"] == "Resume (needs signature)"
    assert fz["released"] == {"cls": "masthead", "btn": "Emergency stop", "msg": ""}


@needs_node
def test_spend_bar_maths_and_card() -> None:
    f = flows()
    m = f["spendMath"]
    assert (m["low"]["level"], m["low"]["pct"], m["low"]["spent"], m["low"]["remaining"]) == (
        "ok", 20, "40.00", "160.00",
    )  # fmt: skip
    assert m["warn"]["level"] == "warn" and m["warn"]["pct"] == 85
    assert m["full"]["level"] == "full" and m["full"]["pct"] == 100
    assert m["full"]["remaining"] == "0.00"
    assert m["bad"]["level"] == "unknown" and m["zero"]["level"] == "unknown"
    assert f["spendCard"]["now"] == "20" and f["spendCard"]["width"] == "20%"
    assert "$40.00 of $200.00 used" in f["spendCard"]["text"]
    assert "Rolling 24 hours, no fixed reset time" in f["spendCard"]["text"]
    assert "Orders above $50.00 wait for your approval" in f["spendCard"]["text"]
    assert [g["level"] for g in f["gas"]] == ["low", "ok", "low"]


@needs_node
def test_rate_limited_answer_says_when_to_try_again() -> None:
    r = flows()["retry"]
    assert r["seconds"] == "You can try again in 30 seconds."
    assert r["minutes"] == "You can try again in about 5 minutes."
    assert r["date"] == "You can try again in 45 seconds."
    assert r["none"] == "Wait a little and try again."
    assert r["shown"] == "Too many requests. You can try again in 30 seconds."


@needs_node
def test_fixtures_mode_can_activate_the_agent() -> None:
    assert flows()["fixturesActivate"] == {"before": "T0", "after": "T2"}


def test_wallet_adapter_interface_and_test_stub_exist() -> None:
    adapter = (UI / "wallet" / "adapter.js").read_text(encoding="utf-8")
    for method in (
        "connect",
        "chainId",
        "ensureBaseSepolia",
        "signMessage",
        "sendTransaction",
        "onChange",
    ):
        assert re.search(rf"\b{method}\(", adapter), method
    assert "personal_sign" in (UI / "wallet" / "injected.js").read_text(encoding="utf-8")
    assert "class TestWallet" in (UI / "wallet" / "test.js").read_text(encoding="utf-8")
