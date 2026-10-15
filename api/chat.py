"""Chat routes: talk to the agent and read the conversation (ticket 115).

`POST /chat` starts a job of kind `chat`; the same one-active-job rule and the same hourly limit as
`/runs` apply (409 and 429). The owner comes from the session only. The route proposes through the
planner with `origin="planner"`: it has no way to mint, transfer, withdraw or change a tier, and
every order the planner returns still goes through the policy engine.

`GET /chat` returns the owner's messages oldest first. The steps under an agent message are
rebuilt from the audit records the gateway wrote for that job, never from model prose.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.chat_compose import steps_from_records
from api.jobs import CHAT_MESSAGE_MAX_CHARS
from api.wallet import current_tenant, get_webapp
from core.gateway.tenant import Tenant

router = APIRouter()

HISTORY_DEFAULT = 50
HISTORY_MAX = 200


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)


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
    return {
        "id": message["id"],
        "role": message["role"],
        "text": message["text"],
        "job_id": job_id,
        "created_at": message["created_at"],
        "steps": steps,
        "phase": phase,
    }


@router.post("/chat", status_code=202, tags=["chat"])
def post_chat(
    body: ChatRequest, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, str]:
    jobs = get_webapp(request).jobs
    job_id = jobs.start(tenant, "chat", {"message": body.message})
    return {"job_id": job_id, "status": "queued"}


@router.get("/chat", tags=["chat"])
def get_chat(
    request: Request,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    limit: Annotated[int, Query(ge=1, le=HISTORY_MAX)] = HISTORY_DEFAULT,
) -> dict[str, Any]:
    jobs = get_webapp(request).jobs
    messages = tenant.stores.chat.list(limit)
    return {"items": [_view(tenant, m, jobs) for m in messages]}
