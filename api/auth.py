"""Wallet sign-in, sessions and step-up challenges (design 6.1 to 6.4, 12.1, 12.2).

Exposes an APIRouter (`router`), the dependencies `require_session` and `require_step_up(action)`,
and `AuthService`. The application mounts the router and sets `app.state.auth_service`.
Nothing here logs a secret, token or signature. EOAs only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from api.session import (
    CHAIN_ID,
    COOKIE_NAME,
    CSRF_HEADER,
    NONCE_TTL,
    SESSION_TTL,
    STEP_UP_HEADER,
    STEP_UP_TTL,
    AuthConfig,
    AuthUnavailable,
    TokenInvalid,
    new_jti,
    normalize_address,
    sign_token,
    verify_token,
)
from core.storage.interfaces import AuthStore

log = logging.getLogger("agent_wallet.auth")

STEP_UP_ACTIONS = ("release_stop", "tier_raise", "policy_change", "withdraw", "approve")
STATEMENT = "Testnet only. This signature does not move funds."
_NONCE_RE = re.compile(r"^[0-9a-f]{40}$")  # 32 hex random + 8 hex issued-at epoch seconds
_CHALLENGE_RE = re.compile(r"^[0-9a-f]{32}$")
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
NONCES_PER_MINUTE = 10


# ---------------------------------------------------------------- problems (RFC 7807)


class AuthProblem(Exception):
    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str | None = None,
        extra: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(slug)
        self.status, self.slug, self.title = status, slug, title
        self.detail, self.extra, self.headers = detail, dict(extra or {}), dict(headers or {})

    def response(self) -> JSONResponse:
        body: dict[str, Any] = {
            "type": f"urn:agent-wallet:problem:{self.slug}",
            "title": self.title,
            "status": self.status,
        }
        if self.detail:
            body["detail"] = self.detail[:500]
        body.update(self.extra)
        return JSONResponse(
            body,
            status_code=self.status,
            media_type="application/problem+json",
            headers=self.headers or None,
        )


class StepUpRequired(AuthProblem):
    def __init__(self, action: str, detail: str) -> None:
        super().__init__(
            401,
            "step-up-required",
            "A fresh owner signature is required",
            detail,
            extra={"challenge": {"endpoint": "/auth/step-up", "action": action}},
        )


def _unauthenticated(detail: str) -> AuthProblem:
    return AuthProblem(401, "unauthenticated", "Authentication required", detail)


def _signature_invalid(detail: str) -> AuthProblem:
    return AuthProblem(401, "signature-invalid", "Signature invalid", detail)


def _unavailable() -> AuthProblem:
    return AuthProblem(503, "service-unavailable", "Authentication is not available")


def _csrf(detail: str) -> AuthProblem:
    return AuthProblem(403, "csrf", "Cross-site request rejected", detail)


# ---------------------------------------------------------------- stores


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class InMemoryAuthStore:
    """AuthStore for tests and single-process demos. Thread safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nonces: dict[tuple[str, str], datetime] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._stepups: dict[str, tuple[dict[str, Any], datetime]] = {}

    def put_nonce(self, address: str, nonce: str, expires_at: datetime) -> None:
        with self._lock:
            self._nonces[(address, nonce)] = expires_at

    def consume_nonce(self, address: str, nonce: str, now: datetime) -> bool:
        with self._lock:
            expires = self._nonces.pop((address, nonce), None)
        return expires is not None and now < expires

    def put_session(self, jti: str, address: str, expires_at: datetime) -> None:
        with self._lock:
            self._sessions[jti] = {
                "jti": jti,
                "address": address,
                "expires_at": expires_at,
                "revoked": False,
            }

    def get_session(self, jti: str, now: datetime) -> dict[str, Any] | None:
        with self._lock:
            row = self._sessions.get(jti)
            if row is None or row["revoked"] or now >= row["expires_at"]:
                return None
            return dict(row)

    def revoke(self, jti: str) -> None:
        with self._lock:
            if jti in self._sessions:
                self._sessions[jti]["revoked"] = True

    def put_stepup(
        self, challenge_id: str, payload: Mapping[str, Any], expires_at: datetime
    ) -> None:
        with self._lock:
            self._stepups[challenge_id] = (dict(payload), expires_at)

    def consume_stepup(self, challenge_id: str, now: datetime) -> dict[str, Any] | None:
        with self._lock:
            item = self._stepups.pop(challenge_id, None)
        if item is None or now >= item[1]:
            return None
        return item[0]

    def purge_expired(self, now: datetime) -> int:
        with self._lock:
            n = 0
            for key in [k for k, v in self._nonces.items() if now >= v]:
                del self._nonces[key]
                n += 1
            for key in [k for k, v in self._stepups.items() if now >= v[1]]:
                del self._stepups[key]
                n += 1
            for key in [k for k, v in self._sessions.items() if now >= v["expires_at"]]:
                del self._sessions[key]
                n += 1
            return n


