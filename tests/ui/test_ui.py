"""Webapp UI (F1, rebuilt for ticket 119 to docs/design/webapp/ui-contract.md): served at /ui,
offline-safe, design 12.2 routes only, plain language, no warning banner.

Static scans run everywhere; the node harness (tests/ui/harness.mjs) renders the real modules
against a fake DOM and the fixture files and is skipped when node is missing.
"""

from __future__ import annotations

import copy
import functools
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
UI_FILES = sorted(
    p.relative_to(UI).as_posix()
    for p in UI.rglob("*")
    if p.is_file() and p.suffix in {".html", ".css", ".js"}
)
JS_FILES = [f for f in UI_FILES if f.endswith(".js")]
FIXTURES = sorted(p.name for p in (UI / "fixtures").glob("*.json"))

# Design section 12.2 route list, plus the changed existing routes of the same section.
DESIGN_ROUTES = {
    "/auth/nonce",
    "/auth/verify",
    "/auth/logout",
    "/auth/step-up",
    "/me",
    "/agent",
    "/topups",
    "/topups/{tx_hash}",
    "/agent/mint",
    "/agent/withdraw",
    "/presets",
    "/policy",
    "/runs",
    "/chat",
    "/jobs/{job_id}",
    "/jobs",
    "/audit/verify",
    "/portfolio",
    "/tier",
    "/ips",
    "/decisions",
    "/emergency-stop",
    "/emergency-stop/release",
    "/orders/{order_id}/approve",
    "/orders/{order_id}/reject",
    # Webapp v2 (docs/design/webapp/v2-contract.md C1 to C5)
    "/chat/threads",
    "/chat/suggestions",
    "/limits",
    "/jobs/{job_id}/cancel",
    # Chat v3 (spec PR C: SSE stream)
    "/chat/stream/{job_id}",
}
# Links that open in a new tab or are handed to the wallet; the page never fetches them.
ALLOWED_URLS = {
    "https://sepolia.basescan.org/tx/",
    "https://sepolia.base.org",
    "https://sepolia.basescan.org",
}
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


@pytest.mark.parametrize("name", UI_FILES)
def test_static_files_are_served(client: TestClient, name: str) -> None:
    res = client.get(f"/ui/{name}")
    assert res.status_code == 200
    assert res.content == (UI / name).read_bytes()


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_files_are_served_and_valid_json(client: TestClient, name: str) -> None:
    res = client.get(f"/ui/fixtures/{name}")
    assert res.status_code == 200
    json.loads(res.text)


def test_ui_root_serves_index(client: TestClient) -> None:
    res = client.get("/ui/")
    assert res.status_code == 200
    assert 'id="view"' in res.text
    assert 'src="main.js"' in res.text


# ---- design 14.4 claims -----------------------------------------------------------------


def test_no_banner_element_and_testnet_custody_are_chip_footer_and_setup_text() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    css = (UI / "style.css").read_text(encoding="utf-8")
    # The full-width notice bar is gone: no element, no id, no class, no style rule.
    assert not re.search(r"""id=["']banner|class=["'][^"']*\bbanner\b|<aside""", html)
    assert ".banner" not in css and "#banner" not in css
    # One small chip in the header ...
    header = html[html.index("<header") : html.index("</header>")]
    assert 'id="net-chip"' in header and "Base Sepolia testnet" in header
    # ... and one grey line in the footer.
    foot = re.search(r"<footer[^>]*>(.*?)</footer>", html, re.S)
    assert foot, "footer missing"
    text = " ".join(re.sub(r"<[^>]+>", " ", foot.group(1)).split())
    assert text == "Testnet only. The server holds this agent's key. Not financial advice."
    assert html.count("<footer") == 1
    # The header never mentions custody or funds: that lives in the footer and in Setup.
    assert "custod" not in header.lower()
    # Views are mounted in #view only; no script writes to the shell markup of the notice.
    assert 'getElementById("view")' in (UI / "main.js").read_text(encoding="utf-8")
    for name in JS_FILES:
        assert "banner" not in (UI / name).read_text(encoding="utf-8").lower(), name


