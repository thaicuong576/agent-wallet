"""State inspection routes: portfolio, IPS, and tier."""

from __future__ import annotations

import copy

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.problems import make_problem_response
from api.schemas import get_validator

router = APIRouter()


@router.get("/portfolio")
def get_portfolio(request: Request) -> JSONResponse:
    scope = request.app.state.scopes.read(request)
    if scope.tenant is not None:
        try:
            webapp = request.app.state.webapp
            data = webapp.wallet.portfolio_state(scope.tenant)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - RPC text can carry URLs; never echo it
            return make_problem_response(
                status=503,
                problem_type="service-unavailable",
                title="Chain RPC unavailable",
                detail="Could not read the portfolio; try again shortly.",
            )
    else:
        data = copy.deepcopy(request.app.state.default_portfolio)
    get_validator("portfolio-state").validate(data)
    return JSONResponse(status_code=200, content=data)


@router.get("/ips")
def get_ips(request: Request) -> JSONResponse:
    scope = request.app.state.scopes.read(request)
    body = {
        "schema_version": 1,
        "ips": copy.deepcopy(scope.ips),
        "ips_hash": scope.ips_hash,
    }
    get_validator("ips-response").validate(body)
    return JSONResponse(status_code=200, content=body)


@router.get("/tier")
def get_tier(request: Request) -> JSONResponse:
    scopes = request.app.state.scopes
    state = scopes.tier_state(scopes.read(request))
    get_validator("tier-state").validate(state)
    return JSONResponse(status_code=200, content=state)
