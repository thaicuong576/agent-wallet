"""Tests for LLMClient, OpenAIClient, and ScriptedClient."""

from __future__ import annotations

import json
import urllib.request
from unittest.mock import MagicMock

import pytest

from core.agent.client import (
    LLMClient,
    LLMError,
    OpenAIClient,
    ReplyExtractor,
    ScriptedClient,
)


def test_openai_client_missing_key_raises_error(monkeypatch):
    """When OPENAI_API_KEY is unset, complete() raises ValueError."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = OpenAIClient()
    with pytest.raises(ValueError, match="OPENAI_API_KEY environment variable is not set"):
        client.complete("system", "user")


def test_openai_client_reads_env_key_at_call_time(monkeypatch):
    """Reads OPENAI_API_KEY from env at call time and passes it in Authorization header."""
    fake_key = "mock-key-call-time"
    captured_req = None

    def fake_caller(req: urllib.request.Request, timeout: float) -> bytes:
        nonlocal captured_req
        captured_req = req
        return json.dumps(
            {"choices": [{"message": {"content": '{"action": "no_action"}'}}]}
        ).encode("utf-8")

    client = OpenAIClient(http_caller=fake_caller)
    monkeypatch.setenv("OPENAI_API_KEY", fake_key)

    result = client.complete("System prompt", "User prompt")
    assert result == '{"action": "no_action"}'
    assert captured_req is not None
    assert captured_req.get_header("Authorization") == f"Bearer {fake_key}"
    assert captured_req.get_header("Content-type") == "application/json"


def test_openai_client_model_default_and_env_override(monkeypatch):
    """Uses gpt-4o-mini by default, or model from AGENT_WALLET_LLM_MODEL."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-key")
    captured_payloads = []

    def mock_caller(req: urllib.request.Request, timeout: float) -> bytes:
        captured_payloads.append(json.loads(req.data.decode("utf-8")))
        return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

    monkeypatch.delenv("AGENT_WALLET_LLM_MODEL", raising=False)
    client1 = OpenAIClient(http_caller=mock_caller)
    client1.complete("sys", "usr")
    assert captured_payloads[-1]["model"] == "gpt-4o-mini"

    monkeypatch.setenv("AGENT_WALLET_LLM_MODEL", "gpt-4o-custom")
    client2 = OpenAIClient(http_caller=mock_caller)
    client2.complete("sys", "usr")
    assert captured_payloads[-1]["model"] == "gpt-4o-custom"