def test_setup_text_states_testnet_and_custody() -> None:
    source = (UI / "views" / "setup.js").read_text(encoding="utf-8")
    assert (
        "This is a testnet demo: the server holds the agent's key and no real funds are involved."
        in source
    )


def test_header_has_the_four_tabs_chips_pill_and_stop_in_contract_order() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    header = html[html.index("<header") : html.index("</header>")]
    nav = html[html.index("<nav") : html.index("</nav>")]
    hrefs = re.findall(r'href="(#/[a-z]+)"', nav)
    assert hrefs == ["#/chat", "#/overview", "#/activity", "#/rules"]
    labels = re.findall(r">([A-Za-z]+)</a>", nav)
    assert labels == ["Chat", "Overview", "Activity", "Rules"]
    order = [
        header.index(i)
        for i in (
            'id="tabs"',
            'id="net-chip"',
            'id="addr-chip"',
            'id="status-pill"',
            'id="btn-stop"',
        )
    ]
    assert order == sorted(order)
    assert html.index('id="btn-stop"') < html.index('id="view"')  # outside #view, on every route


def test_layout_guards_for_375px_are_in_the_stylesheet() -> None:
    css = (UI / "style.css").read_text(encoding="utf-8")
    assert "max-width: 900px" in css and "max-width: 520px" in css  # stacks under the chat
    assert 'name="viewport"' in (UI / "index.html").read_text(encoding="utf-8")
    assert "overflow-x: auto" in css  # only the tab strip scrolls
    assert css.count("min-width: 0") >= 5 and "overflow-wrap: anywhere" in css
    assert "overflow-x: scroll" not in css and "width: 100vw" not in css
    assert "max-width: 1280px" in css and "--gutter: 32px" in css
    assert "body.screen-chat" in css and "max-width: 1600px" in css and "100dvh" in css
    phone = _block_after(css, "@media (max-width: 900px)")
    assert re.search(r"\.tabs \{[^}]*position: fixed", phone, re.S)
    assert "min-height: 44px" in phone


def _block_after(css: str, query: str) -> str:
    start = css.index(query)
    end = css.find("@media", start + len(query))
    return css[start : end if end != -1 else len(css)]


def test_wallet_strip_is_built_by_side_js_and_hidden_on_desktop() -> None:
    side = (UI / "views" / "side.js").read_text(encoding="utf-8")
    css = (UI / "style.css").read_text(encoding="utf-8")
    assert "wallet-strip" in side and "aria-expanded" in side
    assert ".wallet-strip" in css and "display: none" in css


def test_chat_screen_is_a_single_scroll_region_and_declares_color_scheme() -> None:
    css = (UI / "style.css").read_text(encoding="utf-8")
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "color-scheme: light dark" in css
    assert 'name="color-scheme" content="light dark"' in html
    rule = re.search(r"body\.screen-chat \{([^}]*)\}", css)
    assert rule is not None and "overflow: hidden" in rule.group(1)
    assert "text-transform: uppercase" not in css
    assert "font-size: 11px" not in css


@pytest.mark.parametrize("name", UI_FILES)
def test_no_local_storage_and_no_external_url(name: str) -> None:
    text = (UI / name).read_text(encoding="utf-8")
    for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert banned not in text
    assert not re.search(r"@import|@font-face|//cdn\.", text)
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in text
    for url in re.findall(r"https?://[^\s\"'`)]+", text):
        assert url in ALLOWED_URLS, url


def test_fixtures_hold_no_secrets_or_external_assets() -> None:
    for name in FIXTURES:
        text = (UI / "fixtures" / name).read_text(encoding="utf-8")
        assert "PRIVATE" not in text.upper()
        assert not re.search(r"https?://", text)


