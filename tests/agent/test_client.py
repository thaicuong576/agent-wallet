"""Tests for LLMClient, OpenAIClient, and ScriptedClient."""

from __future__ import annotations

import json
import urllib.request

import pytest

from core.agent.client import LLMClient, LLMError, OpenAIClient, ScriptedClient


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
