"""Chat routes: talk to the agent and read the conversation.

`POST /chat` starts a job of kind `chat`; the same one-active-job rule and the same hourly limit as
`/runs` apply (409 and 429). The owner comes from the session only. The route proposes through the
planner with `origin="planner"`: it has no way to mint, transfer, withdraw or change a tier, and
every order the planner returns still goes through the policy engine.

`GET /chat` returns the owner's messages oldest first. The steps under an agent message are
rebuilt from the audit records the gateway wrote for that job, never from model prose.

Threads: `GET /chat/threads` lists the owner's conversations,
newest first, `POST /chat/threads` opens an empty one (or reuses the owner's newest thread if
it has no messages), and `POST /chat` / `GET /chat` take an optional `thread_id`. Without it the
implicit default thread (the messages written before threads existed) is used. A thread id of
another owner is unknown (404). The title is the first 40 characters of the first owner message,
set by code.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from api.auth import AuthProblem
from api.chat_compose import steps_from_records
from api.jobs import CHAT_MESSAGE_MAX_CHARS
from api.sse import chat_stream_response
from api.wallet import current_tenant, get_webapp
from core.gateway.tenant import Tenant

router = APIRouter()
log = logging.getLogger(__name__)

HISTORY_DEFAULT = 50
HISTORY_MAX = 200
THREADS_DEFAULT = 30
THREADS_MAX = 100


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    thread_id: str | None = Field(default=None, min_length=1, max_length=64)


class ThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _known_thread(tenant: Tenant, thread_id: str | None) -> None:
    """A thread id the owner does not have (or another owner's) is simply unknown."""
    if thread_id is not None and not tenant.stores.chat.thread_exists(thread_id):
        raise AuthProblem(404, "not-found", "Not found", "unknown thread")


def _records(tenant: Tenant, seqs: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seq in seqs:
        try:
            out.append(tenant.gateway.audit_sink[seq])
        except Exception:  # noqa: BLE001 - a missing record must not break the conversation
            continue
    return out


def _view(tenant: Tenant, message: dict[str, Any], jobs: Any) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    phase = "done"
    job_id = message.get("job_id")
    if message["role"] == "agent" and job_id:
        row = jobs.store(tenant).get(job_id)
        if row is not None:
            steps = steps_from_records(_records(tenant, row["seqs"]))
            phase = jobs.phase(tenant, row)
    view: dict[str, Any] = {
        "id": message["id"],
        "role": message["role"],
        "text": message["text"],
        "job_id": job_id,
        "created_at": message["created_at"],
        "steps": steps,
        "phase": phase,
    }
    if message.get("intent") is not None:
        view["intent"] = message["intent"]
    return view


@router.post("/chat", status_code=202, tags=["chat"])
def post_chat(
    body: ChatRequest, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, str]:
    _known_thread(tenant, body.thread_id)
    jobs = get_webapp(request).jobs
    job_id = jobs.start(tenant, "chat", {"message": body.message, "thread_id": body.thread_id})
    if body.thread_id is not None:
        try:
            tenant.stores.chat.attach_job(body.thread_id, job_id)
        except Exception as exc:  # noqa: BLE001 - the job is already running; keep it
            log.warning("job %s not attached to its thread: %s", job_id, type(exc).__name__)
    return {"job_id": job_id, "status": "queued"}


@router.get("/chat", tags=["chat"])
def get_chat(
    request: Request,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    limit: Annotated[int, Query(ge=1, le=HISTORY_MAX)] = HISTORY_DEFAULT,
    thread_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> dict[str, Any]:
    _known_thread(tenant, thread_id)
    jobs = get_webapp(request).jobs
    messages = tenant.stores.chat.list(limit, thread_id)
    return {"items": [_view(tenant, m, jobs) for m in messages]}


@router.get("/chat/threads", tags=["chat"])
def get_threads(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    limit: Annotated[int, Query(ge=1, le=THREADS_MAX)] = THREADS_DEFAULT,
) -> dict[str, Any]:
    return {"items": tenant.stores.chat.list_threads(limit)}


@router.post("/chat/threads", status_code=201, tags=["chat"])
def post_thread(
    body: ThreadRequest,
    response: Response,
    tenant: Annotated[Tenant, Depends(current_tenant)],
) -> dict[str, str]:
    empty = tenant.stores.chat.newest_empty_thread()
    if empty is not None:
        response.status_code = 200
        return {"id": empty["id"], "title": empty["title"]}
    thread = tenant.stores.chat.create_thread(datetime.now(UTC))
    response.status_code = 201
    return {"id": thread["id"], "title": thread["title"]}


@router.get("/chat/stream/{job_id}", tags=["chat"])
async def get_chat_stream(
    job_id: str,
    request: Request,
    tenant: Annotated[Tenant, Depends(current_tenant)],
) -> StreamingResponse:
    return chat_stream_response(request, tenant, job_id)
