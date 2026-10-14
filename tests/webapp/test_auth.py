"""Sign-in and session rows of design 6.4."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.auth import AuthService, InMemoryAuthStore, SqliteAuthStore, default_auth_store
from api.session import COOKIE_NAME, sign_token
from tests.webapp.conftest import HEADERS, ORIGIN, SECRET, build_app, get_nonce, login

PROBLEM = "application/problem+json"


def _slug(resp) -> str:
    assert resp.headers["content-type"].startswith(PROBLEM)
    return resp.json()["type"].rsplit(":", 1)[1]


def _verify(client, wallet, issued, message=None, address=None):
    return client.post(
        "/auth/verify",
        json={
            "address": address or wallet.address,
            "nonce": issued["nonce"],
            "signature": wallet.sign(message or issued["message"]),
        },
        headers=HEADERS,
    )


def test_login_happy_path_creates_session_and_tenant(client, wallet, logins):
    resp = login(client, wallet)
    assert resp.status_code == 200, resp.text
    assert resp.json()["address"] == wallet.address.lower()
    assert "token" not in resp.json()  # cookie only unless bearer is enabled
    set_cookie = resp.headers["set-cookie"]
    assert COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie
    assert "Secure" not in set_cookie  # localhost origin
    assert logins == [wallet.address.lower()]  # first-login hook (tenant creation in A3)
    assert client.get("/whoami").json() == {"address": wallet.address.lower()}


def test_message_contents(client, wallet):
    body = get_nonce(client, wallet.address).json()
    msg = body["message"]
    assert "Domain: localhost:8000" in msg
    assert f"Address: {wallet.address.lower()}" in msg
    assert "Chain ID: 84532" in msg
    assert f"Nonce: {body['nonce']}" in msg
    assert "Issued At: 2026-10-15T12:00:00Z" in msg
    assert "Expiration Time: 2026-10-15T12:05:00Z" in msg


def test_secure_cookie_for_non_local_origin(auth_env, service, wallet):
    origin = "https://wallet.example.org"
    auth_env.setenv("AGENT_WALLET_PUBLIC_ORIGIN", origin)
    c = TestClient(build_app(service), base_url=origin)
    h = {"Origin": origin, "X-AW-CSRF": "1"}
    issued = c.post("/auth/nonce", json={"address": wallet.address}, headers=h).json()
    assert "Domain: wallet.example.org" in issued["message"]
    r = c.post(
        "/auth/verify",
        json={
            "address": wallet.address,
            "nonce": issued["nonce"],
            "signature": wallet.sign(issued["message"]),
        },
        headers=h,
    )
    assert r.status_code == 200
    assert "Secure" in r.headers["set-cookie"]


def test_nonce_reuse_rejected(client, wallet):
    issued = get_nonce(client, wallet.address).json()
    assert _verify(client, wallet, issued).status_code == 200
    again = _verify(client, wallet, issued)
    assert again.status_code == 401
    assert _slug(again) == "unauthenticated"


def test_never_issued_nonce_rejected(client, wallet):
    issued = get_nonce(client, wallet.address).json()
    other = {"nonce": "ab" * 16 + issued["nonce"][32:], "message": issued["message"]}
    assert _verify(client, wallet, other).status_code == 401


def test_expired_message_rejected(client, wallet, clock):
    issued = get_nonce(client, wallet.address).json()
    clock.advance(minutes=5, seconds=1)
    r = _verify(client, wallet, issued)
    assert r.status_code == 401
    assert _slug(r) == "unauthenticated"


def test_wrong_domain_rejected(client, wallet):
    issued = get_nonce(client, wallet.address).json()
    forged = issued["message"].replace("Domain: localhost:8000", "Domain: evil.example")
    r = _verify(client, wallet, issued, message=forged)
    assert r.status_code == 401
    assert _slug(r) == "signature-invalid"


def test_wrong_chain_rejected(client, wallet):
    issued = get_nonce(client, wallet.address).json()
    forged = issued["message"].replace("Chain ID: 84532", "Chain ID: 1")
    r = _verify(client, wallet, issued, message=forged)
    assert r.status_code == 401
    assert _slug(r) == "signature-invalid"


def test_tampered_message_rejected(client, wallet):
    issued = get_nonce(client, wallet.address).json()
    r = _verify(client, wallet, issued, message=issued["message"] + "\nResources: everything")
    assert _slug(r) == "signature-invalid"


def test_wrong_address_rejected(client, wallet, other_wallet):
    issued = get_nonce(client, wallet.address).json()
    r = _verify(client, other_wallet, issued, address=wallet.address)
    assert r.status_code == 401
    assert _slug(r) == "signature-invalid"
    # a failed attempt does not burn the nonce: the real owner can still finish
    assert _verify(client, wallet, issued).status_code == 200


def test_nonce_bound_to_address(client, wallet, other_wallet):
    issued = get_nonce(client, wallet.address).json()
    r = _verify(client, other_wallet, issued)
    assert r.status_code == 401


def test_address_case_variants(client, wallet):
    body = wallet.address[2:]
    for variant in [wallet.address, "0x" + body.lower(), "0x" + body.upper()]:
        r = login(client, wallet, address=variant)
        assert r.status_code == 200, (variant, r.text)
        assert r.json()["address"] == wallet.address.lower()
    for bad in ["0x123", "0X" + body, body, wallet.address + "00", "", "../x"]:
        assert get_nonce(client, bad).status_code == 400, bad


def test_unset_session_secret_returns_503(client, wallet, auth_env):
    auth_env.delenv("AGENT_WALLET_SESSION_SECRET")
    r = get_nonce(client, wallet.address)
    assert r.status_code == 503
    assert _slug(r) == "service-unavailable"
    assert client.get("/whoami").status_code == 503


def test_short_session_secret_returns_503(client, wallet, auth_env):
    auth_env.setenv("AGENT_WALLET_SESSION_SECRET", "x" * 31)
    assert get_nonce(client, wallet.address).status_code == 503
    auth_env.setenv("AGENT_WALLET_SESSION_SECRET", "x" * 32)
    assert get_nonce(client, wallet.address).status_code == 200


def test_unset_origin_returns_503(client, wallet, auth_env):
    auth_env.delenv("AGENT_WALLET_PUBLIC_ORIGIN")
    assert get_nonce(client, wallet.address).status_code == 503


def test_session_token_tamper_and_expiry_rejected(client, wallet, clock):
    assert login(client, wallet).status_code == 200
    token = client.cookies.get(COOKIE_NAME)
    part, mac = token.split(".")

    payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    payload["exp"] += 10
    forged_part = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    for bad in [f"{forged_part}.{mac}", f"{part}.{mac[:-2]}AA", "garbage", part, "x"]:
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, bad)
        assert client.get("/whoami").status_code == 401, bad

    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/whoami").status_code == 200
    clock.advance(hours=8, seconds=1)
    r = client.get("/whoami")
    assert r.status_code == 401
    assert _slug(r) == "unauthenticated"


def test_logout_revokes_jti(client, wallet):
    login(client, wallet)
    token = client.cookies.get(COOKIE_NAME)
    assert client.post("/auth/logout", headers=HEADERS).status_code == 200
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/whoami").status_code == 401


def test_cross_owner_token_never_accepted(client, wallet, other_wallet, clock):
    login(client, wallet)
    token_a = client.cookies.get(COOKIE_NAME)
    part = token_a.split(".")[0]
    jti_a = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["jti"]
    # even a correctly signed token naming B with A's jti is refused (the store binds jti to A)
    forged, _ = sign_token(SECRET.encode(), other_wallet.address, jti_a, clock())
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, forged)
    assert client.get("/whoami").status_code == 401
    # and the owner is never read from a query or header
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token_a)
    r = client.get(
        f"/whoami?address={other_wallet.address}", headers={"X-Owner": other_wallet.address}
    )
    assert r.json() == {"address": wallet.address.lower()}


def test_two_owners_get_separate_sessions(client, wallet, other_wallet):
    login(client, wallet)
    token_a = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    login(client, other_wallet)
    assert client.get("/whoami").json() == {"address": other_wallet.address.lower()}
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token_a)
    assert client.get("/whoami").json() == {"address": wallet.address.lower()}


def test_csrf_header_and_origin_required(client, wallet):
    login(client, wallet)
    assert client.post("/emergency-stop", headers=HEADERS).status_code == 200
    no_header = client.post("/emergency-stop", headers={"Origin": ORIGIN})
    assert no_header.status_code == 403 and _slug(no_header) == "csrf"
    wrong = client.post(
        "/emergency-stop", headers={"Origin": "https://evil.example", "X-AW-CSRF": "1"}
    )
    assert wrong.status_code == 403 and _slug(wrong) == "csrf"
    assert client.post("/emergency-stop", headers={"X-AW-CSRF": "1"}).status_code == 403
    assert client.get("/whoami").status_code == 200  # reads need neither
    # sign-in is covered too (login CSRF)
    assert client.post("/auth/nonce", json={"address": wallet.address}).status_code == 403


def test_bearer_only_when_enabled(client, wallet, auth_env):
    login(client, wallet)
    token = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/whoami", headers=bearer).status_code == 401
    auth_env.setenv("AGENT_WALLET_ALLOW_BEARER", "1")
    assert client.get("/whoami", headers=bearer).status_code == 200
    # a bearer client carries no ambient credential, so no CSRF header is needed
    assert client.post("/emergency-stop", headers=bearer).status_code == 200


def test_eip1271_not_supported_message(client, wallet):
    issued = get_nonce(client, wallet.address).json()
    r = client.post(
        "/auth/verify",
        json={
            "address": wallet.address,
            "nonce": issued["nonce"],
            "signature": "0x" + "ab" * 130,  # longer than a 65-byte EOA signature
        },
        headers=HEADERS,
    )
    assert r.status_code == 401
    assert _slug(r) == "signature-invalid"
    assert "EIP-1271" in r.json()["detail"]


def test_nonce_rate_limited(client, wallet):
    codes = [get_nonce(client, wallet.address).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_no_secret_or_token_in_logs(client, wallet, caplog):
    caplog.set_level(logging.DEBUG)
    login(client, wallet)
    token = client.cookies.get(COOKIE_NAME)
    client.get("/whoami")
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token[:-3] + "AAA")
    client.get("/whoami")
    client.cookies.set(COOKIE_NAME, token)
    client.post("/auth/logout", headers=HEADERS)
    issued = get_nonce(client, wallet.address).json()
    signature = wallet.sign(issued["message"])
    client.post(
        "/auth/verify",
        json={"address": wallet.address, "nonce": issued["nonce"], "signature": signature},
        headers=HEADERS,
    )
    text = "\n".join(r.getMessage() for r in caplog.records) + caplog.text
    assert "login ok" in text  # the capture saw the auth logger
    for secret in (SECRET, token, token.split(".")[1], signature, signature[2:]):
        assert secret not in text


@pytest.mark.parametrize("make_store", [InMemoryAuthStore, lambda: SqliteAuthStore(":memory:")])
def test_store_semantics(make_store):
    store = make_store()
    now = datetime(2026, 10, 15, tzinfo=UTC)
    store.put_nonce("0xa", "n1", now + timedelta(minutes=5))
    assert store.consume_nonce("0xa", "n1", now) is True
    assert store.consume_nonce("0xa", "n1", now) is False
    store.put_nonce("0xa", "n2", now + timedelta(minutes=5))
    assert store.consume_nonce("0xa", "n2", now + timedelta(minutes=5)) is False
    store.put_session("j", "0xa", now + timedelta(hours=1))
    assert store.get_session("j", now)["address"] == "0xa"
    assert store.get_session("j", now + timedelta(hours=1)) is None
    store.revoke("j")
    assert store.get_session("j", now) is None
    store.put_stepup("c", {"a": 1}, now + timedelta(minutes=2))
    assert store.consume_stepup("c", now) == {"a": 1}
    assert store.consume_stepup("c", now) is None
    store.put_stepup("d", {"a": 1}, now + timedelta(minutes=2))
    assert store.consume_stepup("d", now + timedelta(minutes=3)) is None
    assert isinstance(store.purge_expired(now + timedelta(days=1)), int)


def test_sqlite_store_lives_under_data_dir(tmp_path, monkeypatch, wallet, clock, auth_env):
    monkeypatch.setenv("AGENT_WALLET_DATA_DIR", str(tmp_path))
    store = default_auth_store()
    c = TestClient(build_app(AuthService(store, clock=clock)), base_url=ORIGIN)
    assert login(c, wallet).status_code == 200
    assert c.get("/whoami").status_code == 200
    store.close()
    assert (tmp_path / "auth.sqlite").exists()
