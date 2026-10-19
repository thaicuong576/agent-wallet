"""Shared constants, helpers and exceptions used by Postgres storage adapters."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from core.storage.interfaces import JobInProgress

GENESIS_PREV_HASH = "0" * 64
ALLOWED_TIERS = frozenset({"T0", "T1", "T2"})
LOCK_POLL_INTERVAL_S = 0.1
_OWNER_RE = re.compile(r"^0x[0-9a-f]{40}$")
_NON_SPEND_FUNCTIONS = ("approve", "mint", "transfer")


class ActiveJobExists(JobInProgress, ValueError):
    """The owner already has a queued or running job (``job_id`` names it when known)."""


def validate_owner(owner: str) -> str:
    """The owner address must already be lowercase and well formed."""
    if not isinstance(owner, str) or not _OWNER_RE.match(owner):
        raise ValueError(f"Invalid owner address: {owner!r}")
    return owner


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_ts(value: datetime | str) -> datetime:
    """RFC 3339 text (``...Z`` accepted) to an aware UTC datetime."""
    if isinstance(value, datetime):
        return _utc(value)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return _utc(datetime.fromisoformat(s))


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _parse_ts(str(value)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(UTC)