class SqliteAuthStore:
    """AuthStore in DATA_DIR/auth.sqlite. Parameterised SQL only."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS nonces(
                    address TEXT NOT NULL, nonce TEXT NOT NULL, expires_at TEXT NOT NULL,
                    PRIMARY KEY(address, nonce));
                CREATE TABLE IF NOT EXISTS sessions(
                    jti TEXT PRIMARY KEY, address TEXT NOT NULL, expires_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS stepups(
                    challenge_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    expires_at TEXT NOT NULL);
                """
            )

    def put_nonce(self, address: str, nonce: str, expires_at: datetime) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO nonces(address, nonce, expires_at) VALUES (?, ?, ?)",
                (address, nonce, _ts(expires_at)),
            )

    def consume_nonce(self, address: str, nonce: str, now: datetime) -> bool:
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM nonces WHERE address = ? AND nonce = ? AND expires_at > ?",
                (address, nonce, _ts(now)),
            )
            # an expired row is also removed so it cannot linger
            self._db.execute("DELETE FROM nonces WHERE address = ? AND nonce = ?", (address, nonce))
            return cur.rowcount == 1

    def put_session(self, jti: str, address: str, expires_at: datetime) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO sessions(jti, address, expires_at, revoked)"
                " VALUES (?, ?, ?, 0)",
                (jti, address, _ts(expires_at)),
            )

    def get_session(self, jti: str, now: datetime) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT jti, address, expires_at FROM sessions"
                " WHERE jti = ? AND revoked = 0 AND expires_at > ?",
                (jti, _ts(now)),
            ).fetchone()
        if row is None:
            return None
        return {"jti": row[0], "address": row[1], "expires_at": _parse_ts(row[2])}

    def revoke(self, jti: str) -> None:
        with self._lock:
            self._db.execute("UPDATE sessions SET revoked = 1 WHERE jti = ?", (jti,))

    def put_stepup(
        self, challenge_id: str, payload: Mapping[str, Any], expires_at: datetime
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO stepups(challenge_id, payload, expires_at)"
                " VALUES (?, ?, ?)",
                (challenge_id, json.dumps(dict(payload), sort_keys=True), _ts(expires_at)),
            )

    def consume_stepup(self, challenge_id: str, now: datetime) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload, expires_at FROM stepups WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            self._db.execute("DELETE FROM stepups WHERE challenge_id = ?", (challenge_id,))
        if row is None or now >= _parse_ts(row[1]):
            return None
        return json.loads(row[0])

    def purge_expired(self, now: datetime) -> int:
        n = 0
        with self._lock:
            for table in ("nonces", "sessions", "stepups"):
                n += self._db.execute(
                    f"DELETE FROM {table} WHERE expires_at <= ?",  # noqa: S608 fixed allowlist
                    (_ts(now),),
                ).rowcount
        return n

    def close(self) -> None:
        with self._lock:
            self._db.close()


