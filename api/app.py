"""FastAPI application factory for BaKhi Wallet matching contracts/openapi.yaml."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import chat as webapp_chat
from api import jobs as webapp_jobs
from api import limits as webapp_limits
from api import wallet as webapp_wallet
from api.auth import (
    install_auth_handlers,
)
from api.auth import router as auth_router
from api.problems import make_problem_response, utc_now_iso
from api.routes import (
    decisions_router,
    health_router,
    orders_router,
    state_router,
    stop_router,
    tier_router,
)
from api.schemas import EXAMPLES_DIR, get_validator
from api.scope import Scope, Scopes
from core.gateway.audit import AuditSink, InMemoryAuditSink
from core.gateway.gateway import Gateway
from core.policy.ips import compute_ips_hash
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from core.tiers.view import TierView

# Backwards compatibility alias
_Scope = Scope


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
        title="BaKhi Wallet API",
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

    # Scopes container holding captured single/multi tenant dependencies
    scopes = Scopes(
        gateway=gateway,
        tier_view=tier_view,
        owner_controls=owner_controls,
        audit_sink=audit_sink,
        active_ips=active_ips,
        active_ips_hash=active_ips_hash,
        webapp=webapp,
    )
    app.state.scopes = scopes

    # Webapp routes (design 12.2). Always mounted so the contract and the app agree; without
    # `webapp` services every session route answers 503 (fail closed). Existing routes are
    # unchanged and keep the single-owner token mode.
    app.state.webapp = webapp
    app.state.auth_service = webapp.auth if webapp is not None else None
    app.state.default_portfolio = default_portfolio
    app.state.eval_report = eval_report
    app.state.execution_handler = execution_handler

    install_auth_handlers(app)
    app.include_router(auth_router)
    app.include_router(webapp_wallet.router)
    app.include_router(webapp_jobs.router)
    app.include_router(webapp_chat.router)
    app.include_router(webapp_limits.router)

    # Core route modules
    app.include_router(health_router)
    app.include_router(orders_router)
    app.include_router(decisions_router)
    app.include_router(state_router)
    app.include_router(tier_router)
    app.include_router(stop_router)

    return app


__all__ = [
    "Scope",
    "Scopes",
    "_Scope",
    "create_app",
    "get_validator",
    "make_problem_response",
    "utc_now_iso",
]
