"""Webapp Stage 0 UI (F1): served at /ui, offline-safe, design 12.2 routes only, plain language.

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
UI_FILES = [
    "index.html",
    "style.css",
    "main.js",
    "api.js",
    "explain.js",
    "gauges.js",
    "chain.js",
    "wallet/adapter.js",
    "wallet/injected.js",
    "wallet/test.js",
    "views/onboard.js",
    "views/dashboard.js",
    "views/feed.js",
    "views/jobs.js",
    "views/actions.js",
    "views/approvals.js",
    "views/activate.js",
]
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


def test_banner_present_and_not_removable_by_api_data() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    banner = re.search(r'<aside id="banner".*?</aside>', html, re.S)
    assert banner, "banner element missing from index.html"
    text = re.sub(r"<[^>]+>", " ", banner.group(0))
    text = " ".join(text.split()).lower()
    for phrase in (
        "testnet only",
        "custodial",
        "do not send real funds",
        "cannot top up its own eth",
    ):
        assert phrase in text, phrase
    # The banner is static markup: no script touches it, and views are mounted in #view only.
    for name in JS_FILES:
        source = (UI / name).read_text(encoding="utf-8")
        assert "banner" not in source.lower(), name
    assert 'getElementById("view")' in (UI / "main.js").read_text(encoding="utf-8")


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


@needs_node
def test_hostile_blocked_order_is_explained_in_words() -> None:
    feed = harness()["feedAfterHostile"]
    assert harness()["job"] == "denied"
    assert "minting and withdrawing are owner actions" in feed
    assert "<img src=x onerror=alert(1)>" in feed  # shown as inert text


@needs_node
def test_page_works_from_fixtures() -> None:
    h = harness()
    assert "Connect a wallet to begin" in h["signedOut"]["text"]
    assert h["signedOut"]["hasConnectButton"]
    assert h["afterConnect"]["me"] is True
    assert "Agent address" in h["afterConnect"]["text"]
    assert "98 USDC" in h["dashboard"]
    assert "gas floor" in h["dashboard"]
    assert "Reference price" in h["dashboard"]
    assert "Today's spend" in h["dashboard"]
    for phrase in ("Blocked", "Waiting for you", "Failed", "Executed"):
        assert phrase in h["feed"], phrase
    assert "approval" in h["feed"]
    assert h["txLinks"] and all(
        re.fullmatch(r"https://sepolia\.basescan\.org/tx/0x[0-9a-f]{64}", u) for u in h["txLinks"]
    )


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
    assert "Agent address: 0x" + "22" * 20 in f["text"]


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
    assert m["sleeps"] == [2000, 3000]  # poll every 2 s, backing off toward 5 s
    assert "Executed" in m["text"] and "mint 1000 USDC" in m["text"]
    assert m["links"] == [f"https://sepolia.basescan.org/tx/{TX}"]


@needs_node
def test_poll_interval_starts_at_2s_and_caps_at_5s() -> None:
    assert flows()["polling"] == [2000, 3000, 4000, 5000, 5000, 5000]


@needs_node
def test_run_shows_planner_label_and_hostile_run_shows_the_blocked_decision() -> None:
    f = flows()
    assert f["run"]["body"] == {"planner": "auto"}
    assert "fallback" in f["run"]["text"]
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
    assert "Activate your agent" in f["t0Dashboard"]
    assert "you can stop it at any time" in f["t0Dashboard"]
    a = f["activate"]
    assert a["order"] == ["POST /auth/step-up", "PUT /tier"]
    assert a["stepUpBody"] == {"action": "tier_raise", "params": {"tier": "T2"}}
    assert a["putBody"] == {"schema_version": 1, "tier": "T2"}
    assert a["header"].startswith("f" * 32 + ".0x")
    assert "Action: tier_raise" in a["signedMessage"]
    assert a["tierState"]["tier"] == "T2" and a["serverTier"] == "T2"
    assert "Agent activated: tier T2" in a["text"]
    assert f["activateChip"] == "Tier T2"
    assert f["afterActivateMe"] == "T2"
    assert "Activate your agent" not in f["activatedDashboard"]


@needs_node
def test_lowering_the_tier_needs_no_signature() -> None:
    low = flows()["lower"]
    assert low["body"] == {"schema_version": 1, "tier": "T0"}
    assert low["stepUpHeader"] is None and low["stepUpCalls"] == 0
    assert low["serverTier"] == "T0"


@needs_node
def test_stepper_has_activate_step_and_prompt_disclosures() -> None:
    text = flows()["onboardActivate"]
    assert "Activate agent" in text
    for phrase in (
        "Wallet prompt: connect and sign a free login message",
        "Wallet prompt: send test ETH (pays gas)",
        "No wallet prompt: the server mints test USDC",
        "Wallet prompt: one free signature to confirm the preset",
        "Wallet prompt: one free signature to raise the tier",
    ):
        assert phrase in text, phrase


@needs_node
def test_frozen_state_appears_on_stop_and_clears_on_release() -> None:
    fz = flows()["freeze"]
    assert fz["calm"]["cls"] == "masthead" and fz["calm"]["msg"] == ""
    assert fz["frozen"]["cls"] == "masthead frozen"
    assert "FROZEN" in fz["frozen"]["chip"]
    assert "EMERGENCY STOP ACTIVE" in fz["frozen"]["msg"]
    assert "All orders blocked" in fz["frozen"]["msg"]
    assert fz["frozen"]["btn"].startswith("Release stop")
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
    assert "40.00 / 200.00 USD spent in the last 24 hours" in f["spendCard"]["text"]
    assert "Rolling 24-hour window" in f["spendCard"]["text"]
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
        ("get", "/decisions"),
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
