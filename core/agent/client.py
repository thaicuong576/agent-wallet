"""LLM client protocol and implementations for agent-wallet."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


class LLMError(Exception):
    """Raised when an LLM client fails or returns an invalid completion."""


def sanitize_key(text: str, key: str | None = None) -> str:
    """Ensure API keys or sensitive authorization tokens never appear in messages."""
    result = text
    if key and key.strip():
        result = result.replace(key, "[REDACTED_KEY]")
    # Redact any Bearer tokens
    result = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED_TOKEN]", result)
    # Redact common API key patterns (e.g. sk-...)
    result = re.sub(r"\bsk-[A-Za-z0-9_\-]{16,}\b", "[REDACTED_KEY]", result)
    # Redact 32-byte hex keys (64 hex characters)
    result = re.sub(r"\b[0-9a-fA-F]{64}\b", "[REDACTED_SECRET]", result)
    return result


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM completions."""

    def complete(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
    ) -> str:
        """Complete a prompt given system and user messages, with optional JSON schema."""
        ...


class OpenAIClient:
    """OpenAI API client using standard-library urllib only.

    Reads OPENAI_API_KEY from environment at call time. Never logs or echoes the key.
    Model is read from AGENT_WALLET_LLM_MODEL env var (default: 'gpt-4o-mini').
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        http_caller: (
            Callable[[urllib.request.Request, float], bytes | str | dict[str, Any]] | None
        ) = None,
    ) -> None:
        self._explicit_key = api_key
        self._model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http_caller = http_caller

    def __repr__(self) -> str:
        return f"OpenAIClient(model={self._model!r}, base_url={self.base_url!r})"

    def complete(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
    ) -> str:
        """Execute a completion via OpenAI chat completions API."""
        api_key = self._explicit_key or os.environ.get("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            raise ValueError("OPENAI_API_KEY environment variable is not set")

        model = self._model or os.environ.get("AGENT_WALLET_LLM_MODEL", "gpt-4o-mini")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": schema,
                },
            }

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            if self._http_caller is not None:
                raw_response = self._http_caller(req, self.timeout)
            else:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw_response = resp.read()
        except Exception as exc:
            clean_msg = sanitize_key(str(exc), api_key)
            raise LLMError(f"OpenAI request failed: {clean_msg}") from None

        try:
            if isinstance(raw_response, dict):
                data = raw_response
            elif isinstance(raw_response, bytes):
                data = json.loads(raw_response.decode("utf-8"))
            elif isinstance(raw_response, str):
                data = json.loads(raw_response)
            elif hasattr(raw_response, "read"):
                data = json.loads(raw_response.read().decode("utf-8"))
            else:
                raise ValueError(f"Unsupported response type: {type(raw_response).__name__}")
        except Exception as exc:
            clean_msg = sanitize_key(str(exc), api_key)
            raise LLMError(f"Failed to decode OpenAI response: {clean_msg}") from None

        try:
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Response message content is not a string")
            return content
        except Exception as exc:
            clean_msg = sanitize_key(str(exc), api_key)
            raise LLMError(f"OpenAI response missing completion content: {clean_msg}") from None


class ScriptedClient:
    """Scripted LLM client for deterministic testing and offline replay."""

    def __init__(
        self,
        responses: (
            Sequence[str]
            | Mapping[str, str]
            | Callable[[str, str, dict[str, Any] | None], str]
            | str
            | None
        ) = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses
        self._index = 0

    def complete(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
    ) -> str:
        call_info = {"system": system, "user": user, "schema": schema}
        self.calls.append(call_info)

        if self._responses is None:
            return "{}"

        if callable(self._responses):
            return self._responses(system, user, schema)

        if isinstance(self._responses, str):
            return self._responses

        if isinstance(self._responses, Mapping):
            for key, val in self._responses.items():
                if key in user or key in system:
                    return val
            return self._responses.get("*", "{}")

        if isinstance(self._responses, Sequence):
            if self._index < len(self._responses):
                res = self._responses[self._index]
                self._index += 1
                return res
            raise IndexError("ScriptedClient exhausted all configured responses")

        return "{}"