def default_auth_store() -> SqliteAuthStore:
    data_dir = Path(os.environ.get("AGENT_WALLET_DATA_DIR", ".data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return SqliteAuthStore(data_dir / "auth.sqlite")


# ---------------------------------------------------------------- messages


def build_signin_message(
    domain: str, address: str, nonce: str, issued_at: datetime, expires_at: datetime
) -> str:
    return "\n".join(
        [
            "BaKhi Wallet wants you to sign in with your Ethereum account.",
            f"Domain: {domain}",
            f"Address: {address}",
            f"Statement: {STATEMENT}",
            f"Chain ID: {CHAIN_ID}",
            f"Nonce: {nonce}",
            f"Issued At: {issued_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"Expiration Time: {expires_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        ]
    )


def canonical_params(params: Mapping[str, Any]) -> str:
    return json.dumps(dict(params), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def params_hash(params: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_params(params).encode("utf-8")).hexdigest()


def build_step_up_message(
    domain: str,
    address: str,
    action: str,
    params: Mapping[str, Any],
    challenge_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    return "\n".join(
        [
            "BaKhi Wallet asks you to authorise one action.",
            f"Domain: {domain}",
            f"Address: {address}",
            f"Action: {action}",
            f"Parameters: {canonical_params(params)}",
            f"Parameters hash: {params_hash(params)}",
            f"Chain ID: {CHAIN_ID}",
            f"Challenge: {challenge_id}",
            f"Issued At: {issued_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"Expiration Time: {expires_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"Statement: {STATEMENT}",
        ]
    )


def _recover(message: str, signature: str) -> str:
    """Recover the signer address (lowercase) or raise signature-invalid."""
    try:
        raw = bytes.fromhex(signature[2:] if signature.startswith(("0x", "0X")) else signature)
    except (ValueError, TypeError):
        raise _signature_invalid("signature is not hex") from None
    if len(raw) != 65:
        raise _signature_invalid(
            "only plain 65-byte EOA signatures are accepted; EIP-1271 contract wallets are "
            "not supported"
        )
    try:
        return Account.recover_message(encode_defunct(text=message), signature=raw).lower()
    except Exception:  # noqa: BLE001 - any recovery failure is an invalid signature
        raise _signature_invalid("signature could not be recovered") from None


# ---------------------------------------------------------------- service


@dataclass(frozen=True)
class Session:
    address: str
    jti: str
    expires_at: datetime


@dataclass(frozen=True)
class NonceChallenge:
    address: str
    nonce: str
    message: str
    expires_at: datetime


@dataclass(frozen=True)
class StepUpChallenge:
    challenge_id: str
    message: str
    expires_at: datetime
    action: str


class AuthService:
    def __init__(
        self,
        store: AuthStore,
        *,
        config_provider: Callable[[], AuthConfig] = AuthConfig.from_env,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_login: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self._config_provider = config_provider
        self._on_login = on_login
        self._recent: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def config(self) -> AuthConfig:
        try:
            return self._config_provider()
        except AuthUnavailable:
            raise _unavailable() from None

    # -- login

    def issue_nonce(self, address: str, now: datetime) -> NonceChallenge:
        cfg = self.config()
        addr = self._address(address)
        self._rate_limit(addr, now)
        nonce = secrets.token_hex(16) + f"{int(now.timestamp()):08x}"
        expires = datetime.fromtimestamp(int(now.timestamp()), UTC) + NONCE_TTL
        self.store.put_nonce(addr, nonce, expires)
        issued = datetime.fromtimestamp(int(now.timestamp()), UTC)
        return NonceChallenge(
            addr, nonce, build_signin_message(cfg.domain, addr, nonce, issued, expires), expires
        )

    def verify(self, address: str, nonce: str, signature: str, now: datetime) -> Session:
        cfg = self.config()
        addr = self._address(address)
        if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
            raise _unauthenticated("nonce is not valid")
        issued = datetime.fromtimestamp(int(nonce[32:], 16), UTC)
        expires = issued + NONCE_TTL
        if now >= expires:
            raise _unauthenticated("sign-in message expired")
        message = build_signin_message(cfg.domain, addr, nonce, issued, expires)
        if _recover(message, signature) != addr:
            raise _signature_invalid("signature does not match the address and message")
        if not self.store.consume_nonce(addr, nonce, now):
            raise _unauthenticated("nonce was already used or never issued")
        session = self._new_session(cfg, addr, now)
        if self._on_login is not None:
            self._on_login(addr)
        log.info("login ok address=%s", addr)
        return session

    def _new_session(self, cfg: AuthConfig, addr: str, now: datetime) -> Session:
        jti = new_jti()
        expires = now + SESSION_TTL
        self.store.put_session(jti, addr, expires)
        return Session(addr, jti, expires)

    def mint_token(self, session: Session, now: datetime) -> str:
        token, _ = sign_token(self.config().secret, session.address, session.jti, now)
        return token

    # -- session

    def session_from_token(self, token: str, now: datetime) -> Session:
        cfg = self.config()
        try:
            payload = verify_token(cfg.secret, token, now)
        except TokenInvalid:
            raise _unauthenticated("session is not valid") from None
        row = self.store.get_session(payload["jti"], now)
        if row is None or row["address"] != payload["addr"]:
            raise _unauthenticated("session is not valid")
        return Session(payload["addr"], payload["jti"], row["expires_at"])

    def logout(self, session: Session) -> None:
        self.store.revoke(session.jti)

    # -- step-up

    def issue_step_up(
        self, session: Session, action: str, params: Mapping[str, Any], now: datetime
    ) -> StepUpChallenge:
        cfg = self.config()
        if action not in STEP_UP_ACTIONS:
            raise AuthProblem(422, "invalid-request", "Unknown step-up action")
        try:
            canonical_params(params)
        except (TypeError, ValueError):
            raise AuthProblem(422, "invalid-request", "Parameters must be JSON") from None
        challenge_id = secrets.token_hex(16)
        issued = datetime.fromtimestamp(int(now.timestamp()), UTC)
        expires = issued + STEP_UP_TTL
        message = build_step_up_message(
            cfg.domain, session.address, action, params, challenge_id, issued, expires
        )
        self.store.put_stepup(
            challenge_id,
            {
                "address": session.address,
                "action": action,
                "params_hash": params_hash(params),
                "message": message,
            },
            expires,
        )
        return StepUpChallenge(challenge_id, message, expires, action)

    def check_step_up(
        self,
        session: Session,
        header: str | None,
        action: str,
        params: Mapping[str, Any],
        now: datetime,
    ) -> None:
        """Verify and consume a challenge. Raises AuthProblem (StepUpRequired) on any failure."""
        self.config()
        if not header or header.count(".") != 1:
            raise StepUpRequired(action, "send X-Step-Up: <challenge_id>.<signature>")
        challenge_id, signature = header.split(".")
        if not _CHALLENGE_RE.fullmatch(challenge_id):
            raise StepUpRequired(action, "challenge is not valid")
        payload = self.store.consume_stepup(challenge_id, now)  # single use, even on failure
        if payload is None:
            raise StepUpRequired(action, "challenge unknown, used or expired")
        try:
            expected_hash = params_hash(params)
        except (TypeError, ValueError):
            raise StepUpRequired(action, "parameters are not valid") from None
        if (
            payload.get("action") != action
            or payload.get("params_hash") != expected_hash
            or payload.get("address") != session.address
        ):
            raise StepUpRequired(action, "challenge does not match this action and parameters")
        if _recover(payload["message"], signature) != session.address:
            raise _signature_invalid("step-up signature is not from the session address")

    # -- helpers

    @staticmethod
    def _address(address: object) -> str:
        try:
            return normalize_address(address)
        except ValueError:
            raise AuthProblem(
                400, "invalid-request", "Invalid address", "address must be 0x plus 40 hex digits"
            ) from None

    def _rate_limit(self, addr: str, now: datetime) -> None:
        with self._lock:
            window = self._recent[addr]
            while window and now - window[0] > timedelta(minutes=1):
                window.popleft()
            if len(window) >= NONCES_PER_MINUTE:
                raise AuthProblem(
                    429, "rate-limited", "Too many sign-in attempts", headers={"Retry-After": "60"}
                )
            window.append(now)


# ---------------------------------------------------------------- dependencies


def get_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise _unavailable()
    return service


def check_csrf(request: Request, cfg: AuthConfig) -> None:
    if request.method not in _UNSAFE:
        return
    if request.headers.get(CSRF_HEADER) != "1":
        raise _csrf(f"missing {CSRF_HEADER} header")
    if request.headers.get("origin") != cfg.origin:
        raise _csrf("origin not allowed")


def _bearer(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    if value[:7].lower() == "bearer ":
        return value[7:].strip() or None
    return None


def require_session(request: Request) -> Session:
    """The verified session. The owner address comes only from the signed token."""
    service = get_service(request)
    cfg = service.config()
    cookie = request.cookies.get(COOKIE_NAME)
    token = cookie
    if token is None and cfg.allow_bearer:
        token = _bearer(request)
    if not token:
        raise _unauthenticated("no session")
    if cookie is not None:  # bearer clients are not ambient credentials, so no CSRF check
        check_csrf(request, cfg)
    return service.session_from_token(token, service.clock())


async def default_step_up_params(request: Request) -> dict[str, Any]:
    """Path parameters plus the JSON object body, if any."""
    params: dict[str, Any] = dict(request.path_params)
    raw = await request.body()
    if raw:
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if isinstance(body, dict):
            params.update(body)
    return params


def require_step_up(
    action: str,
    params_from: Callable[[Request], Awaitable[Mapping[str, Any]]] = default_step_up_params,
) -> Callable[..., Awaitable[Session]]:
    """Dependency factory: session plus a fresh, single-use signature bound to action+params."""
    if action not in STEP_UP_ACTIONS:
        raise ValueError(f"unknown step-up action: {action}")

    async def dependency(
        request: Request, session: Annotated[Session, Depends(require_session)]
    ) -> Session:
        service = get_service(request)
        params = await params_from(request)
        service.check_step_up(
            session, request.headers.get(STEP_UP_HEADER), action, params, service.clock()
        )
        return session

    return dependency


# ---------------------------------------------------------------- routes


class _AuthRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except AuthProblem as problem:
                return problem.response()

        return handler


def install_auth_handlers(app: FastAPI) -> None:
    """Map AuthProblem raised by dependencies on non-auth routes to problem+json."""

    async def _handle(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, AuthProblem)
        return exc.response()

    app.add_exception_handler(AuthProblem, _handle)


router = APIRouter(prefix="/auth", tags=["auth"], route_class=_AuthRoute)


class NonceRequest(BaseModel):
    address: str


class VerifyRequest(BaseModel):
    address: str
    nonce: str
    signature: str


class StepUpRequest(BaseModel):
    action: Literal["release_stop", "tier_raise", "policy_change", "withdraw", "approve"]
    params: dict[str, Any] = Field(default_factory=dict)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _login_csrf(request: Request) -> None:
    """Sign-in routes carry no cookie yet; same header and Origin rules stop login CSRF."""
    service = get_service(request)
    check_csrf(request, service.config())


@router.post("/nonce", dependencies=[Depends(_login_csrf)])
def post_nonce(body: NonceRequest, request: Request) -> dict[str, str]:
    service = get_service(request)
    challenge = service.issue_nonce(body.address, service.clock())
    return {
        "nonce": challenge.nonce,
        "message": challenge.message,
        "expires_at": _iso(challenge.expires_at),
    }


@router.post("/verify", dependencies=[Depends(_login_csrf)])
def post_verify(body: VerifyRequest, request: Request, response: Response) -> dict[str, Any]:
    service = get_service(request)
    now = service.clock()
    session = service.verify(body.address, body.nonce, body.signature, now)
    cfg = service.config()
    token = service.mint_token(session, now)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=cfg.secure_cookie,
        path="/",
    )
    out: dict[str, Any] = {"address": session.address, "expires_at": _iso(session.expires_at)}
    if cfg.allow_bearer:
        out["token"] = token
    return out


@router.post("/logout")
def post_logout(
    request: Request, response: Response, session: Annotated[Session, Depends(require_session)]
) -> dict[str, bool]:
    get_service(request).logout(session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/step-up")
def post_step_up(
    body: StepUpRequest, request: Request, session: Annotated[Session, Depends(require_session)]
) -> dict[str, str]:
    service = get_service(request)
    challenge = service.issue_step_up(session, body.action, body.params, service.clock())
    return {
        "challenge_id": challenge.challenge_id,
        "message": challenge.message,
        "expires_at": _iso(challenge.expires_at),
    }
