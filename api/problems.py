"""RFC 7807 problem details responses and timestamp formatting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.responses import JSONResponse

from api.schemas import get_validator


def make_problem_response(
    status: int,
    problem_type: str,
    title: str,
    detail: str | None = None,
    instance: str | None = None,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    """Construct an RFC 7807 Problem Details JSONResponse validating against the schema."""
    content: dict[str, Any] = {
        "type": f"urn:agent-wallet:problem:{problem_type}",
        "title": title,
        "status": status,
    }
    if detail is not None:
        content["detail"] = detail[:500]
    if instance is not None:
        content["instance"] = instance
    if errors is not None:
        content["errors"] = errors

    get_validator("problem").validate(content)
    return JSONResponse(
        status_code=status,
        content=content,
        media_type="application/problem+json",
    )


def utc_now_iso() -> str:
    """Return RFC 3339 UTC timestamp with trailing Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["make_problem_response", "utc_now_iso"]
