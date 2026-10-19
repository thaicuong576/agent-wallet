"""Job rate and concurrency limits."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from api.auth import AuthProblem
from core.storage.interfaces import JobInProgress, RateLimited


@dataclass(frozen=True)
class JobLimits:
    """Every job limit in one place."""

    runs_per_hour: int = 6
    chat_inquiries_per_hour: int = 60
    mint_calls_per_hour: int = 4
    note_max_chars: int = 2000

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JobLimits:
        env = os.environ if env is None else env
        raw = env.get("AGENT_WALLET_MINT_CALLS_PER_HOUR", "")
        mint = (
            int(raw)
            if re.fullmatch(r"[0-9]{1,6}", raw) and int(raw) > 0
            else cls.mint_calls_per_hour
        )
        raw_inq = env.get("AGENT_WALLET_CHAT_INQUIRIES_PER_HOUR", "")
        inq = (
            int(raw_inq)
            if re.fullmatch(r"[0-9]{1,6}", raw_inq) and int(raw_inq) > 0
            else cls.chat_inquiries_per_hour
        )
        return cls(mint_calls_per_hour=mint, chat_inquiries_per_hour=inq)

    @property
    def chat_inquiry_per_hour(self) -> int:
        return self.chat_inquiries_per_hour

    @property
    def chat_actions_per_hour(self) -> int:
        return self.runs_per_hour

    @property
    def chat_action_per_hour(self) -> int:
        return self.runs_per_hour

    def per_hour(self, kind: str) -> int | None:
        return {
            "run": self.runs_per_hour,
            "chat": self.runs_per_hour,
            "chat_action": self.runs_per_hour,
            "chat_inquiry": self.chat_inquiries_per_hour,
            "mint": self.mint_calls_per_hour,
        }.get(kind)


# ---------------------------------------------------------------- problems (RFC 7807)


def job_in_progress(job_id: str) -> AuthProblem:
    return AuthProblem(
        409,
        "job-in-progress",
        "A job is already running",
        "wait for the active job to finish",
        extra={"job_id": job_id},
    )


def rate_limited(retry_after_s: int) -> AuthProblem:
    return AuthProblem(
        429,
        "rate-limited",
        "Too many requests",
        "the hourly limit for this action is used up",
        headers={"Retry-After": str(max(1, retry_after_s))},
    )


__all__ = ["JobInProgress", "JobLimits", "RateLimited", "job_in_progress", "rate_limited"]