def test_api_paths_are_in_design_route_list() -> None:
    js = (UI / "api.js").read_text(encoding="utf-8")
    block = re.search(r"const ROUTES = \{(.*?)\};", js, re.S)
    assert block, "ROUTES block not found in ui/api.js"
    paths = set(re.findall(r'"(/[^"]*)"', block.group(1)))
    assert paths
    assert paths <= DESIGN_ROUTES, paths - DESIGN_ROUTES
    # No path literal anywhere else in the module: every call goes through ROUTES.
    rest = js.replace(block.group(0), "")
    assert not re.findall(r'"/[a-z][^"]*"', rest)


def test_js_read_endpoints_respond(client: TestClient) -> None:
    # Existing routes the UI still reads answer in single mode (the old console check).
    for path in ("/tier", "/decisions?limit=50", "/portfolio"):
        assert client.get(path).status_code == 200, path


def test_stop_visible_and_session_only() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    header = html[html.index("<header") : html.index("</header>")]
    assert 'id="btn-stop"' in header  # in the header, outside #view: on every route
    assert html.index('id="btn-stop"') < html.index('id="view"')
    main = (UI / "main.js").read_text(encoding="utf-8")
    assert "await api.stop();" in main  # engaging needs no signature
    assert "api.release(await ctx.stepUp(" in main  # only the release path asks for one
    assert 'stopBtn.textContent = frozen ? "Resume (needs signature)"' in (
        UI / "gauges.js"
    ).read_text(encoding="utf-8")


@needs_node
def test_reference_and_fallback_labels_rendered() -> None:
    h = harness()
    assert "Reference price" in h["dashboard"]
    assert "Latest run: scripted planner" in h["feedAfterHostile"]
    assert "Latest run:" not in h["feedBeforeAnyRun"]
    labels = h["plannerLabels"]
    assert "scripted" in labels["scripted"] and "fallback" in labels["fallback"]
    assert "OpenAI" in labels["openai"] and labels["none"] is None


def test_existing_routes_used_by_ui_exist_in_openapi() -> None:
    spec = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    for path in ("/decisions", "/portfolio", "/tier", "/ips", "/emergency-stop"):
        assert path in spec["paths"], path


def test_every_existing_rule_code_has_a_plain_language_sentence() -> None:
    codes = [c["code"] for c in json.loads((ROOT / "contracts" / "rule-codes.json").read_text())]
    assert len(codes) >= 25
    source = (UI / "explain.js").read_text(encoding="utf-8")
    for code in codes + ["ACCESS.OWNER_ONLY_FUNCTION", "TIER.GLOBAL_STOP", "TIER.POLICY_CHANGED"]:
        assert f'"{code}"' in source, code


@needs_node
def test_explain_table_is_complete_and_non_empty() -> None:
    h = harness()
    codes = {c["code"] for c in json.loads((ROOT / "contracts" / "rule-codes.json").read_text())}
    assert codes <= set(h["explainKeys"])
    assert h["explainSentencesNonEmpty"] is True
    assert h["formatted"] == {
        "amount": "98",
        "wei": "0.02",
        "parse": "10000000000000000",
        "parseBad": None,
    }


@needs_node
def test_hostile_api_string_does_not_become_html() -> None:
    h = harness()["hostile"]
    assert h["innerHTMLUsed"] is False
    assert h["injectedElements"] == 0
    assert h["shownAsText"] is True
    assert h["rulesShownAsText"] is True and h["setupShownAsText"] is True


@needs_node
def test_hostile_blocked_order_is_explained_in_words() -> None:
    feed = harness()["feedAfterHostile"]
    assert harness()["job"] == "denied"
    assert "minting and withdrawing are owner actions" in feed
    assert "<img src=x onerror=alert(1)>" in feed  # shown as inert text


