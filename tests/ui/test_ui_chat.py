"""Chat tab, threads, suggestions, and read-cache tests."""

from __future__ import annotations

import re

import yaml

from tests.ui.conftest import ROOT, UI, cache_flows, chat_flows, harness, needs_node


def test_chat_is_the_first_tab_and_a_view() -> None:
    main = (UI / "main.js").read_text(encoding="utf-8")
    assert "const VIEWS = { chat," in main and "export function routeFor" in main
    assert 'asked === "setup"' in main and ': "chat";' in main  # Chat is the default once active
    assert (UI / "views" / "chat" / "index.js").is_file()
    for view in ("overview", "activity", "rules", "setup"):
        assert (UI / "views" / f"{view}.js").is_file(), view
    for gone in ("dashboard", "feed", "onboard"):
        assert not (UI / "views" / f"{gone}.js").exists(), gone


def test_rules_tab_reads_the_limits_route() -> None:
    assert '"/limits"' in (UI / "lib" / "limits.js").read_text(encoding="utf-8")
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
    assert s["cards"] == 2 and s["saysChips"] == 0
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
        "Agent says" not in f["history"]["text"]
        and "Wallet: 1000 USDC idle." in f["history"]["text"]
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
    assert d["unknownFallbackSelected"] == "unknown_thread_id"
    assert d["unknownFallbackTitle"] == "New chat"
    assert d["unknownFallbackHash"] == "#/chat/unknown_thread_id"
    assert d["unknownStatus"] == ""
    assert d["notFoundFallbackSelected"] is None
    assert d["notFoundStatus"] == ""
    assert d["notFoundHash"] == "#/chat"


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
    for name in (
        "views/chat/index.js",
        "views/chat/bubble.js",
        "views/chat/stream.js",
        "views/cards.js",
        "views/side.js",
    ):
        text = (UI / name).read_text(encoding="utf-8")
        assert "localStorage" not in text and "sessionStorage" not in text, name
        assert not re.findall(r"https?://(?!sepolia\.basescan\.org)", text), name


@needs_node
def test_chat_new_thread_n_clicks_idempotent_and_focuses() -> None:
    n = chat_flows()["nClicksNewChat"]
    assert n["postCalls"] == 1
    assert n["postCallsAfterEmptyClick"] == 1
    assert n["focused"] is True
    assert n["threadCount"] == 2
    assert n["threadId"] == "tnew"


@needs_node
def test_chat_stale_history_ignored() -> None:
    s = chat_flows()["staleHistoryIgnored"]
    assert s["t1HistoryCalled"] == 1
    assert s["stillShowsT2"] is True
    assert s["doesNotShowT1"] is True
    assert s["currentThreadId"] == "t2"


@needs_node
def test_chat_title_refresh_after_first_message() -> None:
    t = chat_flows()["titleRefreshAfterFirstMessage"]
    assert t["newThreadId"] == "tnew"
    assert t["updatedImmediately"] is True
    assert "What is my balance" in t["headerTitleBeforeJobDone"]
    assert "What is my balance" in t["railTitleBeforeJobDone"]


@needs_node
def test_chat_new_chat_never_jumps_to_default_on_send() -> None:
    r = chat_flows()["newChatDefaultJumpRepro"]
    assert r["threadAfterNew"] == "tnew"
    assert r["hashAfterNew"] == "#/chat/tnew"
    assert r["jumpedToDefault"] is False
    assert r["stayedInNewThread"] is True
    assert r["threadAfterSend"] == "tnew"
    assert r["hashAfterSend"] == "#/chat/tnew"


@needs_node
def test_chat_unified_bubble_and_measurement() -> None:
    m = chat_flows()["measurement"]
    # (a) API requests during send-and-finish: reduced, no duplicate calls
    assert m["sendAndFinishTotal"] <= 10
    assert m["sendAndFinishRequests"]["byRoute"]["POST /chat"] == 1
    assert m["sendAndFinishRequests"]["byRoute"].get("GET /chat/threads", 0) <= 1
    # (b) Time to first agent text is at first token (1.0s, not waiting for done at 3.0s)
    assert m["timeToFirstAgentTextSec"] == 1.0
    # (c) Exactly ONE agent bubble created in turn, reusing the live bubble node
    assert m["bubblesCreatedInTurn"] == 1
    assert m["reusedSameBubble"] is True
    # (d) Thread switch: skeleton seen, single GET /chat request
    assert m["threadSwitchRequests"]["total"] == 1
    assert m["threadSwitchSkeletonSeen"] is True


