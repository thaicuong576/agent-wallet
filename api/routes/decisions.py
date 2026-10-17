"""Decision listing and audit chain query routes."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.problems import make_problem_response
from api.schemas import UUID_REGEX, get_validator

router = APIRouter()


@router.get("/decisions")
def list_decisions(
    order_id: str | None = None,
    outcome: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    *,
    request: Request,
) -> JSONResponse:
    audit_sink = request.app.state.scopes.read(request).audit_sink
    if order_id is not None and not UUID_REGEX.match(order_id):
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Query Parameter",
            detail="order_id query parameter must be a lowercase UUID.",
        )

    valid_outcomes = {
        "no_action",
        "denied",
        "pending_approval",
        "executed",
        "failed",
    }
    if outcome is not None and outcome not in valid_outcomes:
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Query Parameter",
            detail=f"outcome must be one of {sorted(valid_outcomes)}.",
        )

    if limit < 1 or limit > 200:
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Query Parameter",
            detail="limit must be between 1 and 200.",
        )

    if cursor is not None and len(cursor) > 512:
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Query Parameter",
            detail="cursor must not exceed 512 characters.",
        )

    # Parse cursor (opaque seq representation)
    seq_cursor: int | None = None
    if cursor is not None:
        try:
            # Try decoding urlsafe base64 if possible
            decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            seq_cursor = int(decoded)
        except Exception:
            try:
                seq_cursor = int(cursor)
            except ValueError:
                return make_problem_response(
                    status=422,
                    problem_type="invalid-request",
                    title="Invalid Cursor",
                    detail="cursor is invalid.",
                )

    raw_records = list(audit_sink.records) if hasattr(audit_sink, "records") else list(audit_sink)
    filtered = []
    for r in raw_records:
        if order_id is not None and r.get("order_id") != order_id:
            continue
        if outcome is not None and r.get("outcome") != outcome:
            continue
        if seq_cursor is not None and r.get("seq", 0) <= seq_cursor:
            continue
        filtered.append(r)

    page = filtered[:limit]
    next_cursor = None
    if len(filtered) > limit:
        last_seq = page[-1].get("seq", 0)
        next_cursor = base64.urlsafe_b64encode(str(last_seq).encode("utf-8")).decode("ascii")

    body = {
        "schema_version": 1,
        "items": page,
        "next_cursor": next_cursor,
    }
    get_validator("decisions-response").validate(body)
    return JSONResponse(status_code=200, content=body)
