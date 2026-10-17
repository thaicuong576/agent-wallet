"""Order submission and owner approval/rejection routes."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.problems import make_problem_response, utc_now_iso
from api.schemas import UUID_REGEX, get_validator
from core.gateway.exceptions import (
    GatewayBusy,
    GatewayError,
    InvalidOrder,
    OrderIdConflict,
    ReconcileRequired,
)
from core.tiers.errors import (
    EmergencyStopActiveError,
    OrderAlreadyDecidedError,
    OrderNotFoundError,
)

router = APIRouter()


@router.post("/orders")
async def submit_order(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Request",
            detail="Request body must be valid JSON.",
        )

    if not isinstance(body, dict):
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Request",
            detail="Request body must be a JSON object.",
        )

    validator = get_validator("order")
    errors = list(validator.iter_errors(body))
    if errors:
        field_errors = [
            {
                "path": ".".join(str(p) for p in err.absolute_path),
                "message": err.message,
            }
            for err in errors
        ]
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Order",
            detail=f"Order failed schema validation: {errors[0].message}",
            errors=field_errors,
        )

    scopes = request.app.state.scopes
    scope = scopes.single()
    gateway = scope.gateway
    tier_view = scope.tier_view
    owner_controls = scope.owner_controls

    # Sync gateway tier and emergency stop with tier_view
    if hasattr(gateway, "tier") and hasattr(tier_view, "current"):
        gateway.tier = tier_view.current()
    if hasattr(gateway, "emergency_stop") and hasattr(tier_view, "is_stopped"):
        gateway.emergency_stop = tier_view.is_stopped()

    try:
        record = gateway.propose(body)
    except InvalidOrder as e:
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Order",
            detail=str(e),
        )
    except OrderIdConflict as e:
        return make_problem_response(
            status=409,
            problem_type="order-id-conflict",
            title="Order ID Conflict",
            detail=str(e),
        )
    except GatewayError as e:
        return make_problem_response(
            status=409,
            problem_type="invalid-request",
            title="Gateway Error",
            detail=str(e),
        )

    # If order was escalated to pending_approval, register in owner approval queue
    if record.get("outcome") == "pending_approval" and owner_controls is not None:
        try:
            owner_controls.request_approval(body["order_id"])
        except Exception:
            pass

    get_validator("decision-record").validate(record)
    return JSONResponse(status_code=200, content=record)


@router.post("/orders/{order_id}/approve")
def approve_order(order_id: str, request: Request) -> JSONResponse:
    if not UUID_REGEX.match(order_id):
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Order ID",
            detail="order_id must be a lowercase UUID.",
        )

    scopes = request.app.state.scopes
    scope = scopes.owner(request)
    if isinstance(scope, JSONResponse):
        return scope
    scopes.require_step_up(request, scope, "approve", {"order_id": order_id})
    tier_view, owner_controls = scope.tier_view, scope.owner_controls
    gateway, audit_sink = scope.gateway, scope.audit_sink
    active_ips_hash = scope.ips_hash
    scopes.queue_pending(scope, order_id)

    if tier_view.is_stopped():
        return make_problem_response(
            status=409,
            problem_type="emergency-stop-state",
            title="Emergency Stop Active",
            detail="Cannot approve order while emergency stop is active.",
        )

    status = tier_view.approval_status(order_id)
    if status is None:
        return make_problem_response(
            status=404,
            problem_type="not-found",
            title="Order Not Found",
            detail=f"Order '{order_id}' not found in approval queue.",
        )
    # Multi mode: an approval whose execution did not finish (gateway busy, reconcile) left
    # the order approved but still `pending_approval` in the chain. A retry resumes it.
    resume = False
    if scope.tenant is not None and status == "approved":
        latest = audit_sink.find_by_order_id(order_id)
        resume = latest is not None and latest.get("outcome") == "pending_approval"
    if status != "pending" and not resume:
        return make_problem_response(
            status=409,
            problem_type="not-pending",
            title="Order Not Pending",
            detail=f"Order '{order_id}' has already been decided with status '{status}'.",
        )

    # Look up pending order and original decision record
    orig_order: dict[str, Any] | None = None
    orig_decision: dict[str, Any] | None = None
    rec_ips_hash = active_ips_hash

    if hasattr(gateway, "_orders") and order_id in gateway._orders:
        orig_order, prev_rec = gateway._orders[order_id]
        orig_decision = prev_rec.get("decision")
        rec_ips_hash = prev_rec.get("ips_hash", active_ips_hash)
    else:
        for r in reversed(
            list(audit_sink.records) if hasattr(audit_sink, "records") else list(audit_sink)
        ):
            if r.get("order_id") == order_id:
                orig_order = r.get("order")
                orig_decision = r.get("decision")
                rec_ips_hash = r.get("ips_hash", active_ips_hash)
                break

    if orig_order is None:
        return make_problem_response(
            status=404,
            problem_type="not-found",
            title="Order Record Not Found",
            detail=f"Audit record for order '{order_id}' not found.",
        )

    try:
        if not resume:
            owner_controls.approve(order_id)
    except EmergencyStopActiveError as e:
        return make_problem_response(
            status=409,
            problem_type="emergency-stop-state",
            title="Emergency Stop Active",
            detail=str(e),
        )
    except OrderNotFoundError as e:
        return make_problem_response(
            status=404,
            problem_type="not-found",
            title="Order Not Found",
            detail=str(e),
        )
    except OrderAlreadyDecidedError as e:
        return make_problem_response(
            status=409,
            problem_type="not-pending",
            title="Order Already Decided",
            detail=str(e),
        )

    # Execution of approved order
    now_ts = utc_now_iso()
    if scope.tenant is not None:
        # The queue now says approved, so proposing the stored order again lets the
        # gateway run its full decision path (tier, stops, policy, simulate, sign, audit).
        # It is always re-proposed with planner origin: an owner-only function stays denied.
        try:
            stored_record = gateway.propose(orig_order, origin="planner")
        except GatewayBusy:
            return make_problem_response(
                status=409,
                problem_type="job-in-progress",
                title="Gateway Busy",
                detail="The gateway is busy; the approval is recorded, retry shortly.",
            )
        except ReconcileRequired as e:
            return make_problem_response(
                status=409,
                problem_type="invalid-request",
                title="Reconcile Required",
                detail=str(e),
            )
        except (InvalidOrder, GatewayError) as e:
            return make_problem_response(
                status=409,
                problem_type="invalid-request",
                title="Gateway Error",
                detail=str(e),
            )
    elif hasattr(gateway, "execute_approved"):
        stored_record = gateway.execute_approved(order_id)
    elif hasattr(gateway, "approve_and_execute"):
        stored_record = gateway.approve_and_execute(order_id)
    else:
        execution_handler = getattr(request.app.state, "execution_handler", None)
        if execution_handler is not None:
            exec_result = execution_handler(orig_order)
        else:
            exec_result = {
                "schema_version": 1,
                "order_id": order_id,
                "status": "failed",
                "tx_hash": None,
                "chain_id": 84532,
                "block_number": None,
                "gas_used": None,
                "error_code": "SIGNER_UNAVAILABLE",
                "detail": "Signer not configured",
                "executed_at": now_ts,
            }

        exec_status = exec_result.get("status", "failed")
        outcome = "executed" if exec_status == "executed" else "failed"

        new_record = {
            "schema_version": 1,
            "seq": 0,
            "order_id": order_id,
            "order": copy.deepcopy(orig_order),
            "decision": copy.deepcopy(orig_decision),
            "no_action": None,
            "outcome": outcome,
            "execution": exec_result,
            "approval": {
                "status": "approved",
                "by": "owner",
                "at": now_ts,
            },
            "tier": tier_view.current(),
            "emergency_stop": tier_view.is_stopped(),
            "ips_hash": rec_ips_hash,
            "created_at": now_ts,
            "prev_hash": "0" * 64,
            "hash": "0" * 64,
        }
        stored_record = audit_sink.append(new_record)
        if hasattr(gateway, "_orders"):
            gateway._orders[order_id] = (
                copy.deepcopy(orig_order),
                copy.deepcopy(stored_record),
            )

    get_validator("decision-record").validate(stored_record)
    return JSONResponse(status_code=200, content=stored_record)


@router.post("/orders/{order_id}/reject")
def reject_order(order_id: str, request: Request) -> JSONResponse:
    if not UUID_REGEX.match(order_id):
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Order ID",
            detail="order_id must be a lowercase UUID.",
        )

    scopes = request.app.state.scopes
    scope = scopes.owner(request)
    if isinstance(scope, JSONResponse):
        return scope
    tier_view, owner_controls = scope.tier_view, scope.owner_controls
    gateway, audit_sink = scope.gateway, scope.audit_sink
    active_ips_hash = scope.ips_hash
    scopes.queue_pending(scope, order_id)

    status = tier_view.approval_status(order_id)
    if status is None:
        return make_problem_response(
            status=404,
            problem_type="not-found",
            title="Order Not Found",
            detail=f"Order '{order_id}' not found in approval queue.",
        )

    if status != "pending":
        return make_problem_response(
            status=409,
            problem_type="not-pending",
            title="Order Not Pending",
            detail=f"Order '{order_id}' has already been decided with status '{status}'.",
        )

    orig_order: dict[str, Any] | None = None
    rec_ips_hash = active_ips_hash

    if hasattr(gateway, "_orders") and order_id in gateway._orders:
        orig_order, prev_rec = gateway._orders[order_id]
        rec_ips_hash = prev_rec.get("ips_hash", active_ips_hash)
    else:
        for r in reversed(
            list(audit_sink.records) if hasattr(audit_sink, "records") else list(audit_sink)
        ):
            if r.get("order_id") == order_id:
                orig_order = r.get("order")
                rec_ips_hash = r.get("ips_hash", active_ips_hash)
                break

    if orig_order is None:
        return make_problem_response(
            status=404,
            problem_type="not-found",
            title="Order Record Not Found",
            detail=f"Audit record for order '{order_id}' not found.",
        )

    try:
        owner_controls.reject(order_id)
    except OrderNotFoundError as e:
        return make_problem_response(
            status=404,
            problem_type="not-found",
            title="Order Not Found",
            detail=str(e),
        )
    except OrderAlreadyDecidedError as e:
        return make_problem_response(
            status=409,
            problem_type="not-pending",
            title="Order Already Decided",
            detail=str(e),
        )

    now_ts = utc_now_iso()
    rejection_decision = {
        "schema_version": 1,
        "order_id": order_id,
        "decision": "deny",
        "rules_hit": [
            {
                "code": "TIER.APPROVAL_REJECTED",
                "evidence": {
                    "decided_by": "owner",
                },
            }
        ],
        "reason": "Order rejected by owner.",
        "ips_hash": rec_ips_hash,
        "decided_at": now_ts,
    }

    record = {
        "schema_version": 1,
        "seq": 0,
        "order_id": order_id,
        "order": copy.deepcopy(orig_order),
        "decision": rejection_decision,
        "no_action": None,
        "outcome": "denied",
        "execution": None,
        "approval": {
            "status": "rejected",
            "by": "owner",
            "at": now_ts,
        },
        "tier": tier_view.current(),
        "emergency_stop": tier_view.is_stopped(),
        "ips_hash": rec_ips_hash,
        "created_at": now_ts,
        "prev_hash": "0" * 64,
        "hash": "0" * 64,
    }

    stored_record = audit_sink.append(record)
    if hasattr(gateway, "_orders"):
        gateway._orders[order_id] = (
            copy.deepcopy(orig_order),
            copy.deepcopy(stored_record),
        )

    get_validator("decision-record").validate(stored_record)
    return JSONResponse(status_code=200, content=stored_record)
