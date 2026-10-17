"""Conversation history formatting and message sanitization."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REPLY_MAX_CHARS = 600
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_reply(value: Any) -> str | None:
    """The model's conversational reply as plain text: no control characters, bounded length."""
    if not isinstance(value, str):
        return None
    text = _CONTROL_RE.sub(" ", value).strip()
    return text[:REPLY_MAX_CHARS] or None


_TX_LIST_RE = re.compile(
    r"\s*Transactions?:\s*0x[0-9a-fA-F]{64}(?:,\s*0x[0-9a-fA-F]{64})*\.?",
    re.IGNORECASE,
)
_AGENT_SAYS_RE = re.compile(r"(?:^|\n)\s*Agent says:\s*", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def clean_history_text(text: Any, max_chars: int = 400) -> str:
    """Clean conversation history text for the planner.

    Removes "Agent says: " prefix, transaction hash lists, control characters,
    collapses runs of whitespace, trims, and cuts to max_chars.
    """
    if not isinstance(text, str):
        return ""
    if not text:
        return ""
    text = _AGENT_SAYS_RE.sub(" ", text)
    text = _TX_LIST_RE.sub(" ", text)
    text = _CONTROL_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:max_chars]


VALID_INTENTS = {"question", "clarify", "action", "refusal"}
HISTORY_MAX_TURNS = 10
HISTORY_MAX_CHARS = 3500


def format_conversation_history(
    history: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    """Format and bound conversation history for the planner payload (chat v3).

    Last 10 turns of the same thread, at most 3,500 characters total, oldest dropped first.
    """
    if not history:
        return []
    turns: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "user")
        text = str(item.get("text") or "")
        turn: dict[str, str] = {"role": role, "text": text}
        if item.get("intent") is not None:
            turn["intent"] = str(item["intent"])
        turns.append(turn)
    if len(turns) > HISTORY_MAX_TURNS:
        turns = turns[-HISTORY_MAX_TURNS:]
    while turns and sum(len(t["text"]) for t in turns) > HISTORY_MAX_CHARS:
        turns.pop(0)
    return turns
