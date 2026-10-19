"""Scope encapsulation for single-mode and session-mode tenants."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from api import wallet as webapp_wallet
from api.auth import Session, get_service, require_session
from api.problems import make_problem_response, utc_now_iso
from api.session import STEP_UP_HEADER
from core.policy.ips import _to_plain_data, compute_ips_hash


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


@dataclass
class Scope:
    """What an owner route acts on: the one global gateway (single mode) or the session tenant."""

    tier_view: Any
    owner_controls: Any
    gateway: Any
    audit_sink: Any
    ips: dict[str, Any]
    ips_hash: str
    tenant: webapp_wallet.Tenant | None = None
    session: Session | None = None


class Scopes:
    """Holds captured services and resolves scopes for single/multi tenant requests."""

    def __init__(
        self,
        gateway: Any,
        tier_view: Any,
        owner_controls: Any,
        audit_sink: Any,
        active_ips: dict[str, Any],
        active_ips_hash: str,
        webapp: webapp_wallet.WebappServices | None = None,
    ) -> None:
        self.gateway = gateway
        self.tier_view = tier_view
        self.owner_controls = owner_controls
        self.audit_sink = audit_sink
        self.active_ips = active_ips
        self.active_ips_hash = active_ips_hash
        self.webapp = webapp

    def single(self) -> Scope:
        return Scope(
            tier_view=self.tier_view,
            owner_controls=self.owner_controls,
            gateway=self.gateway,
            audit_sink=self.audit_sink,
            ips=self.active_ips,
            ips_hash=self.active_ips_hash,
        )

    def session(self, request: Request) -> Scope:
        """Multi mode: the verified session cookie names the tenant; nothing else can."""
        session = require_session(request)
        tenant = webapp_wallet.current_tenant(request, session)
        gw = tenant.gateway
        policy_ips = getattr(getattr(gw, "policy", None), "ips", None)
        ips_doc = _to_plain_data(policy_ips) if policy_ips is not None else {"schema_version": 1}
        return Scope(
            tier_view=tenant.stores.tier_read,
            owner_controls=tenant.stores.tier_write,
            gateway=gw,
            audit_sink=gw.audit_sink,
            ips=ips_doc,
            ips_hash=getattr(gw, "ips_hash", None) or compute_ips_hash(ips_doc),
            tenant=tenant,
            session=session,
        )

    def read(self, request: Request) -> Scope:
        """Reads: the session in multi mode, no credential in single mode (as before)."""
        return self.session(request) if self.webapp is not None else self.single()

    def owner(self, request: Request) -> Scope | JSONResponse:
        """Owner actions: the session in multi mode, the shared owner token in single mode."""
        if self.webapp is not None:
            return self.session(request)
        auth_err = _check_owner_auth(request)
        if auth_err is not None:
            return auth_err
        return self.single()

    def require_step_up(
        self, request: Request, scope: Scope, action: str, params: dict[str, Any]
    ) -> None:
        """Multi mode: a fresh signature bound to `action` and `params`."""
        if scope.session is None:
            return
        service = get_service(request)
        service.check_step_up(
            scope.session, request.headers.get(STEP_UP_HEADER), action, params, service.clock()
        )

    def queue_pending(self, scope: Scope, order_id: str) -> None:
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

    def tier_state(self, scope: Scope) -> dict[str, Any]:
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


__all__ = ["Scope", "Scopes", "_check_owner_auth"]