@needs_node
def test_page_works_from_fixtures() -> None:
    h = harness()
    assert "Connect your wallet" in h["signedOut"]["text"]
    assert h["signedOut"]["hasConnectButton"]
    assert h["afterConnect"]["me"] is True
    assert "0x9a8b7c6d5e4f30211203f4e5d6c7b8a998877665" in h["afterConnect"]["text"]
    assert "USD Coin" in h["dashboard"] and "98" in h["dashboard"]
    assert "ETH for fees" in h["dashboard"] and "Reference price" in h["dashboard"]
    assert "Today's limit" in h["side"] and "$2.00 of $300.00 used" in h["side"]
    assert "chain verified" in h["side"] and "enough" in h["side"]
    for phrase in ("Per order", "Per day", "Ask me first above", "Where money may go"):
        assert phrase in h["rules"], phrase
    for phrase in ("Blocked", "Waiting for you", "Failed", "Executed"):
        assert phrase in h["feed"], phrase
    assert "approval" in h["feed"]
    assert h["txLinks"] and all(
        re.fullmatch(r"https://sepolia\.basescan\.org/tx/0x[0-9a-f]{64}", u) for u in h["txLinks"]
    )


@needs_node
def test_step_card_kinds_done_blocked_pending_skipped_and_failed() -> None:
    h = harness()
    assert h["stepKindList"] == ["done", "blocked", "pending", "skipped", "fail"]
    kinds = h["stepKinds"]
    for kind in h["stepKindList"]:
        assert kinds[kind]["cls"] == f"step-card {kind}", kind
    # Plain words first, the rule code is a small secondary chip, and the state is a word too.
    assert "the funds would go to an address your policy does not list" in kinds["blocked"]["text"]
    assert "ACCESS.RECIPIENT_NOT_ALLOWED" in kinds["blocked"]["text"]
    assert "Waiting for you" in kinds["pending"]["text"]
    assert "Skipped" in kinds["skipped"]["text"] and "Executed" in kinds["done"]["text"]
    assert "Failed" in kinds["fail"]["text"]


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


# ---- F1b: real-mode flows against a fake fetch and a TestWallet -----------------------------

TX = "0x" + "cd" * 32


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


def test_every_route_the_js_calls_exists_in_openapi() -> None:
    spec = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    js = (UI / "api.js").read_text(encoding="utf-8")
    block = re.search(r"const ROUTES = \{(.*?)\};", js, re.S)
    assert block
    paths = set(re.findall(r'"(/[^"]*)"', block.group(1)))
    assert paths <= set(spec["paths"]), paths - set(spec["paths"])
    methods = {
        ("post", "/auth/nonce"), ("post", "/auth/verify"), ("post", "/auth/step-up"),
        ("get", "/me"), ("get", "/agent"), ("post", "/topups"), ("get", "/topups/{tx_hash}"),
        ("post", "/agent/mint"), ("post", "/agent/withdraw"), ("post", "/runs"),
        ("get", "/jobs"), ("get", "/jobs/{job_id}"), ("get", "/audit/verify"),
        ("post", "/emergency-stop"), ("post", "/emergency-stop/release"),
        ("post", "/orders/{order_id}/approve"), ("post", "/orders/{order_id}/reject"),
        ("get", "/presets"), ("put", "/tier"), ("get", "/portfolio"), ("get", "/ips"),
        ("get", "/decisions"), ("get", "/chat"), ("post", "/chat"),
    }  # fmt: skip
    for method, path in methods:
        assert method in spec["paths"][path], (method, path)


def test_no_token_is_stored_or_exposed_by_the_page() -> None:
    for name in UI_FILES:
        text = (UI / name).read_text(encoding="utf-8")
        assert "Authorization" not in text and "Bearer" not in text, name
        assert "window.token" not in text and "globalThis.token" not in text, name


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


