"""Tests for agent-facing POST /orders endpoint."""

from __future__ import annotations

import copy
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from core.gateway import Gateway, InMemoryAuditSink
from core.tiers import OwnerControls, TierStore, TierView


class MockEscalatePolicy:
    """Policy that unconditionally escalates orders for owner approval."""

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
                    "evidence": {"order_usd": "100.00", "escalate_above_usd": "50"},
                }
            ],
            "reason": "Order exceeds limit, needs approval.",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T10:00:00Z",
        }


def test_submit_order_happy_denied(client: TestClient, valid_order_payload: dict[str, Any]) -> None:
    """POST /orders happy path: valid order returns 200 DecisionRecord (denied)."""
    resp = client.post("/orders", json=valid_order_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == 1
    assert data["order_id"] == valid_order_payload["order_id"]
    assert data["outcome"] == "denied"
    assert data["order"] == valid_order_payload
    assert data["decision"]["decision"] == "deny"
    assert "hash" in data
    assert "prev_hash" in data


def test_submit_order_happy_escalated(valid_order_payload: dict[str, Any]) -> None:
    """POST /orders happy path: escalated order queues for owner approval."""
    store = TierStore(":memory:")
    view = TierView(store)
    owner = OwnerControls(store)
    audit = InMemoryAuditSink()
    gw = Gateway(
        policy=MockEscalatePolicy(),
        tier="T2",
        emergency_stop=False,
        audit_sink=audit,
    )
    owner.set_tier("T2")
    app = create_app(gateway=gw, tier_view=view, owner_controls=owner, audit_sink=audit)
    client = TestClient(app)

    resp = client.post("/orders", json=valid_order_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["outcome"] == "pending_approval"
    assert data["decision"]["decision"] == "escalate"

    # Verify that order was registered in pending queue
    order_id = valid_order_payload["order_id"]
    assert view.approval_status(order_id) == "pending"
    assert order_id in view.pending_orders()


def test_submit_order_idempotent_replay(
    client: TestClient, valid_order_payload: dict[str, Any]
) -> None:
    """POST /orders idempotency: replaying exact order returns identical record."""
    resp1 = client.post("/orders", json=valid_order_payload)
    assert resp1.status_code == 200

    resp2 = client.post("/orders", json=valid_order_payload)
    assert resp2.status_code == 200
    assert resp1.json() == resp2.json()


def test_submit_order_error_schema_validation(client: TestClient) -> None:
    """POST /orders error path: malformed order returns 422 Problem details."""
    invalid_order = {
        "schema_version": 1,
        "order_id": "not-a-uuid",
        "created_at": "invalid-timestamp",
    }
    resp = client.post("/orders", json=invalid_order)
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"

    prob = resp.json()
    assert prob["type"] == "urn:agent-wallet:problem:invalid-request"
    assert prob["status"] == 422
    assert "errors" in prob
    assert len(prob["errors"]) > 0


def test_submit_order_error_conflict_different_body(
    client: TestClient, valid_order_payload: dict[str, Any]
) -> None:
    """POST /orders error path: reusing order_id with different payload returns 409 Problem."""
    resp1 = client.post("/orders", json=valid_order_payload)
    assert resp1.status_code == 200

    # Modify body but keep same order_id
    conflicting_order = copy.deepcopy(valid_order_payload)
    conflicting_order["amount"]["value"] = "999999999"

    resp2 = client.post("/orders", json=conflicting_order)
    assert resp2.status_code == 409
    assert resp2.headers["content-type"] == "application/problem+json"

    prob = resp2.json()
    assert prob["type"] == "urn:agent-wallet:problem:order-id-conflict"
    assert prob["status"] == 409


def test_submit_order_error_non_json_body(client: TestClient) -> None:
    """POST /orders error path: non-JSON body returns 422 Problem."""
    resp = client.post(
        "/orders",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "urn:agent-wallet:problem:invalid-request"
