"""Static scans and page/stylesheet checks for the webapp UI."""

from __future__ import annotations

import json
import re

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.ui._helpers import ALLOWED_URLS, DESIGN_ROUTES, _block_after
from tests.ui.conftest import ROOT, UI, harness, jobs_flows, needs_node

UI_FILES = sorted(
    p.relative_to(UI).as_posix()
    for p in UI.rglob("*")
    if p.is_file() and p.suffix in {".html", ".css", ".js"}
)
JS_FILES = [f for f in UI_FILES if f.endswith(".js")]
FIXTURES = sorted(p.name for p in (UI / "fixtures").glob("*.json"))


def test_load_records_one_request_replaces_n() -> None:
    res = jobs_flows()
    assert res.get("singleRequestReplacesN") is True
    assert res.get("jobCallsCount") == 0
    assert res.get("jobsCallsCount") == 1
    assert res.get("fallbackWorks") is True
    assert res.get("fallbackJobCallsCount") == 2


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


def test_bare_domain_redirects_to_ui(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/chat"


SCREENS = ["/chat", "/chat/t1", "/overview", "/activity", "/rules", "/setup"]


@pytest.mark.parametrize("path", SCREENS)
def test_clean_screen_paths_serve_the_app(client: TestClient, path: str) -> None:
    res = client.get(path, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    assert res.status_code == 200
    assert res.text == (UI / "index.html").read_text(encoding="utf-8")
    assert 'src="/ui/main.js"' in res.text


def test_api_calls_to_screen_paths_still_reach_the_api(client: TestClient) -> None:
    # GET /chat is also the chat-history API; a JSON request must not get the HTML page.
    res = client.get("/chat", headers={"Accept": "application/json"})
    assert "text/html" not in res.headers.get("content-type", "")


def test_ui_root_serves_index(client: TestClient) -> None:
    res = client.get("/ui/")
    assert res.status_code == 200
    assert 'id="view"' in res.text
    assert 'src="/ui/main.js"' in res.text


def test_no_banner_element_and_testnet_custody_are_chip_footer_and_setup_text() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
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
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
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


def test_chat_screen_is_a_single_scroll_region_and_declares_color_scheme() -> None:
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
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
    js = (UI / "lib" / "api.js").read_text(encoding="utf-8")
    block = re.search(r"const ROUTES = \{(.*?)\};", js, re.S)
    assert block, "ROUTES block not found in ui/lib/api.js"
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
    source = (UI / "lib" / "explain.js").read_text(encoding="utf-8")
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


def test_every_route_the_js_calls_exists_in_openapi() -> None:
    spec = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    js = (UI / "lib" / "api.js").read_text(encoding="utf-8")
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


def test_design_tokens_come_from_design_md() -> None:
    css = (UI / "css" / "style.css").read_text(encoding="utf-8")
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
    # The full DESIGN.md palette and the radii of the re-skin.
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
