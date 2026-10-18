"""Unit tests for the multi-protocol evidence loop runner."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.evidence.webapp_evidence_loop import (
    SCENARIOS,
    Throttle,
    check_verdict,
    compute_counts,
    is_rate_limited,
    main,
    parse_retry_after,
    read_state_file,
    render_report,
    run_loop,
)


class FakeAccount:
    def __init__(self, address: str = "0x9D1Ae26890c683a52e73A504091b090f7689373a") -> None:
        self.address = address


class FakeResponse:
    def __init__(self, data: Any, status_code: int = 200, text: str = "") -> None:
        self._data = data
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def put(self, url: str, **kw: Any) -> FakeResponse:
        self.calls.append(("PUT", url, kw))
        return FakeResponse({"ok": True}, status_code=200)


class FakeClient:
    def __init__(
        self,
        *,
        agent_address: str = "0xc18afDC2dF55772bA3c031Df98305F02a3a36493",
        usdc_balance: int = 80_000_000,
        audit_verify_ok: bool = True,
        job_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        chat_steps_map: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.base = "https://agent-wallet-fake.example.com"
        self.headers = {"X-AW-CSRF": "1"}
        self.account = FakeAccount()
        self.http = FakeHttp()
        self.agent_address = agent_address
        self.usdc_balance = usdc_balance
        self.audit_verify_ok = audit_verify_ok
        self.job_handler = job_handler
        self.chat_steps_map = chat_steps_map or {}
        self.sent_jobs: list[tuple[str, dict[str, Any]]] = []
        self.req_calls: list[tuple[str, str, dict[str, Any]]] = []
        self._chat_items: list[dict[str, Any]] = []
        self._job_counter = 0

    def login(self) -> dict[str, Any]:
        return {"message": "fake challenge", "session": {"token": "fake-session-token"}}

    def req(self, method: str, path: str, **kw: Any) -> FakeResponse:
        self.req_calls.append((method, path, kw))
        if path == "/agent":
            return FakeResponse(
                {
                    "address": self.agent_address,
                    "eth_wei": "100000000000000000",
                    "usdc": {"value": str(self.usdc_balance), "decimals": 6},
                }
            )
        if path == "/presets":
            return FakeResponse({"schema_version": 1, "items": [{"preset_id": "diversified"}]})
        if path.startswith("/chat"):
            return FakeResponse({"items": list(self._chat_items)})
        if path == "/audit/verify":
            return FakeResponse({"ok": self.audit_verify_ok, "records": 48, "first_bad_seq": None})
        return FakeResponse({}, status_code=200)

    def step_up(self, action: str, params: dict[str, Any]) -> dict[str, str]:
        return {"X-Step-Up": "fake_challenge.fake_signature"}

    def job(
        self, path: str, body: dict[str, Any] | None = None, timeout: int = 240
    ) -> dict[str, Any]:
        body = body or {}
        self.sent_jobs.append((path, body))
        if self.job_handler:
            return self.job_handler(path, body)

        self._job_counter += 1
        job_id = f"job{self._job_counter:04d}"
        if path == "/chat":
            msg = body.get("message", "")
            steps = self.chat_steps_map.get(msg, [])
            self._chat_items.append({"job_id": job_id, "role": "agent", "steps": steps})
            return {"job_id": job_id, "status": "succeeded"}
        if path == "/agent/mint":
            self.usdc_balance += int(body.get("amount_usdc", "100")) * 10**6
            return {"job_id": job_id, "status": "succeeded"}
        return {"job_id": job_id, "status": "succeeded"}


def test_throttle_never_exceeds_k_in_3600s() -> None:
    current_time = 1000.0
    sleep_calls: list[float] = []

    def fake_now() -> float:
        return current_time

    def fake_sleep(dur: float) -> None:
        nonlocal current_time
        sleep_calls.append(dur)
        current_time += dur

    k = 5
    throttle = Throttle(per_hour=k, now_fn=fake_now, sleep_fn=fake_sleep)

    sends: list[float] = []
    # Send 18 messages with varying spacing
    for _ in range(18):
        throttle.wait_if_needed()
        throttle.record_send()
        sends.append(current_time)
        current_time += 15.0

    # Assert invariant: for any send t, count of sends in [t - 3600, t] <= k
    for t in sends:
        window_count = sum(1 for s in sends if t - 3600.0 < s <= t)
        assert window_count <= k, f"At t={t}, found {window_count} sends > {k}"

    assert len(sleep_calls) > 0


def test_throttle_validation() -> None:
    try:
        Throttle(per_hour=7)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for per_hour=7")

    try:
        Throttle(per_hour=0)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for per_hour=0")


def test_dry_run_sends_nothing(tmp_path: Path) -> None:
    client = FakeClient()
    out_file = tmp_path / "out.md"
    state_file = tmp_path / "state.jsonl"

    code = main(
        [
            "--url",
            "https://agent-wallet-fake.example.com",
            "--rounds",
            "2",
            "--out",
            str(out_file),
            "--state",
            str(state_file),
        ],
        client=client,
    )
    assert code == 0
    assert len(client.sent_jobs) == 0
    assert len(client.http.calls) == 0
    assert not out_file.exists()


def test_resume_from_state_file(tmp_path: Path) -> None:
    client = FakeClient()
    state_file = tmp_path / "state.jsonl"
    out_file = tmp_path / "out.md"

    # Pre-populate state file with 3 completed scenarios of round 1
    pre_records = [
        {
            "round": 1,
            "scenario": SCENARIOS[0][0],
            "status": "succeeded",
            "error_code": None,
            "expectation": "execute",
            "verdict": "executed",
            "verdict_ok": True,
            "steps": [{"function": "deposit", "outcome": "executed", "tx_hash": "0x" + "1" * 64}],
            "timestamp": "2026-10-18T10:00:00Z",
        },
        {
            "round": 1,
            "scenario": SCENARIOS[1][0],
            "status": "succeeded",
            "error_code": None,
            "expectation": "execute",
            "verdict": "executed",
            "verdict_ok": True,
            "steps": [{"function": "supply", "outcome": "executed", "tx_hash": "0x" + "2" * 64}],
            "timestamp": "2026-10-18T10:05:00Z",
        },
        {
            "round": 1,
            "scenario": SCENARIOS[2][0],
            "status": "succeeded",
            "error_code": None,
            "expectation": "execute",
            "verdict": "executed",
            "verdict_ok": True,
            "steps": [{"function": "lend", "outcome": "executed", "tx_hash": "0x" + "3" * 64}],
            "timestamp": "2026-10-18T10:10:00Z",
        },
    ]
    with state_file.open("w", encoding="utf-8") as f:
        for r in pre_records:
            f.write(json.dumps(r) + "\n")

    sim_time = 2000.0

    def fake_now() -> float:
        return sim_time

    def fake_sleep(dur: float) -> None:
        nonlocal sim_time
        sim_time += dur

    code = main(
        [
            "--url",
            "https://agent-wallet-fake.example.com",
            "--execute",
            "--rounds",
            "1",
            "--out",
            str(out_file),
            "--state",
            str(state_file),
        ],
        client=client,
        now_fn=fake_now,
        sleep_fn=fake_sleep,
    )
    assert code == 0

    # Only remaining 7 scenarios (index 3 to 9) should have been sent to client
    assert len(client.sent_jobs) == len(SCENARIOS) - 3
    all_recs = read_state_file(state_file)
    assert len(all_recs) == len(SCENARIOS)


def test_hostile_executed_exits_nonzero(tmp_path: Path) -> None:
    hostile_tx = "0x" + "f" * 64

    # Hostile scenario returns an executed step (safety violation)
    chat_map: dict[str, list[dict[str, Any]]] = {}
    for name, phrasings, exp in SCENARIOS:
        prompt = phrasings[0]
        if "hostile" in name:
            chat_map[prompt] = [
                {"function": "transfer", "outcome": "executed", "tx_hash": hostile_tx}
            ]
        elif exp == "execute":
            chat_map[prompt] = [
                {"function": "act", "outcome": "executed", "tx_hash": "0x" + "1" * 64}
            ]
        else:
            chat_map[prompt] = []

    client = FakeClient(chat_steps_map=chat_map)
    out_file = tmp_path / "out.md"
    state_file = tmp_path / "state.jsonl"

    sim_time = 1000.0

    def fake_now() -> float:
        return sim_time

    def fake_sleep(dur: float) -> None:
        nonlocal sim_time
        sim_time += dur

    code = main(
        [
            "--url",
            "https://agent-wallet-fake.example.com",
            "--execute",
            "--rounds",
            "1",
            "--out",
            str(out_file),
            "--state",
            str(state_file),
        ],
        client=client,
        now_fn=fake_now,
        sleep_fn=fake_sleep,
    )
    assert code != 0
    report_text = out_file.read_text(encoding="utf-8")
    assert "CRITICAL SAFETY VIOLATION" in report_text or "Unexpected" in report_text


def test_report_holds_no_64_hex_except_fed_tx_hashes(tmp_path: Path) -> None:
    tx1 = "0x" + "a1b2c3d4" * 8
    tx2 = "0x" + "e5f60718" * 8
    allowed_hexes = {tx1.removeprefix("0x").lower(), tx2.removeprefix("0x").lower()}

    chat_map: dict[str, list[dict[str, Any]]] = {}
    for i, (_name, phrasings, exp) in enumerate(SCENARIOS):
        prompt = phrasings[0]
        if i == 0:
            chat_map[prompt] = [{"function": "deposit", "outcome": "executed", "tx_hash": tx1}]
        elif i == 1:
            chat_map[prompt] = [{"function": "supply", "outcome": "executed", "tx_hash": tx2}]
        elif exp == "execute":
            chat_map[prompt] = [{"function": "call", "outcome": "no_action", "tx_hash": None}]
        else:
            chat_map[prompt] = []

    client = FakeClient(chat_steps_map=chat_map)
    out_file = tmp_path / "out.md"
    state_file = tmp_path / "state.jsonl"

    sim_time = 5000.0

    def fake_now() -> float:
        return sim_time

    def fake_sleep(dur: float) -> None:
        nonlocal sim_time
        sim_time += dur

    code = main(
        [
            "--url",
            "https://agent-wallet-fake.example.com",
            "--execute",
            "--rounds",
            "1",
            "--out",
            str(out_file),
            "--state",
            str(state_file),
        ],
        client=client,
        now_fn=fake_now,
        sleep_fn=fake_sleep,
    )
    assert code == 0
    report_text = out_file.read_text(encoding="utf-8")

    # Match all 64-character hex substrings
    hex64_pattern = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
    found_matches = {m.group(0).lower() for m in hex64_pattern.finditer(report_text)}

    # Ensure EVERY 64-hex string in the report is in allowed_hexes
    for h in found_matches:
        assert h in allowed_hexes, f"Unexpected 64-hex string found in report: {h}"
    assert allowed_hexes.issubset(found_matches)


def test_verdicts_pure_logic() -> None:
    # execute
    ok, label = check_verdict("execute", [{"outcome": "executed", "tx_hash": "0x123"}])
    assert ok is True and label == "executed"

    ok, label = check_verdict("execute", [{"outcome": "executed", "tx_hash": None}])
    assert ok is False

    ok, label = check_verdict("execute", [])
    assert ok is False

    # deny_or_noaction
    ok, label = check_verdict("deny_or_noaction", [{"outcome": "executed", "tx_hash": "0x123"}])
    assert ok is False and "hostile" in label

    ok, label = check_verdict("deny_or_noaction", [{"outcome": "denied"}])
    assert ok is True and label == "denied"

    ok, label = check_verdict("deny_or_noaction", [])
    assert ok is True and label == "no_action"

    # any
    ok, label = check_verdict("any", [{"outcome": "executed", "tx_hash": "0x123"}])
    assert ok is True and label == "executed"

    ok, label = check_verdict("any", [{"outcome": "denied"}])
    assert ok is True and label == "denied"

    ok, label = check_verdict("any", [])
    assert ok is True and label == "no_action"


def test_rate_limited_retry(tmp_path: Path) -> None:
    attempt = 0

    def job_handler(path: str, body: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempt
        if path == "/chat":
            attempt += 1
            if attempt == 1:
                return {
                    "http": 429,
                    "problem": {
                        "status": 429,
                        "slug": "rate-limited",
                        "retry_after": 12.0,
                    },
                }
            return {"job_id": "job-retry-ok", "status": "succeeded"}
        return {"job_id": "job-ok", "status": "succeeded"}

    client = FakeClient(job_handler=job_handler)
    slept_values: list[float] = []
    sim_time = 100.0

    def fake_now() -> float:
        return sim_time

    def fake_sleep(dur: float) -> None:
        nonlocal sim_time
        slept_values.append(dur)
        sim_time += dur

    args = argparse.Namespace(
        url="https://agent-wallet-fake.example.com",
        execute=True,
        rounds=1,
        max_hours=1.0,
        per_hour=5,
        out=str(tmp_path / "out.md"),
        state=str(tmp_path / "state.jsonl"),
    )
    # Run only 1 scenario by temporarily shortening SCENARIOS
    orig_scenarios = list(SCENARIOS)
    try:
        SCENARIOS.clear()
        SCENARIOS.append(orig_scenarios[0])
        code = run_loop(client, args, now_fn=fake_now, sleep_fn=fake_sleep)
    finally:
        SCENARIOS.clear()
        SCENARIOS.extend(orig_scenarios)

    assert code == 0
    assert 12.0 in slept_values
    assert attempt == 2


def test_balance_care_mint(tmp_path: Path) -> None:
    # Start with low balance: 10 USDC (10_000_000 raw)
    client = FakeClient(usdc_balance=10_000_000)
    out_file = tmp_path / "out.md"
    state_file = tmp_path / "state.jsonl"

    sim_time = 0.0

    def fake_now() -> float:
        return sim_time

    def fake_sleep(dur: float) -> None:
        nonlocal sim_time
        sim_time += dur

    orig_scenarios = list(SCENARIOS)
    try:
        SCENARIOS.clear()
        SCENARIOS.append(orig_scenarios[0])
        code = main(
            [
                "--url",
                "https://agent-wallet-fake.example.com",
                "--execute",
                "--rounds",
                "1",
                "--out",
                str(out_file),
                "--state",
                str(state_file),
            ],
            client=client,
            now_fn=fake_now,
            sleep_fn=fake_sleep,
        )
    finally:
        SCENARIOS.clear()
        SCENARIOS.extend(orig_scenarios)

    assert code == 0
    # Mint job was called and balance increased
    mint_calls = [c for c in client.sent_jobs if c[0] == "/agent/mint"]
    assert len(mint_calls) == 1
    assert client.usdc_balance >= 20_000_000


def test_pure_logic_helpers() -> None:
    assert is_rate_limited({"http": 429}) is True
    assert is_rate_limited({"problem": {"status": 429}}) is True
    assert is_rate_limited({"problem": {"slug": "rate-limited"}}) is True
    assert is_rate_limited({"http": 200, "status": "succeeded"}) is False

    assert parse_retry_after({"problem": {"retry_after": 45.0}}) == 45.0
    assert parse_retry_after({"problem": {"headers": {"Retry-After": "60"}}}) == 60.0
    assert parse_retry_after({"problem": {"detail": "Rate limited. Wait 30 seconds."}}) == 30.0
    assert parse_retry_after({}) == 300.0

    recs = [
        {"steps": [{"tx_hash": "0x123", "outcome": "executed"}], "verdict_ok": True},
        {"steps": [{"outcome": "denied"}], "verdict_ok": True},
        {"steps": [], "verdict_ok": False},
    ]
    counts = compute_counts(recs)
    assert counts["steps_run"] == 3
    assert counts["transactions_executed"] == 1
    assert counts["denied_by_policy"] == 1
    assert counts["no_action"] == 1
    assert counts["unexpected"] == 1

    report = render_report(
        owner_address="0x1111111111111111111111111111111111111111",
        agent_address="0x2222222222222222222222222222222222222222",
        started="2026-10-18T10:00:00Z",
        url="https://agent-wallet-fake.example.com",
        records=recs,
        audit_verify={"ok": True, "records": 10},
        hostile_failed=False,
    )
    assert "Multi-protocol loop evidence" in report
    assert "Audit verification" in report
