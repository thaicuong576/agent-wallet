"""PgAuthStore: implements AuthStore protocol (core.storage.interfaces)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from core.storage.postgres.common import _utc
from core.storage.postgres.pool import PgPool


class PgAuthStore:
    """Sign-in nonces, sessions and step-up challenges, shared between tenants."""

    def __init__(self, pool: PgPool) -> None:
        self._pool = pool

    def put_nonce(self, address: str, nonce: str, expires_at: datetime) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO nonces (address, nonce, expires_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (address, nonce) DO UPDATE SET expires_at = EXCLUDED.expires_at",
                (address, nonce, _utc(expires_at)),
            )

    def consume_nonce(self, address: str, nonce: str, now: datetime) -> bool:
        """Single use: the row is removed whether or not it was still valid."""
        with self._pool.cursor() as cur:
            cur.execute(
                "DELETE FROM nonces WHERE address = %s AND nonce = %s RETURNING expires_at",
                (address, nonce),
            )
            row = cur.fetchone()
        return row is not None and _utc(row[0]) > _utc(now)

    def put_session(self, jti: str, address: str, expires_at: datetime) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (jti, address, expires_at, revoked) "
                "VALUES (%s, %s, %s, false) ON CONFLICT (jti) DO UPDATE SET "
                "address = EXCLUDED.address, expires_at = EXCLUDED.expires_at, revoked = false",
                (jti, address, _utc(expires_at)),
            )

    def get_session(self, jti: str, now: datetime) -> dict[str, Any] | None:
        with self._pool.cursor() as cur:
            cur.execute(
                "SELECT jti, address, expires_at FROM sessions "
                "WHERE jti = %s AND NOT revoked AND expires_at > %s",
                (jti, _utc(now)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {"jti": str(row[0]), "address": str(row[1]), "expires_at": _utc(row[2])}

    def revoke(self, jti: str) -> None:
        with self._pool.cursor() as cur:
            cur.execute("UPDATE sessions SET revoked = true WHERE jti = %s", (jti,))

    def put_stepup(
        self, challenge_id: str, payload: Mapping[str, Any], expires_at: datetime
    ) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO stepup_challenges (challenge_id, payload, expires_at) "
                "VALUES (%s, %s::jsonb, %s) ON CONFLICT (challenge_id) DO UPDATE SET "
                "payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at",
                (challenge_id, json.dumps(dict(payload), sort_keys=True), _utc(expires_at)),
            )

    def consume_stepup(self, challenge_id: str, now: datetime) -> dict[str, Any] | None:
        """Single use, even when expired: the row is removed on every attempt."""
        with self._pool.cursor() as cur:
            cur.execute(
                "DELETE FROM stepup_challenges WHERE challenge_id = %s "
                "RETURNING payload, expires_at",
                (challenge_id,),
            )
            row = cur.fetchone()
        if row is None or _utc(now) >= _utc(row[1]):
            return None
        payload = row[0]
        return dict(payload) if isinstance(payload, dict) else json.loads(payload)

    def purge_expired(self, now: datetime) -> int:
        total = 0
        with self._pool.cursor() as cur:
            cur.execute("DELETE FROM nonces WHERE expires_at <= %s", (_utc(now),))
            total += int(cur.rowcount)
            cur.execute("DELETE FROM sessions WHERE expires_at <= %s", (_utc(now),))
            total += int(cur.rowcount)
            cur.execute("DELETE FROM stepup_challenges WHERE expires_at <= %s", (_utc(now),))
            total += int(cur.rowcount)
        return total