def test_design_tokens_come_from_design_md() -> None:
    css = (UI / "style.css").read_text(encoding="utf-8")
    for token, value in (
        ("--primary", "#ffd23f"),
        ("--primary-pressed", "#e9b800"),
        ("--on-accent", "#161513"),
        ("--ink-deep", "#211e1c"),
        ("--focus", "#227e9e"),
    ):
        assert re.search(rf"{re.escape(token)}:\s*{value}", css), token
    assert "prefers-color-scheme: dark" in css
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css
    # The full DESIGN.md palette and the radii of the re-skin (ticket 127).
    for token, value in (
        ("--canvas", "#ffffff"),
        ("--canvas-soft", "#fbf9f8"),
        ("--canvas-tint", "#f5f4f2"),
        ("--canvas-warm", "#f1efed"),
        ("--ink", "#161513"),
        ("--hairline", "#e4e1dd"),
        ("--divider", "#e9e7e4"),
        ("--field-border", "#d4cfca"),
        ("--accent-gold-soft", "#fff1b8"),
        ("--link", "#006b8f"),
        ("--error", "#d63b25"),
        ("--brand-mark", "#8a6700"),
        ("--band-teal", "#d6e5e5"),
        ("--band-forest", "#33553c"),
        ("--r-ctl", "4px"),
        ("--r-card", "8px"),
        ("--r-stat", "15px"),
    ):
        assert re.search(rf"{re.escape(token)}:\s*{value}", css), token


# ---- audit chain check (kept from the owner console) ------------------------------------


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


# ---- chat tab (ticket 115) ----------------------------------------------------------------


def test_chat_is_the_first_tab_and_a_view() -> None:
    main = (UI / "main.js").read_text(encoding="utf-8")
    assert "const VIEWS = { chat," in main and "export function routeFor" in main
    assert 'asked === "setup"' in main and ': "chat";' in main  # Chat is the default once active
    for view in ("chat", "overview", "activity", "rules", "setup"):
        assert (UI / "views" / f"{view}.js").is_file(), view
    for gone in ("dashboard", "feed", "onboard"):
        assert not (UI / "views" / f"{gone}.js").exists(), gone


def test_rules_tab_reads_the_limits_route() -> None:
    assert '"/limits"' in (UI / "limits.js").read_text(encoding="utf-8")
    assert "getLimits(" in (UI / "views" / "rules.js").read_text(encoding="utf-8")
    spec = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    assert "get" in spec["paths"]["/limits"]


@needs_node
def test_chat_send_with_enter_shows_progress_then_step_cards() -> None:
    f = chat_flows()
    assert "No messages yet." in f["emptyText"]
    assert f["counterTyped"] == "27 / 500"
    assert f["postsAfterShiftEnter"] == 0  # Shift+Enter is a newline
    s = f["send"]
    assert s["body"] == {"message": "Put 10 USDC to work in Aave"} and s["csrf"] == "1"
    # The progress card is in the log before the POST resolves; an unknown phase is "Working".
    assert s["pre"] == [["Sending your message"]]
    assert s["live"] == [
        ["Message received"],
        ["Message received", "Waiting to start"],
        ["Message received", "Waiting to start", "Working on your request"],
    ]
    assert s["composerCleared"] is True
    assert s["sleeps"] == [750, 750]  # the fast first polls (see pollDelay)
    assert s["bubbles"] == ["bubble user", "bubble agent"]
    assert s["cards"] == 2 and s["saysChips"] == 1
    assert "Approved 10 USDC for the Aave pool." in s["text"] and "Executed" in s["text"]
    assert "Transactions:" not in s["text"]  # hashes appear only as BaseScan links
    assert s["links"] == [f"https://sepolia.basescan.org/tx/{TX_CHAT}"] * 2
    assert s["sendDisabled"] is False


@needs_node
def test_chat_blocked_step_is_explained_and_links_to_activate() -> None:
    b = chat_flows()["blocked"]
    assert "Blocked" in b["text"] and "Skipped" in b["text"]
    assert "read-only mode (tier T0)" in b["text"]
    assert b["codes"] == ["TIER.READ_ONLY"]
    assert b["cards"] == ["step-card blocked", "step-card skipped"]
    assert b["activateLinks"] == 2 and set(b["links"]) == {"#/setup"}  # no tx link


