"""Owner tier modification routes."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api import wallet as webapp_wallet
from api.problems import make_problem_response
from api.schemas import get_validator
from api.session import STEP_UP_HEADER
from core.policy.ips import compute_ips_hash
from core.tiers.errors import InvalidTierError

router = APIRouter()

TIER_RANK = {"T0": 0, "T1": 1, "T2": 2}


@router.put("/tier")
async def set_tier(request: Request) -> JSONResponse:
    scopes = request.app.state.scopes
    scope = scopes.owner(request)
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

    preset_id = body.get("preset_id")
    allowed_keys = (
        {"schema_version", "tier"} if preset_id is None else {"schema_version", "tier", "preset_id"}
    )
    if (
        body.get("schema_version") != 1
        or body.get("tier") not in ("T0", "T1", "T2")
        or set(body.keys()) - allowed_keys
    ):
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Tier Request",
            detail=(
                "Body must strictly contain schema_version=1, tier in [T0, T1, T2], "
                "and optional preset_id."
            ),
        )

    if preset_id is not None:
        valid_preset_ids = {p["preset_id"] for p in webapp_wallet.list_presets()}
        if preset_id not in valid_preset_ids:
            return make_problem_response(
                status=422,
                problem_type="invalid-request",
                title="Unknown Preset",
                detail=(
                    f"Preset '{preset_id}' is not recognized. "
                    f"Valid presets: {sorted(valid_preset_ids)}"
                ),
            )

    tier_val = str(body["tier"])
    if scope.tenant is not None and preset_id is not None:
        # Choosing a preset changes the universe the agent may act in: the owner's fresh
        # signature must be bound to the tier and to the exact preset chosen.
        scopes.require_step_up(
            request, scope, "tier_raise", {"tier": tier_val, "preset_id": str(preset_id)}
        )
    elif scope.tenant is not None and TIER_RANK[tier_val] > TIER_RANK.get(
        scope.tier_view.current(), 0
    ):
        # Raising widens authority, so it needs a fresh signature bound to the new tier.
        scopes.require_step_up(request, scope, "tier_raise", {"tier": tier_val})
    try:
        scope.owner_controls.set_tier(tier_val)
    except InvalidTierError as e:
        return make_problem_response(
            status=422,
            problem_type="invalid-request",
            title="Invalid Tier",
            detail=str(e),
        )

    if scope.tenant is not None:
        if preset_id is not None:
            new_hash = scope.tenant.reload_policy(preset_id)
            if (
                hasattr(scope.tenant.stores, "policy_selection")
                and scope.tenant.stores.policy_selection is not None
            ):
                step_up_hdr = request.headers.get(STEP_UP_HEADER) or ""
                sig_hash = (
                    hashlib.sha256(step_up_hdr.encode("utf-8")).hexdigest() if step_up_hdr else ""
                )
                scope.tenant.stores.policy_selection.append(preset_id, new_hash, sig_hash)
        elif (
            hasattr(scope.tenant.stores, "policy_selection")
            and scope.tenant.stores.policy_selection is not None
            and scope.tenant.stores.policy_selection.latest() is None
        ):
            step_up_hdr = request.headers.get(STEP_UP_HEADER) or ""
            sig_hash = (
                hashlib.sha256(step_up_hdr.encode("utf-8")).hexdigest() if step_up_hdr else ""
            )
            scope.tenant.stores.policy_selection.append(
                "conservative", scope.tenant.gateway.ips_hash, sig_hash
            )
    else:
        if hasattr(scope.gateway, "tier"):
            scope.gateway.tier = tier_val
        if preset_id is not None:
            preset_file = webapp_wallet.PRESETS_DIR / f"{preset_id}.json"
            if preset_file.exists():
                p_ips = json.loads(preset_file.read_text(encoding="utf-8"))
                active_ips_hash = compute_ips_hash(p_ips)
                scope.ips = p_ips
                scope.ips_hash = active_ips_hash
                scopes.active_ips = p_ips
                scopes.active_ips_hash = active_ips_hash
                if hasattr(scope.gateway, "ips_hash"):
                    scope.gateway.ips_hash = active_ips_hash

    new_state = scopes.tier_state(scope)

    get_validator("tier-state").validate(new_state)
    return JSONResponse(status_code=200, content=new_state)
