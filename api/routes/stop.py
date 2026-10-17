"""Owner emergency stop routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.problems import make_problem_response, utc_now_iso
from api.schemas import get_validator

router = APIRouter()


@router.post("/emergency-stop")
def engage_emergency_stop(request: Request) -> JSONResponse:
    scopes = request.app.state.scopes
    scope = scopes.owner(request)  # fail-safe direction: the session alone is enough
    if isinstance(scope, JSONResponse):
        return scope
    tier_view, owner_controls = scope.tier_view, scope.owner_controls
    audit_sink, active_ips_hash = scope.audit_sink, scope.ips_hash

    if tier_view.is_stopped():
        return make_problem_response(
            status=409,
            problem_type="emergency-stop-state",
            title="Emergency Stop State Conflict",
            detail="Emergency stop is already engaged.",
        )

    owner_controls.engage_stop()
    if scope.tenant is None and hasattr(scope.gateway, "emergency_stop"):
        scope.gateway.emergency_stop = True

    now_ts = utc_now_iso()
    record = {
        "schema_version": 1,
        "seq": 0,
        "order_id": None,
        "order": None,
        "decision": None,
        "no_action": {
            "rules_hit": [
                {
                    "code": "TIER.EMERGENCY_STOP",
                    "evidence": {
                        "engaged_by": "owner",
                    },
                }
            ],
            "reason": (
                "Emergency stop engaged by the owner; all orders are denied until released."
            ),
        },
        "outcome": "no_action",
        "execution": None,
        "approval": None,
        "tier": tier_view.current(),
        "emergency_stop": True,
        "ips_hash": active_ips_hash,
        "created_at": now_ts,
        "prev_hash": "0" * 64,
        "hash": "0" * 64,
    }

    stored_record = audit_sink.append(record)
    get_validator("decision-record").validate(stored_record)
    return JSONResponse(status_code=200, content=stored_record)


@router.post("/emergency-stop/release")
def release_emergency_stop(request: Request) -> JSONResponse:
    scopes = request.app.state.scopes
    scope = scopes.owner(request)
    if isinstance(scope, JSONResponse):
        return scope
    scopes.require_step_up(request, scope, "release_stop", {})
    tier_view, owner_controls = scope.tier_view, scope.owner_controls
    audit_sink, active_ips_hash = scope.audit_sink, scope.ips_hash

    if not tier_view.is_stopped():
        return make_problem_response(
            status=409,
            problem_type="emergency-stop-state",
            title="Emergency Stop State Conflict",
            detail="Emergency stop is not engaged.",
        )

    owner_controls.release_stop()
    if scope.tenant is None and hasattr(scope.gateway, "emergency_stop"):
        scope.gateway.emergency_stop = False

    now_ts = utc_now_iso()
    record = {
        "schema_version": 1,
        "seq": 0,
        "order_id": None,
        "order": None,
        "decision": None,
        "no_action": {
            "rules_hit": [
                {
                    "code": "TIER.EMERGENCY_STOP_RELEASED",
                    "evidence": {
                        "released_by": "owner",
                    },
                }
            ],
            "reason": "Emergency stop released by the owner; normal operations resumed.",
        },
        "outcome": "no_action",
        "execution": None,
        "approval": None,
        "tier": tier_view.current(),
        "emergency_stop": False,
        "ips_hash": active_ips_hash,
        "created_at": now_ts,
        "prev_hash": "0" * 64,
        "hash": "0" * 64,
    }

    stored_record = audit_sink.append(record)
    get_validator("decision-record").validate(stored_record)
    return JSONResponse(status_code=200, content=stored_record)