@needs_node
def test_chat_frozen_state_disables_the_composer() -> None:
    f = chat_flows()["frozen"]
    assert "Your agent is frozen" in f["text"] and "Resume in the top bar" in f["text"]
    assert f["sendDisabled"] is True and f["inputDisabled"] is True and f["posts"] == 0


@needs_node
def test_chat_rate_limit_and_conflict_are_plain_words_and_keep_the_message() -> None:
    f = chat_flows()
    r = f["rateLimited"]
    assert r["status"] == "Too many requests. You can try again in about 2 minutes."
    assert r["kept"] == "one more thing" and r["bubbles"] == 0 and r["sendEnabled"] is True
    c = f["conflict"]
    assert "still in progress" in c["status"] and c["kept"] == "second"
    assert "Earlier request finished." in c["text"]  # the running job was followed


@needs_node
def test_chat_history_prompt_chips_and_signed_out() -> None:
    f = chat_flows()
    assert f["history"]["bubbles"] == ["bubble user", "bubble agent"]
    assert f["history"]["getCalls"] == ["/chat?limit=50"]
    assert (
        "Agent says" in f["history"]["text"] and "Wallet: 1000 USDC idle." in f["history"]["text"]
    )
    assert f["chipFilled"] == "Put 10 USDC to work in Aave"
    assert f["prompts"] == [
        "What is my balance?",
        "Put 10 USDC to work in Aave",
        "Pull 5 USDC back out",
    ]
    assert f["postsAfterChip"] == 0  # a chip fills the box, it never sends
    assert f["maxLength"] == 500
    assert "Connect a wallet" in f["signedOut"]["text"] and f["signedOut"]["chatCalls"] == 0


@needs_node
def test_chat_hostile_strings_stay_text_and_fixtures_flow_works() -> None:
    h = chat_flows()["hostile"]
    assert h == {"innerHTMLUsed": False, "injected": 0, "anchors": 0, "shown": True}
    fx = chat_flows()["fixtures"]
    assert "read-only mode (tier T0)" in fx["text"] and fx["cards"] == 2


TX_CHAT = "0x" + "cd" * 32


# ---- webapp v2: chat wired to phases, cancel, threads, limits and suggestions (F1, F2) ------


@needs_node
def test_chat_progress_card_and_stop_button() -> None:
    p = chat_flows()["phases"]
    assert p["seen"][-1]["rows"] == [
        "Message received",
        "Waiting to start",
        "Working on your request",
        "Sending to Base Sepolia",
        "Confirming on Base Sepolia",
    ]
    # Send is Stop while the job is queued or running, and the card has its own Stop button
    assert all(x["stop"] and x["cardStop"] == 1 for x in p["seen"])
    assert p["cancelPosts"] == 1 and p["csrf"] == "1"
    assert p["sendLabelAfter"] == ["Send"]  # back to Send once the job ended
    assert p["skipped"] == 1 and p["skipText"] is True


@needs_node
def test_chat_stop_too_late_says_the_order_is_sent() -> None:
    assert "Too late to stop: this order is already sent." in chat_flows()["tooLate"]


@needs_node
def test_chat_limit_line_and_quick_fixes_never_send_on_their_own() -> None:
    q = chat_flows()["quick"]
    assert q["limitLine"] == [
        "This order $5000.00, per-order limit $50.00, today $10.00 of $1000.00"
    ]
    assert q["chips"] == ["Try 50 USDCWhy is this blocked?"]
    assert q["limitsCalls"] == 1  # read once per render, not once per card
    assert q["composer"] == "Put 50 USDC to work in Aave" and q["postsAfterTry"] == 0
    assert q["asked"] == {"message": "Why is this blocked?"}
    assert q["composerAfter"] == q["composer"]  # the question did not touch the composer
    assert q["unknownPhase"][-1] == "Working on your request"


