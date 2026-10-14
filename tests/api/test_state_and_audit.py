"""Tests for GET /tier, GET /portfolio, GET /ips, GET /decisions, and GET /eval/report."""

from __future__ import annotations

import copy
import pathlib
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app, get_validator

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]


def test_get_tier_happy(client: TestClient) -> None:
    """GET /tier happy path: returns TierState validating against schema."""
    resp = client.get("/tier")
    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == 1
    assert data["tier"] in ("T0", "T1", "T2")
    assert isinstance(data["emergency_stop"], bool)
    assert data["changed_by"] == "owner"

    get_validator("tier-state").validate(data)


def test_get_portfolio_happy(client: TestClient) -> None:
    """GET /portfolio happy path: returns PortfolioState validating against schema."""
    resp = client.get("/portfolio")
    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == 1
    assert data["price_source"] == "mainnet_reference"
    assert "positions" in data
    assert "total_value_usd" in data

    get_validator("portfolio-state").validate(data)


def test_get_ips_happy(client: TestClient) -> None:
    """GET /ips happy path: returns active IPS and its canonical SHA-256 hash."""
    resp = client.get("/ips")
    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == 1
    assert "ips" in data
    assert "ips_hash" in data
    assert len(data["ips_hash"]) == 64

    get_validator("ips-response").validate(data)
    get_validator("ips").validate(data["ips"])


def test_get_decisions_happy_and_filtering(
    client: TestClient, valid_order_payload: dict[str, Any]
) -> None:
    """GET /decisions happy path: lists audit records with outcome and order_id filtering."""
    # Submit two different orders
    order1 = copy.deepcopy(valid_order_payload)
    order1["order_id"] = "11111111-1111-1111-1111-111111111111"
    resp1 = client.post("/orders", json=order1)
    assert resp1.status_code == 200

    order2 = copy.deepcopy(valid_order_payload)
    order2["order_id"] = "22222222-2222-2222-2222-222222222222"
    resp2 = client.post("/orders", json=order2)
    assert resp2.status_code == 200

    # 1. Unfiltered query returns all records
    list_all = client.get("/decisions")
    assert list_all.status_code == 200
    all_data = list_all.json()
    assert all_data["schema_version"] == 1
    assert len(all_data["items"]) >= 2
    get_validator("decisions-response").validate(all_data)

    # 2. Filter by order_id
    filtered_order = client.get(f"/decisions?order_id={order1['order_id']}")
    assert filtered_order.status_code == 200
    items = filtered_order.json()["items"]
    assert len(items) == 1
    assert items[0]["order_id"] == order1["order_id"]

    # 3. Filter by outcome
    filtered_outcome = client.get("/decisions?outcome=denied")
    assert filtered_outcome.status_code == 200
    for item in filtered_outcome.json()["items"]:
        assert item["outcome"] == "denied"


def test_get_decisions_pagination(client: TestClient, valid_order_payload: dict[str, Any]) -> None:
    """GET /decisions supports limit and opaque cursor pagination."""
    # Seed 3 orders
    for i in range(3):
        order = copy.deepcopy(valid_order_payload)
        order["order_id"] = f"{i:08x}-0000-0000-0000-000000000000"
        client.post("/orders", json=order)

    # Page 1: limit 2
    p1 = client.get("/decisions?limit=2")
    assert p1.status_code == 200
    d1 = p1.json()
    assert len(d1["items"]) == 2
    assert d1["next_cursor"] is not None

    # Page 2: with cursor
    p2 = client.get(f"/decisions?limit=2&cursor={d1['next_cursor']}")
    assert p2.status_code == 200
    d2 = p2.json()
    assert len(d2["items"]) >= 1


def test_get_decisions_errors(client: TestClient) -> None:
    """GET /decisions error paths: invalid query params return 422 Problem."""
    # Invalid order_id
    resp = client.get("/decisions?order_id=bad-uuid")
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "urn:agent-wallet:problem:invalid-request"

    # Invalid outcome enum
    resp2 = client.get("/decisions?outcome=not_a_real_outcome")
    assert resp2.status_code == 422
    assert resp2.json()["type"] == "urn:agent-wallet:problem:invalid-request"

    # Invalid limit (< 1)
    resp3 = client.get("/decisions?limit=0")
    assert resp3.status_code == 422
    assert resp3.json()["type"] == "urn:agent-wallet:problem:invalid-request"

    # Invalid limit (> 200)
    resp4 = client.get("/decisions?limit=201")
    assert resp4.status_code == 422
    assert resp4.json()["type"] == "urn:agent-wallet:problem:invalid-request"

    # Invalid cursor
    resp5 = client.get("/decisions?cursor=invalid-non-numeric-cursor-!@#")
    assert resp5.status_code == 422
    assert resp5.json()["type"] == "urn:agent-wallet:problem:invalid-request"


def test_get_eval_report_happy_and_404() -> None:
    """GET /eval/report returns 200 when report is configured, and 404 Problem when absent."""
    # 1. 404 when absent
    app_no_report = create_app()
    client_no_report = TestClient(app_no_report)
    resp_404 = client_no_report.get("/eval/report")
    assert resp_404.status_code == 404
    assert resp_404.headers["content-type"] == "application/problem+json"
    assert resp_404.json()["type"] == "urn:agent-wallet:problem:not-found"

    # 2. 200 when present
    sample_report = {
        "schema_version": 1,
        "generated_at": "2026-10-16T12:00:00Z",
        "metrics": {"total_scenarios": 10, "passed": 10},
        "scenarios": [
            {
                "id": "SEC-01",
                "layer": "A",
                "passed": True,
                "rule_codes": ["LIMIT.PER_TX"],
                "tx_hash": None,
            }
        ],
    }
    app_with_report = create_app(eval_report=sample_report)
    client_with_report = TestClient(app_with_report)
    resp_200 = client_with_report.get("/eval/report")
    assert resp_200.status_code == 200
    data = resp_200.json()
    assert data["schema_version"] == 1
    assert data["scenarios"][0]["id"] == "SEC-01"
    get_validator("eval-report").validate(data)
