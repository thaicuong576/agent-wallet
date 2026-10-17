"""FastAPI router and request models for jobs endpoints."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import AuthProblem, Session, require_step_up
from api.wallet import current_tenant, get_webapp
from core.gateway.tenant import Tenant

router = APIRouter()


class MintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_usdc: str = Field(pattern=r"^[1-9][0-9]{0,6}$")


class WithdrawAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str
    decimals: int


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Literal["USDC", "ETH"]
    amount: WithdrawAmount


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)
    planner: Literal["auto", "scripted_hostile"] = "auto"


def _accepted(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "status": "queued"}


@router.post("/agent/mint", status_code=202, tags=["agent"])
def post_mint(
    body: MintRequest, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, str]:
    jobs = get_webapp(request).jobs
    return _accepted(jobs.start(tenant, "mint", {"amount_usdc": body.amount_usdc}))


@router.post("/agent/withdraw", status_code=202, tags=["agent"])
def post_withdraw(
    body: WithdrawRequest,
    request: Request,
    _session: Annotated[Session, Depends(require_step_up("withdraw"))],
    tenant: Annotated[Tenant, Depends(current_tenant)],
) -> dict[str, str]:
    jobs = get_webapp(request).jobs
    return _accepted(jobs.start(tenant, "withdraw", body.model_dump()))


@router.post("/runs", status_code=202, tags=["jobs"])
def post_run(
    request: Request,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    body: RunRequest | None = None,
) -> dict[str, str]:
    body = body or RunRequest()
    jobs = get_webapp(request).jobs
    return _accepted(jobs.start(tenant, "run", {"note": body.note, "planner": body.planner}))


@router.get("/jobs/{job_id}", tags=["jobs"])
def get_job(
    job_id: str, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    job = None
    if len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id):
        job = get_webapp(request).jobs.get(tenant, job_id)
    if job is None:
        raise AuthProblem(404, "not-found", "Not found", "unknown job")
    return job


@router.post("/jobs/{job_id}/cancel", status_code=202, tags=["jobs"])
def cancel_job(
    job_id: str, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, str]:
    jobs = get_webapp(request).jobs
    job = None
    if len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id):
        job = jobs.store(tenant).get(job_id)
    if job is None:
        raise AuthProblem(404, "not-found", "Not found", "unknown job")
    if not jobs.cancel(tenant, job_id):
        raise AuthProblem(
            409,
            "not-cancellable",
            "Job cannot be cancelled",
            "the job has already sent a transaction or finished",
            extra={"job_id": job_id, "error": "not-cancellable"},
        )
    return {"job_id": job_id, "status": "cancelling"}


@router.get("/jobs", tags=["jobs"])
def list_jobs(
    request: Request,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    records: int = 0,
) -> dict[str, Any]:
    with_records = records == 1
    return {"items": get_webapp(request).jobs.list(tenant, with_records=with_records)}