@needs_node
def test_chat_clarification_and_streaming_features() -> None:
    e = chat_flows()["clarificationStreaming"]
    # Clarification chips: offered when intent is clarify, fill composer,
    # do not auto-send, removed on send
    assert e["clarifyChips"] == ["Aave", "Compound"]
    assert e["valueAfterClick"] == "Aave"
    assert e["postCallsAfterClick"] == 0
    assert e["clarifyChipsAfterSend"] == 0
    # Live text streaming: token text appended with textContent, hostile tags stay text
    assert e["hostileTextSeen"] == "Checking <img onerror=alert(1)>"
    assert e["innerHTMLUsedDuringStream"] is False
    # Reset clears streamed text
    assert e["textAfterReset"] == ""
    assert e["hiddenAfterReset"] is True
    assert e["textAfterSecondToken"] == "Verified cleanly."
    # Unified bubble: no Agent says chip, no AGENT_SAYS prefix
    assert e["saysChipCount"] == 0
    assert e["lastAgentHasSays"] is False
    # Thread switch error and retry
    assert "Couldn't load this chat." in e["errorText"]
    assert "Retry" in e["errorText"]
    assert any("Err msg" in b for b in e["recoveredBubbles"])


@needs_node
def test_ui_read_cache_measurements() -> None:
    r = cache_flows()
    # (a) Cold load of Overview + side panel: single flight merges /portfolio and /agent
    assert r["coldLoad"]["portfolio"] == 1
    assert r["coldLoad"]["agent"] == 1
    assert r["coldLoad"]["total"] == 8
    # (b) Flow Overview -> Activity -> Overview -> Chat -> Overview within 5s
    assert r["fiveSecFlow"]["total"] == 19
    assert r["fiveSecFlow"]["byRoute"]["GET /portfolio"] == 1
    assert r["fiveSecFlow"]["byRoute"]["GET /agent"] == 1
    assert r["fiveSecFlow"]["byRoute"]["GET /limits"] == 1
    assert r["fiveSecFlow"]["byRoute"]["GET /audit/verify"] == 1
    # (c) Time from clicking tab to first painted content:
    # First visit: skeleton immediate, content after fetch
    assert r["latencySimulation"]["v1SkeletonImmediate"] is True
    assert r["latencySimulation"]["v1HasContent"] is True
    # Second visit: paints immediately from peek (no skeleton!), repaints after fetch
    assert r["latencySimulation"]["v2SkeletonImmediate"] is False
    assert r["latencySimulation"]["v2ContentImmediate"] is True
    assert r["latencySimulation"]["v2ContentAfterFetch"] is True


@needs_node
def test_ui_read_cache_semantics() -> None:
    r = cache_flows()
    # 1. Single flight
    assert r["singleFlight"]["portRequests"] == 1
    assert r["singleFlight"]["sameValue"] is True
    assert r["singleFlight"]["isDistinctObject"] is True

    # 2. Memo expiry (8 seconds)
    assert r["memoExpiry"]["reqsUnder8s"] == 1
    assert r["memoExpiry"]["reqsAfter8s"] == 2

    # 3. Write invalidates
    assert r["writeInvalidates"]["beforeWrite"] == 1
    assert r["writeInvalidates"]["afterWrite"] == 2
    assert r["writeInvalidates"]["afterStop"] == 3
    assert r["writeInvalidates"]["afterMint"] == 4

    # 4. Failed read not cached
    assert r["failedReadNotCached"]["caught"] is True
    assert r["failedReadNotCached"]["peekAfterFail"] is True
    assert r["failedReadNotCached"]["recoveredTotal"] == "1000.00"

    # 5. Cache cleared on session change
    assert r["sessionChange"]["peekOwner1HasData"] is True
    assert r["sessionChange"]["peekAfterSignOutIsUndefined"] is True
    assert r["sessionChange"]["peekOwner2BeforeReadIsUndefined"] is True
    assert r["sessionChange"]["owner2Total"] == "5555.00"

    # 6. View paints from peek and then repaints
    assert r["paintPeekThenRepaint"]["hasSkeletonOnSecondVisit"] is False
    assert "$1000.00" in r["paintPeekThenRepaint"]["hero2PeekText"]
    assert "$9999.00" in r["paintPeekThenRepaint"]["hero2FreshText"]

    # 7. Stale response dropped when view unmounted
    assert r["staleResponseDropped"]["completedWithoutCrash"] is True

    # 8. Hostile API strings are not parsed as HTML
    assert r["hostileStrings"]["innerHTMLUsed"] is False
    assert r["hostileStrings"]["overviewHasScriptText"] is True
    assert r["hostileStrings"]["activityHasScriptText"] is True
