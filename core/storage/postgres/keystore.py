"""PgKeystoreStore: implements KeystoreStore protocol (core.storage.interfaces)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from core.storage.postgres.common import _now, validate_owner
from core.storage.postgres.pool import PgPool


class PgKeystoreStore:
    """Encrypted keystore JSON in a row. Bound to one owner, or unbound for rotation."""

    def __init__(self, pool: PgPool, bound_owner: str | None = None) -> None:
        self._pool = pool
        self._bound = bound_owner

    def _scope(self, owner: str) -> str:
        validate_owner(owner)
        if self._bound is not None and owner != self._bound:
            raise ValueError("This keystore handle is bound to a different owner")
        return owner

    @contextmanager
    def _cursor(self, owner: str | None) -> Iterator[Any]:
        # Rotation (unbound) sees every keystore through the '*' setting the policy admits.
        with self._pool.tenant(owner if owner is not None else "*") as cur:
            yield cur

    def put(self, owner: str, blob: str, *, overwrite: bool = False) -> None:
        self._scope(owner)
        if not isinstance(blob, str) or not blob:
            raise ValueError("keystore blob must be a non-empty string")
        kdf = "unknown"
        try:
            parsed = json.loads(blob)
            crypto = parsed.get("crypto") or parsed.get("Crypto") or {}
            kdf = str(crypto.get("kdf") or parsed.get("kdf") or "unknown")
        except (ValueError, AttributeError):
            pass
        with self._cursor(owner) as cur:
            if overwrite:
                cur.execute(
                    "INSERT INTO keystores (owner, keystore, kdf, created_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (owner) DO UPDATE "
                    "SET keystore = EXCLUDED.keystore, kdf = EXCLUDED.kdf",
                    (owner, blob, kdf, _now()),
                )
                return
            cur.execute(
                "INSERT INTO keystores (owner, keystore, kdf, created_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (owner) DO NOTHING",
                (owner, blob, kdf, _now()),
            )
            if cur.rowcount == 0:
                raise FileExistsError(f"A keystore already exists for {owner}")

    def get(self, owner: str) -> str | None:
        self._scope(owner)
        with self._cursor(owner) as cur:
            cur.execute("SELECT keystore FROM keystores WHERE owner = %s", (owner,))
            row = cur.fetchone()
        return None if row is None else str(row[0])

    def exists(self, owner: str) -> bool:
        return self.get(owner) is not None

    def owners(self) -> Iterable[str]:
        with self._cursor(self._bound) as cur:
            if self._bound is None:
                cur.execute("SELECT owner FROM keystores ORDER BY owner")
            else:
                cur.execute("SELECT owner FROM keystores WHERE owner = %s", (self._bound,))
            return [str(r[0]) for r in cur.fetchall()]
