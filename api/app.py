"""FastAPI application factory for agent-wallet matching contracts/openapi.yaml."""

from __future__ import annotations

import base64
import copy
import functools
import hmac
import json
import os
import pathlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import jsonschema
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import jobs as webapp_jobs
from api import wallet as webapp_wallet
from api.auth import (
    Session,
    get_service,
    install_auth_handlers,
    require_session,
)
from api.auth import router as auth_router
from api.session import STEP_UP_HEADER
from core.gateway.audit import AuditSink, InMemoryAuditSink
from core.gateway.exceptions import (
    GatewayBusy,
    GatewayError,
    InvalidOrder,
    OrderIdConflict,
    ReconcileRequired,
)
from core.gateway.gateway import Gateway
from core.policy.ips import _to_plain_data, compute_ips_hash
from core.tiers.errors import (
    EmergencyStopActiveError,
    InvalidTierError,
    OrderAlreadyDecidedError,
    OrderNotFoundError,
)
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from core.tiers.view import TierView

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT_DIR / "contracts" / "schemas"
EXAMPLES_DIR = ROOT_DIR / "contracts" / "examples"

UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

PROBLEM_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["type", "title", "status"],
    "additionalProperties": True,
    "properties": {
        "type": {"type": "string", "pattern": r"^urn:agent-wallet:problem:[a-z-]+$"},
        "title": {"type": "string"},
        "status": {"type": "integer", "minimum": 400, "maximum": 599},
        "detail": {"type": "string", "maxLength": 500},
        "instance": {"type": "string"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "message"],
                "properties": {
                    "path": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
    },
}

DECISIONS_RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "items", "next_cursor"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "items": {"type": "array"},
        "next_cursor": {"type": ["string", "null"]},
    },
}

IPS_RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "ips", "ips_hash"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "ips": {"type": "object"},
        "ips_hash": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    },
}

EVAL_REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "generated_at", "metrics", "scenarios"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "generated_at": {
            "type": "string",
            "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$",
        },
        "metrics": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        },
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "layer", "passed"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": r"^(AM|SEC|CMP)-[0-9]{2}$"},
                    "layer": {"type": "string", "enum": ["A", "B", "C"]},
                    "passed": {"type": "boolean"},
                    "rule_codes": {
                        "type": "array",
                        "items": {"type": "string", "pattern": r"^[A-Z]+\.[A-Z0-9_]+$"},
                    },
                    "tx_hash": {
                        "type": ["string", "null"],
                        "pattern": r"^0x[0-9a-f]{64}$",
                    },
                },
            },
        },
    },
}


@functools.lru_cache(maxsize=16)
def get_validator(schema_name: str) -> jsonschema.Draft202012Validator:
    """Load and cache Draft 2020-12 schema validator."""
    if schema_name == "problem":
        return jsonschema.Draft202012Validator(PROBLEM_SCHEMA)
    if schema_name == "decisions-response":
        return jsonschema.Draft202012Validator(DECISIONS_RESPONSE_SCHEMA)
    if schema_name == "ips-response":
        return jsonschema.Draft202012Validator(IPS_RESPONSE_SCHEMA)
    if schema_name == "eval-report":
        return jsonschema.Draft202012Validator(EVAL_REPORT_SCHEMA)

    schema_path = SCHEMAS_DIR / f"{schema_name}.schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(data)


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


def _check_owner_auth(request: Request) -> JSONResponse | None:
    """Verify owner token from environment AGENT_WALLET_OWNER_TOKEN.

    Fails closed with 503 if token is unset.
    Returns 401 if token is missing or invalid.
    """
    expected_token = os.environ.get("AGENT_WALLET_OWNER_TOKEN")
    if not expected_token:
        return make_problem_response(
            status=503,
            problem_type="service-unavailable",
            title="Service Unavailable",
            detail=(
                "Owner controls are disabled because AGENT_WALLET_OWNER_TOKEN is not configured."
            ),
        )

    auth = request.headers.get("authorization", "").strip()
    token = ""
    if auth:
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        else:
            token = auth
    if not token:
        token = request.headers.get("x-owner-token", "").strip()

    if not token or not hmac.compare_digest(token, expected_token):
        return make_problem_response(
            status=401,
            problem_type="unauthorized",
            title="Unauthorized",
            detail="Missing or invalid owner token.",
        )
    return None