@needs_node
def test_chat_history_rail_threads_and_suggestions() -> None:
    r = chat_flows()["rail"]
    assert r["groups"] == ["Today", "Earlier"]
    assert r["rows"][0].startswith("Put 10 USDC to workRunning")
    assert r["rows"][1].startswith("Withdraw 5 USDCBlocked")
    assert r["chips"] == ["What did you do today?", "Supply 10 USDC to Aave"]
    assert r["loaded"] == 1 and "Blocked thread reply." in r["text"][1]
    assert r["active"] == [r["rows"][1]]
    assert r["newPosts"] == 1 and r["sendBody"] == {"message": "hello", "thread_id": "tnew"}
    assert chat_flows()["fallback"] == [
        "What is my balance?",
        "Put 10 USDC to work in Aave",
        "Pull 5 USDC back out",
    ]


@needs_node
def test_chat_failed_after_sent_says_it_may_still_land() -> None:
    u = chat_flows()["uncertain"]
    assert u["text"] is True and u["link"] == ["#/activity"]


@needs_node
def test_chat_thread_survival_across_remounts_and_reload() -> None:
    s = harness()["threadSurvival"]
    assert s["createdThread"] == "t6"
    assert s["afterSendThread"] == "t6"
    assert s["hashAfterSend"] == "#/chat/t6"
    assert s["sameRouteThread"] == "t6"
    assert s["routeBackThread"] == "t6"
    assert s["topbarThread"] == "t6"
    assert s["changedThread"] == "t6"
    assert s["reloadThread"] == "t6"


@needs_node
def test_chat_thread_survival_in_chat_flows() -> None:
    s = chat_flows()["threadSurvival"]
    assert s["threadAfterNew"] == "tnew"
    assert s["hashAfterNew"] == "#/chat/tnew"
    assert s["titleAfterSend"] == "Starting new"
    assert any("Starting new" in r for r in s["activeRailAfterSend"])
    assert s["threadSameRoute"] == "tnew"
    assert s["hashSameRoute"] == "#/chat/tnew"
    assert s["threadRouteBack"] == "tnew"
    assert s["threadTopbar"] == "tnew"
    assert s["threadChanged"] == "tnew"
    assert s["threadReload"] == "tnew"
    assert s["reloadTitle"] == "Starting new"
    assert any("Starting new" in r for r in s["reloadActiveRail"])


@needs_node
def test_chat_deep_link_and_404_fallback() -> None:
    d = chat_flows()["deepLinkAnd404"]
    assert d["deepLinkSelected"] == "t2"
    assert d["deepLinkTitle"] == "Thread 2"
    assert "YouHello t2" in d["deepLinkBubbles"]
    assert d["unknownFallbackSelected"] == "t1"
    assert d["unknownFallbackTitle"] == "Thread 1"
    assert d["unknownFallbackHash"] == "#/chat/t1"
    assert d["unknownStatus"] == ""
    assert d["notFoundFallbackSelected"] == "t1"
    assert d["notFoundStatus"] == ""
    assert d["notFoundHash"] == "#/chat/t1"


@needs_node
def test_chat_job_isolation_between_threads() -> None:
    iso = chat_flows()["jobIsolation"]
    assert iso["t1RunningLive"] is True
    assert iso["t1RunningStopBtn"] is True
    assert iso["t2DuringRunLive"] is False
    assert iso["t2DuringRunSendBtn"] is True
    assert "Earlier in t2" in "".join(iso["t2TextAfterJobDone"])
    assert "Earlier in t1" in "".join(iso["t1TextFinal"])


def test_chat_ui_has_no_new_storage_or_external_urls() -> None:
    for name in ("views/chat.js", "views/cards.js", "views/side.js"):
        text = (UI / name).read_text(encoding="utf-8")
        assert "localStorage" not in text and "sessionStorage" not in text, name
        assert not re.findall(r"https?://(?!sepolia\.basescan\.org)", text), name
