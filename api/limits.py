"""Limits read model and suggested prompts (contract C4, C5 of the webapp v2 contract).

Both routes are read-only and owner-scoped through `current_tenant`. The limits are derived from
the owner's active IPS, the rolling 24 hour spend in the owner's ledger and the stop flag. The
suggestions are chosen by code from the owner's state; no model is called and nothing is signed.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from api.wallet import current_tenant, get_webapp
from core.gateway.tenant import Tenant
from core.policy.ips import _to_plain_data
from core.protocols.addresses import AAVE_V3_A_USDC, AAVE_V3_USDC

log = logging.getLogger("agent_wallet.limits")

router = APIRouter()

ROLLING_WINDOW = timedelta(hours=24)

SUGGESTIONS: dict[str, list[dict[str, str]]] = {
    "stopped": [
        {"text": "Why is the agent stopped?", "kind": "question"},
        {"text": "What can I do while the stop is on?", "kind": "question"},
    ],
    "pending_approval": [
        {"text": "What is waiting for my approval?", "kind": "question"},
        {"text": "Why does this order need my approval?", "kind": "question"},
    ],
    "no_funds": [
        {"text": "How do I fund my agent wallet?", "kind": "question"},
        {"text": "What are my limits?", "kind": "question"},
    ],
    "funded": [
        {"text": "Supply 10 USDC to Aave", "kind": "action"},
        {"text": "Show my portfolio and what you would do", "kind": "question"},
        {"text": "What are my limits?", "kind": "question"},
    ],
    "unknown": [
        {"text": "What are my limits?", "kind": "question"},
        {"text": "What can you do for me?", "kind": "question"},
    ],
}


def _active_ips(tenant: Tenant) -> dict[str, Any]:
    ips = getattr(getattr(tenant.gateway, "policy", None), "ips", None)
    return _to_plain_data(ips) if ips is not None else {}


def _usd(value: Any) -> str:
    return f"{Decimal(str(value)):.2f}"


def limits_view(tenant: Tenant, now: Any) -> dict[str, Any]:
    ips = _active_ips(tenant)
    limits = ips.get("limits") or {}
    approval = ips.get("approval") or {}
    universe = ips.get("universe") or {}
    spent = tenant.stores.ledger.spent_usd_since(now - ROLLING_WINDOW)
    return {
        "per_order_usd": _usd(limits.get("per_tx_usd", "0")),
        "per_day_usd": _usd(limits.get("per_day_usd", "0")),
        "used_today_usd": _usd(spent),
        "approval_threshold_usd": _usd(approval.get("escalate_above_usd", "0")),
        "protocols": list(universe.get("protocols") or []),
        "emergency_stop": "stopped" if tenant.tier_view.is_stopped() else "ready",
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _has_funds(request: Request, tenant: Tenant) -> bool | None:
    """True or False from the chain; None when the RPC cannot be read (callers stay neutral)."""
    wallet = get_webapp(request).wallet
    try:
        return any(
            wallet.token_balance(token, tenant.agent_address) > 0
            for token in (AAVE_V3_USDC, AAVE_V3_A_USDC)
        )
    except Exception:  # noqa: BLE001 - RPC text can carry URLs; never echo it
        log.warning("suggestions balance read failed owner=%s", tenant.owner)
        return None


def suggestions_for(request: Request, tenant: Tenant) -> list[dict[str, str]]:
    if tenant.tier_view.is_stopped():
        return SUGGESTIONS["stopped"]
    if tenant.tier_view.pending_orders():
        return SUGGESTIONS["pending_approval"]
    funded = _has_funds(request, tenant)
    if funded is None:
        return SUGGESTIONS["unknown"]
    return SUGGESTIONS["funded" if funded else "no_funds"]


@router.get("/limits", tags=["state"])
def get_limits(
    request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    return limits_view(tenant, get_webapp(request).wallet.now())


@router.get("/chat/suggestions", tags=["chat"])
def get_chat_suggestions(
    request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    return {"items": suggestions_for(request, tenant)}
