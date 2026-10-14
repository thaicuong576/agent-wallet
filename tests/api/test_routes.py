"""Test that OpenAPI contract paths match app route set, and test default app construction."""

from __future__ import annotations

import pathlib
from typing import Any

import yaml
from fastapi.testclient import TestClient

from api.app import create_app
from api.main import app as main_app

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
OPENAPI_FILE = ROOT_DIR / "contracts" / "openapi.yaml"


def _app_operations() -> dict[str, set[str]]:
    """Path to HTTP methods of every route the app mounts, read from its generated schema.

    `app.routes` no longer lists included routers' routes individually, so the schema is the
    reliable view. Framework docs routes are not in it; `/health` is the unauthenticated probe.
    """
    schema = create_app().openapi()
    return {
        path: {method.upper() for method in item}
        for path, item in schema["paths"].items()
        if path != "/health"
    }


def test_routes_equal_openapi_paths() -> None:
    """The OpenAPI path set equals the app's contract route set."""
    openapi_spec: dict[str, Any] = yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))
    openapi_paths = set(openapi_spec["paths"].keys())

    app_contract_routes = set(_app_operations())

    assert app_contract_routes == openapi_paths, (
        f"Difference: {app_contract_routes ^ openapi_paths}"
    )
    # Confirm health probe is also present and unauthenticated
    assert "/health" in create_app().openapi()["paths"]


def test_http_methods_match_openapi() -> None:
    """Every operation method defined in openapi.yaml is mounted on the FastAPI app, and back."""
    openapi_spec: dict[str, Any] = yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))
    route_methods = _app_operations()

    for path, path_item in openapi_spec["paths"].items():
        assert path in route_methods, f"Path {path} missing from app routes"
        declared = {method.upper() for method in path_item}
        assert declared == route_methods[path], f"Methods differ on {path}"


def test_default_app_instantiation_without_arguments(
    valid_order_payload: dict[str, Any],
) -> None:
    """create_app() without arguments builds an in-memory deny-all T0 app."""
    app = create_app()
    client = TestClient(app)

    # Health probe works without auth
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Default tier is T0
    tier_resp = client.get("/tier")
    assert tier_resp.status_code == 200
    assert tier_resp.json()["tier"] == "T0"
    assert tier_resp.json()["emergency_stop"] is False

    # Orders are denied under T0
    order_resp = client.post("/orders", json=valid_order_payload)
    assert order_resp.status_code == 200
    record = order_resp.json()
    assert record["outcome"] == "denied"
    assert record["tier"] == "T0"
    assert record["decision"]["rules_hit"][0]["code"] == "TIER.READ_ONLY"


def test_main_app_instance() -> None:
    """api.main exposes app instance ready for ASGI servers."""
    client = TestClient(main_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
