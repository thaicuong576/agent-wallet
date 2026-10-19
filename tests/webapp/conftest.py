"""Headless wallet and app fixtures for the sign-in tests.

TestWallet signs the exact text the server returns, as a browser wallet would with
personal_sign. No network, no real keys: every key is generated in the test process.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.auth import (
    AuthService,
    InMemoryAuthStore,
    Session,
    install_auth_handlers,
    require_session,
    require_step_up,
    router,
)

ORIGIN = "http://localhost:8000"
DOMAIN = "localhost:8000"
SECRET = "s" * 40
HEADERS = {"Origin": ORIGIN, "X-AW-CSRF": "1"}


class TestWallet:
    __test__ = False

    def __init__(self) -> None:
        self.account = Account.create()
        self.address: str = self.account.address  # EIP-55 checksum form

    def sign(self, message: str) -> str:
        signed = self.account.sign_message(encode_defunct(text=message))
        return "0x" + signed.signature.hex().removeprefix("0x")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def wallet() -> TestWallet:
    return TestWallet()


@pytest.fixture
def other_wallet() -> TestWallet:
    return TestWallet()


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AGENT_WALLET_SESSION_SECRET", SECRET)
    monkeypatch.setenv("AGENT_WALLET_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.delenv("AGENT_WALLET_ALLOW_BEARER", raising=False)
    return monkeypatch


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def logins() -> list[str]:
    return []


@pytest.fixture
def service(auth_env, clock, logins) -> AuthService:
    return AuthService(InMemoryAuthStore(), clock=clock, on_login=logins.append)


def build_app(service: AuthService) -> FastAPI:
    app = FastAPI()
    app.state.auth_service = service
    install_auth_handlers(app)
    app.include_router(router)

    @app.get("/whoami")
    def whoami(session: Annotated[Session, Depends(require_session)]) -> dict:
        return {"address": session.address}

    @app.post("/emergency-stop")
    def stop(session: Annotated[Session, Depends(require_session)]) -> dict:
        return {"stopped": True}

    @app.post("/emergency-stop/release")
    def release(session: Annotated[Session, Depends(require_step_up("release_stop"))]) -> dict:
        return {"released": True, "by": session.address}

    @app.post("/orders/{order_id}/approve")
    def approve(
        order_id: str, session: Annotated[Session, Depends(require_step_up("approve"))]
    ) -> dict:
        return {"approved": order_id}

    return app


@pytest.fixture
def app(service) -> FastAPI:
    return build_app(service)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, base_url=ORIGIN)


def get_nonce(client: TestClient, address: str, headers: dict | None = None):
    return client.post("/auth/nonce", json={"address": address}, headers=headers or HEADERS)


def login(client: TestClient, wallet: TestWallet, address: str | None = None):
    """Full sign-in. Returns the verify response (session cookie is kept by the client)."""
    addr = address or wallet.address
    issued = get_nonce(client, addr).json()
    return client.post(
        "/auth/verify",
        json={
            "address": addr,
            "nonce": issued["nonce"],
            "signature": wallet.sign(issued["message"]),
        },
        headers=HEADERS,
    )


def step_up(client: TestClient, wallet: TestWallet, action: str, params: dict | None = None) -> str:
    """Request a challenge, sign it, and return the X-Step-Up header value."""
    resp = client.post(
        "/auth/step-up", json={"action": action, "params": params or {}}, headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return f"{body['challenge_id']}.{wallet.sign(body['message'])}"
