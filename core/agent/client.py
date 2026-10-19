"""LLM client protocol and implementations for BaKhi Wallet."""

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
        sdk_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._explicit_key = api_key
        self._model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http_caller = http_caller
        self._sdk_factory = sdk_factory

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

    def complete_stream(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        """Execute a streaming completion via OpenAI chat completions API."""
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
            "stream": True,
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

        try:
            if self._sdk_factory is not None:
                client = self._sdk_factory(
                    api_key=api_key, base_url=self.base_url, timeout=self.timeout
                )
            else:
                import openai

                client = openai.OpenAI(
                    api_key=api_key, base_url=self.base_url, timeout=self.timeout
                )

            stream = client.chat.completions.create(**payload)
            chunks: list[str] = []
            for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    content = getattr(delta, "content", None) if delta is not None else None
                    if content:
                        chunks.append(content)
                        if on_token is not None:
                            on_token(content)
            return "".join(chunks)
        except Exception as exc:
            clean_msg = sanitize_key(str(exc), api_key)
            raise LLMError(f"OpenAI streaming request failed: {clean_msg}") from None


_ESCAPE_MAP = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_HEX_DIGITS = set("0123456789abcdefABCDEF")


class ReplyExtractor:
    """Incrementally extracts characters of top-level "reply" string from streamed JSON.

    State machine over JSON characters:
    - Finds key "reply" at root object depth 1.
    - Decodes string value handling JSON escapes (\\n, \\", \\\\, \\/, \\t, \\r, \\b, \\f, \\uXXXX).
    - Supports escape sequences split across chunk boundaries.
    - Ignores nested "reply" keys.
    - Ignores null replies.
    - Never raises: on any unexpected syntax or error, stops emitting.
    """

    def __init__(self) -> None:
        self._state = "SEEK_ROOT"
        self._depth = 0
        self._array_depth = 0
        self._key_buffer: list[str] = []
        self._is_reply_key = False
        self._unicode_hex: list[str] = []
        self._pending_high_surrogate: int | None = None
        self._in_string = False
        self._skip_escape = False

    def feed(self, chunk: str) -> str:
        """Feed a chunk of characters and return newly decoded reply characters."""
        if not chunk or self._state in ("DONE", "STOPPED"):
            return ""

        try:
            emitted: list[str] = []
            for ch in chunk:
                state = self._state
                if state in ("DONE", "STOPPED"):
                    break

                if state == "SEEK_ROOT":
                    if ch.isspace():
                        continue
                    if ch == "{":
                        self._depth = 1
                        self._state = "EXPECT_KEY"
                    else:
                        self._state = "STOPPED"

                elif state == "EXPECT_KEY":
                    if ch.isspace() or ch == ",":
                        continue
                    if ch == '"':
                        self._key_buffer = []
                        self._state = "READ_KEY"
                    elif ch == "}":
                        self._depth = 0
                        self._state = "DONE"
                    else:
                        self._state = "STOPPED"

                elif state == "READ_KEY":
                    if ch == '"':
                        key_name = "".join(self._key_buffer)
                        self._is_reply_key = key_name == "reply"
                        self._state = "EXPECT_COLON"
                    elif ch == "\\":
                        self._state = "READ_KEY_ESCAPE"
                    else:
                        self._key_buffer.append(ch)

                elif state == "READ_KEY_ESCAPE":
                    self._key_buffer.append(ch)
                    self._state = "READ_KEY"

                elif state == "EXPECT_COLON":
                    if ch.isspace():
                        continue
                    if ch == ":":
                        if self._is_reply_key:
                            self._state = "EXPECT_REPLY_VALUE"
                        else:
                            self._state = "EXPECT_VALUE"
                    else:
                        self._state = "STOPPED"

                elif state == "EXPECT_REPLY_VALUE":
                    if ch.isspace():
                        continue
                    if ch == '"':
                        self._state = "REPLY_STRING"
                    elif ch == "n":
                        self._state = "STOPPED"
                    else:
                        self._state = "STOPPED"

                elif state == "REPLY_STRING":
                    if ch == '"':
                        if self._pending_high_surrogate is not None:
                            emitted.append("\ufffd")
                            self._pending_high_surrogate = None
                        self._state = "DONE"
                    elif ch == "\\":
                        self._state = "REPLY_ESCAPE"
                    else:
                        if self._pending_high_surrogate is not None:
                            emitted.append("\ufffd")
                            self._pending_high_surrogate = None
                        if 0xD800 <= ord(ch) <= 0xDFFF:
                            emitted.append("\ufffd")
                        else:
                            emitted.append(ch)

                elif state == "REPLY_ESCAPE":
                    if ch in _ESCAPE_MAP:
                        if self._pending_high_surrogate is not None:
                            emitted.append("\ufffd")
                            self._pending_high_surrogate = None
                        emitted.append(_ESCAPE_MAP[ch])
                        self._state = "REPLY_STRING"
                    elif ch == "u":
                        self._unicode_hex = []
                        self._state = "REPLY_UNICODE"
                    else:
                        if self._pending_high_surrogate is not None:
                            emitted.append("\ufffd")
                            self._pending_high_surrogate = None
                        self._state = "STOPPED"

                elif state == "REPLY_UNICODE":
                    if ch in _HEX_DIGITS:
                        self._unicode_hex.append(ch)
                        if len(self._unicode_hex) == 4:
                            code = int("".join(self._unicode_hex), 16)
                            if 0xD800 <= code <= 0xDBFF:
                                if self._pending_high_surrogate is not None:
                                    emitted.append("\ufffd")
                                self._pending_high_surrogate = code
                            elif 0xDC00 <= code <= 0xDFFF:
                                if self._pending_high_surrogate is not None:
                                    high = self._pending_high_surrogate
                                    combined = 0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00)
                                    emitted.append(chr(combined))
                                    self._pending_high_surrogate = None
                                else:
                                    emitted.append("\ufffd")
                            else:
                                if self._pending_high_surrogate is not None:
                                    emitted.append("\ufffd")
                                    self._pending_high_surrogate = None
                                emitted.append(chr(code))
                            self._state = "REPLY_STRING"
                    else:
                        if self._pending_high_surrogate is not None:
                            emitted.append("\ufffd")
                            self._pending_high_surrogate = None
                        self._state = "STOPPED"

                elif state == "EXPECT_VALUE":
                    if ch.isspace():
                        continue
                    if ch == '"':
                        self._state = "SKIP_STRING"
                    elif ch == "{":
                        self._depth = 2
                        self._array_depth = 0
                        self._in_string = False
                        self._skip_escape = False
                        self._state = "SKIP_COMPLEX"
                    elif ch == "[":
                        self._depth = 1
                        self._array_depth = 1
                        self._in_string = False
                        self._skip_escape = False
                        self._state = "SKIP_COMPLEX"
                    elif ch in "-0123456789tfn":
                        self._state = "SKIP_PRIMITIVE"
                    else:
                        self._state = "STOPPED"

                elif state == "SKIP_STRING":
                    if ch == "\\":
                        self._state = "SKIP_STRING_ESCAPE"
                    elif ch == '"':
                        self._state = "EXPECT_COMMA_OR_END"

                elif state == "SKIP_STRING_ESCAPE":
                    self._state = "SKIP_STRING"

                elif state == "SKIP_PRIMITIVE":
                    if ch.isspace():
                        self._state = "EXPECT_COMMA_OR_END"
                    elif ch == ",":
                        self._state = "EXPECT_KEY"
                    elif ch == "}":
                        self._depth = 0
                        self._state = "DONE"
                    elif ch in "0123456789.eE+-alrseul":
                        continue
                    else:
                        self._state = "STOPPED"

                elif state == "EXPECT_COMMA_OR_END":
                    if ch.isspace():
                        continue
                    if ch == ",":
                        self._state = "EXPECT_KEY"
                    elif ch == "}":
                        self._depth = 0
                        self._state = "DONE"
                    else:
                        self._state = "STOPPED"

                elif state == "SKIP_COMPLEX":
                    if self._in_string:
                        if self._skip_escape:
                            self._skip_escape = False
                        elif ch == "\\":
                            self._skip_escape = True
                        elif ch == '"':
                            self._in_string = False
                    else:
                        if ch == '"':
                            self._in_string = True
                            self._skip_escape = False
                        elif ch == "{":
                            self._depth += 1
                        elif ch == "}":
                            self._depth -= 1
                            if self._depth == 1 and self._array_depth == 0:
                                self._state = "EXPECT_COMMA_OR_END"
                        elif ch == "[":
                            self._array_depth += 1
                        elif ch == "]":
                            self._array_depth -= 1
                            if self._depth == 1 and self._array_depth == 0:
                                self._state = "EXPECT_COMMA_OR_END"

            return "".join(emitted)
        except Exception:
            self._state = "STOPPED"
            return ""


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
