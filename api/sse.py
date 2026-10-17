"""Server-Sent Events helper and chat progress stream (ticket C-C).

Adapted from ideas in `sysid/sse-starlette` (BSD-3-Clause) on top of Starlette's
`StreamingResponse`. Formats SSE events, emits comment pings every 15 s, and
terminates cleanly on client disconnect without cancelling the underlying job.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from starlette.responses import StreamingResponse

from api.auth import AuthProblem
from api.jobs import ACTIVE, _parse
from api.wallet import get_webapp
from core.gateway.tenant import Tenant

log = logging.getLogger("agent_wallet.sse")

PING_INTERVAL_S: float = 15.0
POLL_INTERVAL_S: float = 0.5
DEFAULT_SEPARATOR: str = "\r\n"
_LINE_SEP_EXPR = re.compile(r"\r\n|\r|\n")


class ServerSentEvent:
    """Helper class to format Server-Sent Events according to WHATWG SSE spec."""

    def __init__(
        self,
        data: Any = None,
        *,
        event: str | None = None,
        id: str | None = None,
        retry: int | None = None,
        comment: str | None = None,
        sep: str = DEFAULT_SEPARATOR,
    ) -> None:
        self.data = data
        self.event = event
        self.id = id
        self.retry = retry
        self.comment = comment
        self._sep = sep

    def encode(self) -> bytes:
        buffer = io.StringIO()
        if self.comment is not None:
            for chunk in _LINE_SEP_EXPR.split(str(self.comment)):
                buffer.write(f": {chunk}{self._sep}")

        if self.id is not None:
            clean_id = _LINE_SEP_EXPR.sub("", str(self.id))
            buffer.write(f"id: {clean_id}{self._sep}")

        if self.event is not None:
            clean_event = _LINE_SEP_EXPR.sub("", str(self.event))
            buffer.write(f"event: {clean_event}{self._sep}")

        if self.data is not None:
            if isinstance(self.data, str):
                data_str = self.data
            else:
                data_str = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
            for chunk in _LINE_SEP_EXPR.split(data_str):
                buffer.write(f"data: {chunk}{self._sep}")

        if self.retry is not None:
            if not isinstance(self.retry, int):
                raise TypeError("retry argument must be int")
            buffer.write(f"retry: {self.retry}{self._sep}")

        buffer.write(self._sep)
        return buffer.getvalue().encode("utf-8")


def format_sse(
    data: Any = None,
    *,
    event: str | None = None,
    id: str | None = None,
    retry: int | None = None,
    comment: str | None = None,
    sep: str = DEFAULT_SEPARATOR,
) -> bytes:
    """Convenience helper to encode an SSE event into bytes."""
    return ServerSentEvent(
        data=data, event=event, id=id, retry=retry, comment=comment, sep=sep
    ).encode()


async def stream_chat_events(
    request: Request,
    tenant: Tenant,
    job_id: str,
    *,
    ping_interval: float = PING_INTERVAL_S,
    poll_interval: float = POLL_INTERVAL_S,
) -> AsyncGenerator[bytes, None]:
    """Yield SSE bytes for job progress until terminal status, then emit done event."""
    jobs_service = get_webapp(request).jobs
    row = jobs_service.store(tenant).get(job_id)
    if row is None:
        return

    try:
        created_at_dt = _parse(row["created_at"])
    except Exception:
        created_at_dt = datetime.now(UTC)

    event_id = 0
    token_index = 0
    last_phase = None
    last_ping = time.monotonic()

    try:
        while True:
            if await request.is_disconnected():
                break

            row = jobs_service.store(tenant).get(job_id)
            if row is None:
                break

            status = row.get("status")
            current_phase = jobs_service.phase(tenant, row)
            now_dt = datetime.now(UTC)
            elapsed = max(0.0, (now_dt - created_at_dt).total_seconds())

            if status in ACTIVE:
                if current_phase != last_phase and current_phase != "done":
                    event_id += 1
                    last_phase = current_phase
                    payload = {
                        "phase": current_phase,
                        "elapsed_seconds": round(elapsed, 1),
                    }
                    yield ServerSentEvent(data=payload, event="phase", id=str(event_id)).encode()

                all_tokens = jobs_service.tokens_since(job_id, 0)
                if token_index > 0 and len(all_tokens) < token_index:
                    event_id += 1
                    yield ServerSentEvent(data={}, event="reset", id=str(event_id)).encode()
                    token_index = 0

                new_pieces = all_tokens[token_index:]
                for piece in new_pieces:
                    event_id += 1
                    yield ServerSentEvent(
                        data={"text": piece}, event="token", id=str(event_id)
                    ).encode()
                    token_index += 1

                now_mono = time.monotonic()
                if now_mono - last_ping >= ping_interval:
                    last_ping = now_mono
                    yield ServerSentEvent(comment="ping").encode()

                await asyncio.sleep(poll_interval)
            else:
                # Before the done event, also flush tokens not yet sent
                all_tokens = jobs_service.tokens_since(job_id, 0)
                if token_index > 0 and 0 < len(all_tokens) < token_index:
                    event_id += 1
                    yield ServerSentEvent(data={}, event="reset", id=str(event_id)).encode()
                    token_index = 0

                new_pieces = all_tokens[token_index:] if all_tokens else []
                for piece in new_pieces:
                    event_id += 1
                    yield ServerSentEvent(
                        data={"text": piece}, event="token", id=str(event_id)
                    ).encode()
                    token_index += 1

                # Terminal status reached: emit final done event
                event_id += 1
                done_data = jobs_service.get(tenant, job_id)
                yield ServerSentEvent(data=done_data, event="done", id=str(event_id)).encode()
                break
    except (asyncio.CancelledError, GeneratorExit):
        # Client disconnected - a disconnect never cancels the job
        pass


def chat_stream_response(
    request: Request,
    tenant: Tenant,
    job_id: str,
    *,
    ping_interval: float | None = None,
    poll_interval: float | None = None,
) -> StreamingResponse:
    """Validate job ownership and return Starlette StreamingResponse for SSE."""
    if len(job_id) != 32 or not all(c in "0123456789abcdef" for c in job_id):
        raise AuthProblem(404, "not-found", "Not found", "unknown job")

    jobs_service = get_webapp(request).jobs
    row = jobs_service.store(tenant).get(job_id)
    if row is None:
        raise AuthProblem(404, "not-found", "Not found", "unknown job")

    p_interval = ping_interval if ping_interval is not None else PING_INTERVAL_S
    poll_int = poll_interval if poll_interval is not None else POLL_INTERVAL_S

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        stream_chat_events(
            request, tenant, job_id, ping_interval=p_interval, poll_interval=poll_int
        ),
        media_type="text/event-stream",
        headers=headers,
    )
