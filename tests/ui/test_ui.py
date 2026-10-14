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
    "chain.js",
    "wallet/adapter.js",
    "wallet/injected.js",
    "wallet/test.js",
    "views/onboard.js",
    "views/dashboard.js",
    "views/feed.js",
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
    assert labels["openai"] is None and labels["none"] is None


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