def test_openai_client_schema_response_format(monkeypatch):
    """Passes response_format json_schema when schema argument is provided."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-key")
    captured_payload = None

    def mock_caller(req: urllib.request.Request, timeout: float) -> bytes:
        nonlocal captured_payload
        captured_payload = json.loads(req.data.decode("utf-8"))
        return json.dumps({"choices": [{"message": {"content": '{"status": "ok"}'}}]}).encode(
            "utf-8"
        )

    schema = {"type": "object", "properties": {"status": {"type": "string"}}}
    client = OpenAIClient(http_caller=mock_caller)
    res = client.complete("sys", "usr", schema=schema)
    assert res == '{"status": "ok"}'
    assert captured_payload is not None
    assert captured_payload["response_format"]["type"] == "json_schema"
    assert captured_payload["response_format"]["json_schema"]["schema"] == schema


def test_openai_client_never_echoes_key_on_failure(monkeypatch):
    """The API key never appears in any raised exception message or log."""
    secret_key = "mock-super-secret-key-xyz"
    monkeypatch.setenv("OPENAI_API_KEY", secret_key)

    def failing_caller(req: urllib.request.Request, timeout: float) -> bytes:
        # Simulate network failure echoing authorization header / key
        raise RuntimeError(f"Connection failed for Authorization: Bearer {secret_key}")

    client = OpenAIClient(http_caller=failing_caller)

    with pytest.raises(LLMError) as exc_info:
        client.complete("sys", "usr")

    err_str = str(exc_info.value)
    assert secret_key not in err_str
    assert "[REDACTED" in err_str


def test_openai_client_never_echoes_key_on_malformed_json(monkeypatch):
    """Malformed JSON response raises LLMError without exposing key."""
    secret_key = "mock-key-for-json-test"
    monkeypatch.setenv("OPENAI_API_KEY", secret_key)

    def bad_json_caller(req: urllib.request.Request, timeout: float) -> bytes:
        return b"<html>502 Bad Gateway with secret " + secret_key.encode() + b"</html>"

    client = OpenAIClient(http_caller=bad_json_caller)
    with pytest.raises(LLMError) as exc_info:
        client.complete("sys", "usr")

    err_str = str(exc_info.value)
    assert secret_key not in err_str


def test_openai_client_repr_and_str_never_contain_key(monkeypatch):
    """Client repr never exposes secret key."""
    secret_key = "mock-key-must-not-be-in-repr"
    client = OpenAIClient(api_key=secret_key, model="gpt-4o-mini")
    assert secret_key not in repr(client)
    assert secret_key not in str(client)


def test_scripted_client_sequence_and_call_recording():
    """ScriptedClient replays responses in sequence and tracks prompt calls."""
    responses = ['{"action": "step1"}', '{"action": "step2"}']
    client = ScriptedClient(responses)

    assert isinstance(client, LLMClient)
    res1 = client.complete("sys1", "usr1")
    assert res1 == '{"action": "step1"}'
    assert len(client.calls) == 1
    assert client.calls[0] == {"system": "sys1", "user": "usr1", "schema": None}

    res2 = client.complete("sys2", "usr2", schema={"type": "object"})
    assert res2 == '{"action": "step2"}'
    assert len(client.calls) == 2
    assert client.calls[1]["schema"] == {"type": "object"}

    with pytest.raises(IndexError, match="exhausted all configured responses"):
        client.complete("sys3", "usr3")


def test_scripted_client_callable_and_mapping():
    """ScriptedClient supports custom dispatch callable and mapping."""
    mapping_client = ScriptedClient(
        {"portfolio": '{"ans": "portfolio"}', "*": '{"ans": "default"}'}
    )
    assert mapping_client.complete("sys", "Check portfolio now") == '{"ans": "portfolio"}'
    assert mapping_client.complete("sys", "Something else") == '{"ans": "default"}'

    def echo_caller(sys, usr, schema):
        return f"Echo: {usr}"

    callable_client = ScriptedClient(echo_caller)
    assert callable_client.complete("sys", "hello") == "Echo: hello"


# ---------------------------------------------------------------- ReplyExtractor tests


def test_reply_extractor_split_at_every_possible_chunk_boundary() -> None:
    full_json = (
        '{"reply": "Hello world! All split boundaries give the same answer.", "action": "propose"}'
    )
    expected = "Hello world! All split boundaries give the same answer."

    # 1. Single character chunks
    extractor_single = ReplyExtractor()
    out_single = "".join(extractor_single.feed(ch) for ch in full_json)
    assert out_single == expected

    # 2. Various chunk sizes
    for chunk_size in (1, 2, 3, 5, 7, 11, 19, 50, 100):
        chunks = [full_json[i : i + chunk_size] for i in range(0, len(full_json), chunk_size)]
        extractor = ReplyExtractor()
        assert "".join(extractor.feed(c) for c in chunks) == expected


def test_reply_extractor_escapes_split_across_chunks() -> None:
    raw_content = 'line1\nline2"quote"\\slash/tab\ttest\u00e9\u0041done'
    # Escaped JSON representation
    escaped_json = (
        '{"reply": "line1\\nline2\\"quote\\"\\\\slash\\/tab\\ttest\\u00e9\\u0041done", "extra": 1}'
    )

    # Split across chunk boundaries at each character
    extractor = ReplyExtractor()
    out = "".join(extractor.feed(ch) for ch in escaped_json)
    assert out == raw_content

    # Specifically test escape sequence split across boundaries
    part1 = '{"reply": "hello \\'
    part2 = "nworld \\u0"
    part3 = '0e9 cafe"}'
    ext = ReplyExtractor()
    assert ext.feed(part1) == "hello "
    assert ext.feed(part2) == "\nworld "
    assert ext.feed(part3) == "é cafe"


def test_reply_extractor_nested_reply_ignored() -> None:
    json_str = '{"order": {"reply": "nested fake reply"}, "reply": "top level real reply"}'
    ext = ReplyExtractor()
    out = "".join(ext.feed(ch) for ch in json_str)
    assert out == "top level real reply"

    # Also test nested after top level
    json_str2 = '{"reply": "first top level", "details": {"reply": "ignore nested"}}'
    ext2 = ReplyExtractor()
    out2 = "".join(ext2.feed(ch) for ch in json_str2)
    assert out2 == "first top level"


def test_reply_extractor_null_reply() -> None:
    json_str = '{"reply": null, "action": "no_action"}'
    ext = ReplyExtractor()
    out = "".join(ext.feed(ch) for ch in json_str)
    assert out == ""

    # With whitespace
    json_str_ws = '{"reply"  :   null  , "action": "no_action"}'
    ext_ws = ReplyExtractor()
    out_ws = "".join(ext_ws.feed(ch) for ch in json_str_ws)
    assert out_ws == ""


def test_reply_extractor_reply_absent() -> None:
    json_str = '{"action": "no_action", "reason": "portfolio already balanced"}'
    ext = ReplyExtractor()
    out = "".join(ext.feed(ch) for ch in json_str)
    assert out == ""


def test_reply_extractor_garbage_input_never_raises() -> None:
    garbage_cases = [
        "{not valid json at all",
        "random string without braces",
        '{"reply": [1, 2, 3]}',
        '{"reply": {}}',
        '{"reply": "unterminated',
        '{"reply": "\\uZZZZ"}',
        '{"reply": "\\x"}',
        "",
        "{",
        "}",
        "---",
    ]
    for g in garbage_cases:
        ext = ReplyExtractor()
        try:
            res = ext.feed(g)
            assert isinstance(res, str)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"ReplyExtractor raised on garbage {g!r}: {exc}")


def test_reply_extractor_emoji_pair_in_one_chunk() -> None:
    raw = r'{"reply": "Hello \uD83D\uDE00 world"}'
    ext = ReplyExtractor()
    out = ext.feed(raw)
    assert out == "Hello 😀 world"


def test_reply_extractor_emoji_pair_split_at_every_boundary() -> None:
    raw = r'{"reply": "Hello \uD83D\uDE00 world"}'
    # Test split into 2 chunks at every possible index
    for i in range(1, len(raw)):
        ext = ReplyExtractor()
        chunk1 = raw[:i]
        chunk2 = raw[i:]
        res = ext.feed(chunk1) + ext.feed(chunk2)
        assert res == "Hello 😀 world", f"Failed split at index {i}"

    # Test single character streaming
    ext_chars = ReplyExtractor()
    res_chars = "".join(ext_chars.feed(ch) for ch in raw)
    assert res_chars == "Hello 😀 world"


def test_reply_extractor_lone_high_surrogate() -> None:
    # High surrogate without following low surrogate (followed by normal text)
    raw = r'{"reply": "lone \uD83D text"}'
    ext = ReplyExtractor()
    out = ext.feed(raw)
    assert out == "lone \ufffd text"

    # High surrogate split across chunks and terminated by quote
    ext_split = ReplyExtractor()
    p1 = r'{"reply": "lone \uD83D'
    p2 = '"}'
    res1 = ext_split.feed(p1)
    res2 = ext_split.feed(p2)
    assert res1 + res2 == "lone \ufffd"

    # High surrogate followed by non-surrogate unicode escape
    raw_esc = r'{"reply": "\uD83D\u0041"}'
    ext_esc = ReplyExtractor()
    assert ext_esc.feed(raw_esc) == "\ufffdA"


def test_reply_extractor_lone_low_surrogate() -> None:
    # Low surrogate without preceding high surrogate
    raw = r'{"reply": "lone \uDE00 text"}'
    ext = ReplyExtractor()
    out = ext.feed(raw)
    assert out == "lone \ufffd text"

    # Low surrogate split across chunks
    ext_split = ReplyExtractor()
    p1 = r'{"reply": "lone \uDE'
    p2 = '00 text"}'
    res = ext_split.feed(p1) + ext_split.feed(p2)
    assert res == "lone \ufffd text"


# ------------------------------------------------ OpenAIClient.complete_stream tests


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)] if content is not None else []


def test_openai_client_complete_stream_returns_full_text(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-key")
    chunks = [
        _FakeChunk('{"reply": "'),
        _FakeChunk("Hello "),
        _FakeChunk("world"),
        _FakeChunk('", "action": "no_action"}'),
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = iter(chunks)

    def fake_factory(**kwargs):
        assert kwargs["api_key"] == "sk-mock-key"
        return mock_client

    client = OpenAIClient(sdk_factory=fake_factory)
    collected_tokens: list[str] = []
    res = client.complete_stream("sys", "usr", on_token=collected_tokens.append)

    assert res == '{"reply": "Hello world", "action": "no_action"}'
    assert collected_tokens == [
        '{"reply": "',
        "Hello ",
        "world",
        '", "action": "no_action"}',
    ]

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["stream"] is True
    assert call_kwargs["temperature"] == 0.0
    assert call_kwargs["model"] == "gpt-4o-mini"


def test_openai_client_complete_stream_redacts_key_on_error(monkeypatch) -> None:
    secret_key = "mock-super-secret-key-complete-stream-xyz"
    monkeypatch.setenv("OPENAI_API_KEY", secret_key)

    def failing_factory(**kwargs):
        raise RuntimeError(f"Connection failed for Authorization: Bearer {secret_key}")

    client = OpenAIClient(sdk_factory=failing_factory)
    with pytest.raises(LLMError) as exc_info:
        client.complete_stream("sys", "usr")

    err_str = str(exc_info.value)
    assert secret_key not in err_str
    assert "[REDACTED" in err_str


def test_openai_client_complete_stream_iteration_error_redacts_key(monkeypatch) -> None:
    secret_key = "mock-stream-error-key-12345"
    monkeypatch.setenv("OPENAI_API_KEY", secret_key)

    def failing_stream():
        yield _FakeChunk("start")
        raise RuntimeError(f"Stream dropped with key: {secret_key}")

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = failing_stream()

    client = OpenAIClient(sdk_factory=lambda **kw: mock_client)
    with pytest.raises(LLMError) as exc_info:
        client.complete_stream("sys", "usr")

    err_str = str(exc_info.value)
    assert secret_key not in err_str
    assert "[REDACTED_KEY]" in err_str


def test_openai_client_complete_stream_repr_has_no_key() -> None:
    secret_key = "mock-complete-stream-repr-secret"
    client = OpenAIClient(api_key=secret_key, sdk_factory=lambda **kw: MagicMock())
    assert secret_key not in repr(client)
    assert secret_key not in str(client)