TIER_RANK = {"T0": 0, "T1": 1, "T2": 2}


@dataclass
class _Scope:
    """What an owner route acts on: the one global gateway (single mode) or the session tenant."""

    tier_view: Any
    owner_controls: Any
    gateway: Any
    audit_sink: Any
    ips: dict[str, Any]
    ips_hash: str
    tenant: webapp_wallet.Tenant | None = None
    session: Session | None = None


def create_app(
    gateway: Gateway | None = None,
    tier_view: TierView | None = None,
    owner_controls: OwnerControls | None = None,
    audit_sink: AuditSink | None = None,
    ips: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    eval_report: dict[str, Any] | None = None,
    execution_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    webapp: webapp_wallet.WebappServices | None = None,
) -> FastAPI:
    """FastAPI application factory implementing every path of contracts/openapi.yaml."""
    # Build default in-memory deny-all T0 components if omitted
    if tier_view is None or owner_controls is None:
        store = TierStore(":memory:")
        if tier_view is None:
            tier_view = TierView(store)
        if owner_controls is None:
            owner_controls = OwnerControls(store)

    if audit_sink is None:
        if gateway is not None and getattr(gateway, "audit_sink", None) is not None:
            audit_sink = gateway.audit_sink
        else:
            audit_sink = InMemoryAuditSink()

    if gateway is None:
        gateway = Gateway(tier="T0", emergency_stop=False, audit_sink=audit_sink)

    # Active IPS snapshot
    active_ips: dict[str, Any]
    if ips is not None:
        active_ips = copy.deepcopy(ips)
    else:
        ips_file = EXAMPLES_DIR / "ips.valid.json"
        if ips_file.exists():
            active_ips = json.loads(ips_file.read_text(encoding="utf-8"))
        else:
            active_ips = {"schema_version": 1}
    active_ips_hash = getattr(gateway, "ips_hash", None) or compute_ips_hash(active_ips)

    # Default portfolio snapshot
    default_portfolio: dict[str, Any]
    if portfolio is not None:
        default_portfolio = copy.deepcopy(portfolio)
    else:
        pf_file = EXAMPLES_DIR / "portfolio-state.valid.json"
        if pf_file.exists():
            default_portfolio = json.loads(pf_file.read_text(encoding="utf-8"))
        else:
            default_portfolio = {
                "schema_version": 1,
                "as_of": utc_now_iso(),
                "price_source": "mainnet_reference",
                "positions": [],
                "total_value_usd": "0.00",
                "spent_24h_usd": "0.00",
                "tx_count_1h": 0,
            }

    app = FastAPI(
        title="agent-wallet API",
        version="1.0.0",
        summary="The single contract between the minimal UI, the eval harness and the backend.",
    )

    # Register RFC 7807 exception handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for err in exc.errors():
            loc = err.get("loc", ())
            clean_loc = [str(x) for x in loc if x not in ("body", "query", "path")]
            path = ".".join(clean_loc) or ".".join(str(x) for x in loc)
            errors.append({"path": path, "message": err.get("msg", "")})
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Request",
            detail="Request validation failed.",
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            prob = "not-found"
            title = "Not Found"
        elif exc.status_code == 409:
            prob = "order-id-conflict"
            title = "Conflict"
        elif exc.status_code == 422:
            prob = "invalid-request"
            title = "Invalid Request"
        elif exc.status_code == 401:
            prob = "unauthorized"
            title = "Unauthorized"
        elif exc.status_code == 503:
            prob = "service-unavailable"
            title = "Service Unavailable"
        else:
            prob = "error"
            title = "Error"
        return make_problem_response(
            status=exc.status_code,
            problem_type=prob,
            title=title,
            detail=str(exc.detail) if exc.detail else title,
        )

    # Webapp routes (design 12.2). Always mounted so the contract and the app agree; without
    # `webapp` services every session route answers 503 (fail closed). Existing routes are
    # unchanged and keep the single-owner token mode.
    app.state.webapp = webapp
    app.state.auth_service = webapp.auth if webapp is not None else None
    install_auth_handlers(app)
    app.include_router(auth_router)
    app.include_router(webapp_wallet.router)
    app.include_router(webapp_jobs.router)

    def _single_scope() -> _Scope:
        return _Scope(tier_view, owner_controls, gateway, audit_sink, active_ips, active_ips_hash)

    def _session_scope(request: Request) -> _Scope:
        """Multi mode: the verified session cookie names the tenant; nothing else can."""
        session = require_session(request)
        tenant = webapp_wallet.current_tenant(request, session)
        gw = tenant.gateway
        policy_ips = getattr(getattr(gw, "policy", None), "ips", None)
        ips_doc = _to_plain_data(policy_ips) if policy_ips is not None else {"schema_version": 1}
        return _Scope(
            tier_view=tenant.stores.tier_read,
            owner_controls=tenant.stores.tier_write,
            gateway=gw,
            audit_sink=gw.audit_sink,
            ips=ips_doc,
            ips_hash=getattr(gw, "ips_hash", None) or compute_ips_hash(ips_doc),
            tenant=tenant,
            session=session,
        )

    def _read_scope(request: Request) -> _Scope:
        """Reads: the session in multi mode, no credential in single mode (as before)."""
        return _session_scope(request) if webapp is not None else _single_scope()

    def _owner_scope(request: Request) -> _Scope | JSONResponse:
        """Owner actions: the session in multi mode, the shared owner token in single mode."""
        if webapp is not None:
            return _session_scope(request)
        auth_err = _check_owner_auth(request)
        if auth_err is not None:
            return auth_err
        return _single_scope()

    def _require_step_up(
        request: Request, scope: _Scope, action: str, params: dict[str, Any]
    ) -> None:
        """Multi mode: a fresh signature bound to `action` and `params` (design 6.2)."""
        if scope.session is None:
            return
        service = get_service(request)
        service.check_step_up(
            scope.session, request.headers.get(STEP_UP_HEADER), action, params, service.clock()
        )

    def _queue_pending(scope: _Scope, order_id: str) -> None:
        """Multi mode: the tenant gateway has no approval callback, so an order the gateway left
        `pending_approval` is queued the first time its owner looks at it."""
        if scope.tenant is None or scope.tier_view.approval_status(order_id) is not None:
            return
        found = scope.audit_sink.find_by_order_id(order_id)
        if found is not None and found.get("outcome") == "pending_approval":
            try:
                scope.owner_controls.request_approval(order_id)
            except Exception:  # noqa: BLE001 - a concurrent enqueue is fine
                pass

    def _tier_state(scope: _Scope) -> dict[str, Any]:
        view = scope.tier_view
        if hasattr(view, "get_tier_state"):
            return view.get_tier_state()
        if hasattr(view, "_store") and hasattr(view._store, "get_tier_state"):
            return view._store.get_tier_state()
        return {
            "schema_version": 1,
            "tier": view.current(),
            "emergency_stop": view.is_stopped(),
            "updated_at": utc_now_iso(),
            "changed_by": "owner",
        }

    # Unauthenticated health endpoint
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # 1. Agent-facing POST /orders
    @app.post("/orders")
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

    # 2. Owner POST /orders/{order_id}/approve
    @app.post("/orders/{order_id}/approve")
    def approve_order(order_id: str, request: Request) -> JSONResponse:
        if not UUID_REGEX.match(order_id):
            return make_problem_response(
                status=422,
                problem_type="invalid-request",
                title="Invalid Order ID",
                detail="order_id must be a lowercase UUID.",
            )

        scope = _owner_scope(request)
        if isinstance(scope, JSONResponse):
            return scope
        _require_step_up(request, scope, "approve", {"order_id": order_id})
        tier_view, owner_controls = scope.tier_view, scope.owner_controls
        gateway, audit_sink = scope.gateway, scope.audit_sink
        active_ips_hash = scope.ips_hash
        _queue_pending(scope, order_id)

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

    # 3. Owner POST /orders/{order_id}/reject
    @app.post("/orders/{order_id}/reject")
    def reject_order(order_id: str, request: Request) -> JSONResponse:
        if not UUID_REGEX.match(order_id):
            return make_problem_response(
                status=422,
                problem_type="invalid-request",
                title="Invalid Order ID",
                detail="order_id must be a lowercase UUID.",
            )

        scope = _owner_scope(request)
        if isinstance(scope, JSONResponse):
            return scope
        tier_view, owner_controls = scope.tier_view, scope.owner_controls
        gateway, audit_sink = scope.gateway, scope.audit_sink
        active_ips_hash = scope.ips_hash
        _queue_pending(scope, order_id)

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

    # 4. GET /decisions
    @app.get("/decisions")
    def list_decisions(
        order_id: str | None = None,
        outcome: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        *,
        request: Request,
    ) -> JSONResponse:
        audit_sink = _read_scope(request).audit_sink
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

        raw_records = (
            list(audit_sink.records) if hasattr(audit_sink, "records") else list(audit_sink)
        )
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

    # 5. GET /portfolio
    @app.get("/portfolio")
    def get_portfolio(request: Request) -> JSONResponse:
        scope = _read_scope(request)
        if scope.tenant is not None:
            try:
                data = webapp.wallet.portfolio_state(scope.tenant)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001 - RPC text can carry URLs; never echo it
                return make_problem_response(
                    status=503,
                    problem_type="service-unavailable",
                    title="Chain RPC unavailable",
                    detail="Could not read the portfolio; try again shortly.",
                )
        else:
            data = copy.deepcopy(default_portfolio)
        get_validator("portfolio-state").validate(data)
        return JSONResponse(status_code=200, content=data)

    # 6. GET /ips
    @app.get("/ips")
    def get_ips(request: Request) -> JSONResponse:
        scope = _read_scope(request)
        body = {
            "schema_version": 1,
            "ips": copy.deepcopy(scope.ips),
            "ips_hash": scope.ips_hash,
        }
        get_validator("ips-response").validate(body)
        return JSONResponse(status_code=200, content=body)

    # 7. GET /tier
    @app.get("/tier")
    def get_tier(request: Request) -> JSONResponse:
        state = _tier_state(_read_scope(request))
        get_validator("tier-state").validate(state)
        return JSONResponse(status_code=200, content=state)

    # 8. Owner PUT /tier
    @app.put("/tier")
    async def set_tier(request: Request) -> JSONResponse:
        scope = _owner_scope(request)
        if isinstance(scope, JSONResponse):
            return scope

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

        if (
            body.get("schema_version") != 1
            or body.get("tier") not in ("T0", "T1", "T2")
            or len(body) != 2
        ):
            return make_problem_response(
                status=422,
                problem_type="invalid-request",
                title="Invalid Tier Request",
                detail="Body must strictly contain schema_version=1 and tier in [T0, T1, T2].",
            )

        tier_val = str(body["tier"])
        if scope.tenant is not None and TIER_RANK[tier_val] > TIER_RANK.get(
            scope.tier_view.current(), 0
        ):
            # Raising widens authority, so it needs a fresh signature bound to the new tier.
            _require_step_up(request, scope, "tier_raise", {"tier": tier_val})
        try:
            scope.owner_controls.set_tier(tier_val)
        except InvalidTierError as e:
            return make_problem_response(
                status=422,
                problem_type="invalid-request",
                title="Invalid Tier",
                detail=str(e),
            )

        if scope.tenant is None and hasattr(gateway, "tier"):
            gateway.tier = tier_val

        new_state = _tier_state(scope)

        get_validator("tier-state").validate(new_state)
        return JSONResponse(status_code=200, content=new_state)

    # 9. Owner POST /emergency-stop
    @app.post("/emergency-stop")
    def engage_emergency_stop(request: Request) -> JSONResponse:
        scope = _owner_scope(request)  # fail-safe direction: the session alone is enough
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
        if scope.tenant is None and hasattr(gateway, "emergency_stop"):
            gateway.emergency_stop = True

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

    # 10. Owner POST /emergency-stop/release
    @app.post("/emergency-stop/release")
    def release_emergency_stop(request: Request) -> JSONResponse:
        scope = _owner_scope(request)
        if isinstance(scope, JSONResponse):
            return scope
        _require_step_up(request, scope, "release_stop", {})
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
        if scope.tenant is None and hasattr(gateway, "emergency_stop"):
            gateway.emergency_stop = False

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

    # 11. GET /eval/report
    @app.get("/eval/report")
    def get_eval_report() -> JSONResponse:
        report = eval_report
        if report is None:
            report_path = ROOT_DIR / "reports" / "eval_report.json"
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception:
                    report = None

        if report is None:
            return make_problem_response(
                status=404,
                problem_type="not-found",
                title="Eval Report Not Found",
                detail="No eval report has been generated yet.",
            )

        get_validator("eval-report").validate(report)
        return JSONResponse(status_code=200, content=report)

    return app
