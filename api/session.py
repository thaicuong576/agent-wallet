"""Session tokens and auth configuration.

Token: base64url(json{addr,iat,exp,jti}) "." base64url(HMAC-SHA256(secret, payload part)).
Configuration is read from the environment on every call so that an unset or short secret
fails closed (503) instead of silently signing with a weak key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

COOKIE_NAME = "aw_session"
CSRF_HEADER = "X-AW-CSRF"
STEP_UP_HEADER = "X-Step-Up"
CHAIN_ID = 84532
SESSION_TTL = timedelta(hours=8)
NONCE_TTL = timedelta(minutes=5)
STEP_UP_TTL = timedelta(minutes=2)
MIN_SECRET_BYTES = 32

ENV_SECRET = "AGENT_WALLET_SESSION_SECRET"
ENV_ORIGIN = "AGENT_WALLET_PUBLIC_ORIGIN"
ENV_BEARER = "AGENT_WALLET_ALLOW_BEARER"

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class AuthUnavailable(Exception):
    """Auth is not configured safely (secret or origin). Maps to 503, fail closed."""


class TokenInvalid(Exception):
    """The session token is malformed, tampered, expired or not a token at all."""


@dataclass(frozen=True)
class AuthConfig:
    secret: bytes
    origin: str  # scheme://host[:port], no trailing slash
    domain: str  # host[:port], bound into the signed message
    allow_bearer: bool

    @property
    def secure_cookie(self) -> bool:
        return (urlsplit(self.origin).hostname or "") not in _LOCAL_HOSTS

    @classmethod
    def from_env(cls) -> AuthConfig:
        secret = os.environ.get(ENV_SECRET, "").encode("utf-8")
        if len(secret) < MIN_SECRET_BYTES:
            raise AuthUnavailable("session secret is not configured")
        origin = os.environ.get(ENV_ORIGIN, "").strip().rstrip("/")
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise AuthUnavailable("public origin is not configured")
        allow_bearer = os.environ.get(ENV_BEARER, "") == "1"
        return cls(secret=secret, origin=origin, domain=parts.netloc, allow_bearer=allow_bearer)


def normalize_address(value: object) -> str:
    """Lowercase 0x address, or ValueError. Case-insensitive on input."""
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value):
        raise ValueError("not a 20-byte hex address")
    return value.lower()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenInvalid("bad encoding") from exc


def new_jti() -> str:
    return secrets.token_hex(16)


def _mac(secret: bytes, payload_part: str) -> str:
    return _b64e(hmac.new(secret, payload_part.encode("ascii"), hashlib.sha256).digest())


def sign_token(secret: bytes, address: str, jti: str, now: datetime) -> tuple[str, datetime]:
    """Return (token, expires_at)."""
    expires = now + SESSION_TTL
    payload = {
        "addr": normalize_address(address),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": jti,
    }
    part = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{part}.{_mac(secret, part)}", datetime.fromtimestamp(payload["exp"], UTC)


def verify_token(secret: bytes, token: str, now: datetime) -> dict:
    """Return the payload of a valid, unexpired token. Revocation is checked by the caller."""
    if not isinstance(token, str) or token.count(".") != 1 or len(token) > 2048:
        raise TokenInvalid("malformed")
    part, mac = token.split(".")
    if not part.isascii() or not hmac.compare_digest(
        mac.encode("ascii", "replace"), _mac(secret, part).encode("ascii")
    ):
        raise TokenInvalid("bad signature")
    try:
        payload = json.loads(_b64d(part))
        addr = normalize_address(payload["addr"])
        exp = int(payload["exp"])
        jti = payload["jti"]
        int(payload["iat"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenInvalid("bad payload") from exc
    if not isinstance(jti, str) or not jti:
        raise TokenInvalid("bad payload")
    if now.timestamp() >= exp:
        raise TokenInvalid("expired")
    return {"addr": addr, "exp": exp, "jti": jti}
