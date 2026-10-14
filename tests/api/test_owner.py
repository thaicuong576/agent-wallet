"""Tests for owner endpoints, token authentication, and fail-closed security."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.gateway import Gateway, InMemoryAuditSink
from core.tiers import OwnerControls, TierStore, TierView

DUMMY_UUID = "12345678-1234-1234-1234-123456789abc"


class MockEscalatePolicy:
    def __init__(
        self,
        ips_hash: str = "e5bf9cf5648f7b23af6583f67c1f303c83475f19578290c3efe35d2d4e6397d1",
    ) -> None:
        self.ips_hash = ips_hash

    def evaluate(self, order: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "order_id": order.get("order_id"),
            "decision": "escalate",
            "rules_hit": [
                {
                    "code": "TIER.APPROVAL_REQUIRED",
                    "evidence": {"order_usd": "100.00"},
                }
            ],
            "reason": "Escalated for approval",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T10:00:00Z",
        }


def _setup_app_with_pending_order(
    valid_order_payload: dict[str, Any],
) -> tuple[TestClient, TierView, OwnerControls, InMemoryAuditSink, str]:
    store = TierStore(":memory:")
    view = TierView(store)
    owner = OwnerControls(store)
    owner.set_tier("T2")
    audit = InMemoryAuditSink()
    gw = Gateway(
        policy=MockEscalatePolicy(),
        tier="T2",
        emergency_stop=False,
        audit_sink=audit,
    )

    def mock_executor(order: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "status": "executed",
            "tx_hash": "0x" + "a" * 64,
            "chain_id": 84532,
            "block_number": 123456,
            "gas_used": 21000,
            "error_code": None,
            "detail": None,
            "executed_at": "2026-10-16T10:05:00Z",
        }

    app = create_app(
        gateway=gw,
        tier_view=view,
        owner_controls=owner,
        audit_sink=audit,
        execution_handler=mock_executor,
    )
    client = TestClient(app)

    # Post order to put it into pending status
    order_id = valid_order_payload["order_id"]
    post_resp = client.post("/orders", json=valid_order_payload)
    assert post_resp.status_code == 200
    assert post_resp.json()["outcome"] == "pending_approval"
    return client, view, owner, audit, order_id


def test_owner_endpoints_reject_missing_token(client: TestClient) -> None:
    """Owner endpoints reject requests missing an owner token with 401 Unauthorized Problem."""
    endpoints = [
        ("POST", f"/orders/{DUMMY_UUID}/approve", None),
        ("POST", f"/orders/{DUMMY_UUID}/reject", None),
        ("POST", "/emergency-stop", None),
        ("POST", "/emergency-stop/release", None),
        ("PUT", "/tier", {"schema_version": 1, "tier": "T1"}),
    ]
    for method, path, payload in endpoints:
        if method == "POST":
            resp = client.post(path, json=payload)
        else:
            resp = client.put(path, json=payload)
        assert resp.status_code == 401, f"{path} did not reject missing token"
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:agent-wallet:problem:unauthorized"


def test_owner_endpoints_reject_wrong_token(client: TestClient) -> None:
    """Owner endpoints reject requests with invalid token with 401 Unauthorized Problem."""
    bad_headers = {"Authorization": "Bearer totally-wrong-token-value"}
    endpoints = [
        ("POST", f"/orders/{DUMMY_UUID}/approve", None),
        ("POST", f"/orders/{DUMMY_UUID}/reject", None),
        ("POST", "/emergency-stop", None),
        ("POST", "/emergency-stop/release", None),
        ("PUT", "/tier", {"schema_version": 1, "tier": "T1"}),
    ]
    for method, path, payload in endpoints:
        if method == "POST":
            resp = client.post(path, json=payload, headers=bad_headers)
        else:
            resp = client.put(path, json=payload, headers=bad_headers)
        assert resp.status_code == 401, f"{path} did not reject wrong token"
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:agent-wallet:problem:unauthorized"


def test_owner_endpoints_fail_closed_when_token_unset(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """Owner endpoints fail closed returning 503 Problem when AGENT_WALLET_OWNER_TOKEN is unset."""
    monkeypatch.delenv("AGENT_WALLET_OWNER_TOKEN", raising=False)
    endpoints = [
        ("POST", f"/orders/{DUMMY_UUID}/approve", None),
        ("POST", f"/orders/{DUMMY_UUID}/reject", None),
        ("POST", "/emergency-stop", None),
        ("POST", "/emergency-stop/release", None),
        ("PUT", "/tier", {"schema_version": 1, "tier": "T1"}),
    ]
    headers = {"Authorization": "Bearer any-token"}
    for method, path, payload in endpoints:
        if method == "POST":
            resp = client.post(path, json=payload, headers=headers)
        else:
            resp = client.put(path, json=payload, headers=headers)
        assert resp.status_code == 503, f"{path} did not fail closed with 503"
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:agent-wallet:problem:service-unavailable"


def test_agent_facing_cannot_reach_approve_or_stop(client: TestClient) -> None:
    """An agent-facing caller without the owner token cannot invoke approve or stop."""
    # Attempting to engage emergency stop without token
    resp = client.post("/emergency-stop")
    assert resp.status_code == 401
    assert resp.json()["type"] == "urn:agent-wallet:problem:unauthorized"

    # Attempting to approve an order without token
    resp = client.post(f"/orders/{DUMMY_UUID}/approve")
    assert resp.status_code == 401
    assert resp.json()["type"] == "urn:agent-wallet:problem:unauthorized"


def test_approve_order_happy_executed(
    configured_env: None,
    auth_headers: dict[str, str],
    valid_order_payload: dict[str, Any],
) -> None:
    """POST /orders/{id}/approve happy path: pending order is approved and executed."""
    client, view, owner, audit, order_id = _setup_app_with_pending_order(valid_order_payload)

    resp = client.post(f"/orders/{order_id}/approve", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["order_id"] == order_id
    assert data["outcome"] == "executed"
    assert data["approval"]["status"] == "approved"
    assert data["approval"]["by"] == "owner"
    assert data["execution"]["status"] == "executed"
    assert data["execution"]["tx_hash"].startswith("0x")

    assert view.approval_status(order_id) == "approved"


def test_approve_order_error_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    """POST /orders/{id}/approve error path: unknown order returns 404 Problem."""
    unknown_id = str(uuid.uuid4())
    resp = client.post(f"/orders/{unknown_id}/approve", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "urn:agent-wallet:problem:not-found"


def test_approve_order_error_invalid_uuid(client: TestClient, auth_headers: dict[str, str]) -> None:
    """POST /orders/{id}/approve error path: invalid UUID returns 422 Problem."""
    resp = client.post("/orders/invalid-uuid/approve", headers=auth_headers)
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "urn:agent-wallet:problem:invalid-request"


def test_approve_order_error_not_pending(
    configured_env: None,
    auth_headers: dict[str, str],
    valid_order_payload: dict[str, Any],
) -> None:
    """POST /orders/{id}/approve error path: approving twice returns 409 Problem."""
    client, view, owner, audit, order_id = _setup_app_with_pending_order(valid_order_payload)

    resp1 = client.post(f"/orders/{order_id}/approve", headers=auth_headers)
    assert resp1.status_code == 200

    resp2 = client.post(f"/orders/{order_id}/approve", headers=auth_headers)
    assert resp2.status_code == 409
    assert resp2.headers["content-type"] == "application/problem+json"
    assert resp2.json()["type"] == "urn:agent-wallet:problem:not-pending"


def test_approve_order_error_when_stop_engaged(
    configured_env: None,
    auth_headers: dict[str, str],
    valid_order_payload: dict[str, Any],
) -> None:
    """POST /orders/{id}/approve error path: approving when stop is active returns 409 Problem."""
    client, view, owner, audit, order_id = _setup_app_with_pending_order(valid_order_payload)

    # Engage stop
    stop_resp = client.post("/emergency-stop", headers=auth_headers)
    assert stop_resp.status_code == 200

    # Attempt approve
    appr_resp = client.post(f"/orders/{order_id}/approve", headers=auth_headers)
    assert appr_resp.status_code == 409
    assert appr_resp.headers["content-type"] == "application/problem+json"
    assert appr_resp.json()["type"] == "urn:agent-wallet:problem:emergency-stop-state"


def test_reject_order_happy(
    configured_env: None,
    auth_headers: dict[str, str],
    valid_order_payload: dict[str, Any],
) -> None:
    """POST /orders/{id}/reject happy path: pending order is rejected and appended as denied."""
    client, view, owner, audit, order_id = _setup_app_with_pending_order(valid_order_payload)

    resp = client.post(f"/orders/{order_id}/reject", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["order_id"] == order_id
    assert data["outcome"] == "denied"
    assert data["decision"]["decision"] == "deny"
    assert data["decision"]["rules_hit"][0]["code"] == "TIER.APPROVAL_REJECTED"
    assert data["approval"]["status"] == "rejected"
    assert data["execution"] is None

    assert view.approval_status(order_id) == "rejected"


def test_reject_order_error_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    """POST /orders/{id}/reject error path: unknown order returns 404 Problem."""
    unknown_id = str(uuid.uuid4())
    resp = client.post(f"/orders/{unknown_id}/reject", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "urn:agent-wallet:problem:not-found"


def test_reject_order_error_not_pending(
    configured_env: None,
    auth_headers: dict[str, str],
    valid_order_payload: dict[str, Any],
) -> None:
    """POST /orders/{id}/reject error path: rejecting twice returns 409 Problem."""
    client, view, owner, audit, order_id = _setup_app_with_pending_order(valid_order_payload)

    resp1 = client.post(f"/orders/{order_id}/reject", headers=auth_headers)
    assert resp1.status_code == 200

    resp2 = client.post(f"/orders/{order_id}/reject", headers=auth_headers)
    assert resp2.status_code == 409
    assert resp2.headers["content-type"] == "application/problem+json"
    assert resp2.json()["type"] == "urn:agent-wallet:problem:not-pending"


def test_emergency_stop_lifecycle_and_conflict(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /emergency-stop engages stop, duplicate returns 409; release restores it."""
    # 1. Engage stop happy path
    stop_resp = client.post("/emergency-stop", headers=auth_headers)
    assert stop_resp.status_code == 200
    stop_data = stop_resp.json()
    assert stop_data["outcome"] == "no_action"
    assert stop_data["no_action"]["rules_hit"][0]["code"] == "TIER.EMERGENCY_STOP"
    assert stop_data["emergency_stop"] is True

    # 2. Engage again -> 409 Problem
    stop_conflict = client.post("/emergency-stop", headers=auth_headers)
    assert stop_conflict.status_code == 409
    assert stop_conflict.headers["content-type"] == "application/problem+json"
    assert stop_conflict.json()["type"] == "urn:agent-wallet:problem:emergency-stop-state"

    # 3. Release stop happy path
    rel_resp = client.post("/emergency-stop/release", headers=auth_headers)
    assert rel_resp.status_code == 200
    rel_data = rel_resp.json()
    assert rel_data["outcome"] == "no_action"
    assert rel_data["no_action"]["rules_hit"][0]["code"] == "TIER.EMERGENCY_STOP_RELEASED"
    assert rel_data["emergency_stop"] is False

    # 4. Release again -> 409 Problem
    rel_conflict = client.post("/emergency-stop/release", headers=auth_headers)
    assert rel_conflict.status_code == 409
    assert rel_conflict.headers["content-type"] == "application/problem+json"
    assert rel_conflict.json()["type"] == "urn:agent-wallet:problem:emergency-stop-state"


def test_set_tier_happy_and_errors(client: TestClient, auth_headers: dict[str, str]) -> None:
    """PUT /tier changes tier with valid token; invalid values or malformed payloads return 422."""
    # Happy path: change to T1
    resp = client.put("/tier", json={"schema_version": 1, "tier": "T1"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["tier"] == "T1"

    # Error path: invalid tier outside enum
    resp_bad = client.put("/tier", json={"schema_version": 1, "tier": "T99"}, headers=auth_headers)
    assert resp_bad.status_code == 422
    assert resp_bad.headers["content-type"] == "application/problem+json"
    assert resp_bad.json()["type"] == "urn:agent-wallet:problem:invalid-request"

    # Error path: missing schema_version
    resp_missing = client.put("/tier", json={"tier": "T0"}, headers=auth_headers)
    assert resp_missing.status_code == 422
    assert resp_missing.json()["type"] == "urn:agent-wallet:problem:invalid-request"
