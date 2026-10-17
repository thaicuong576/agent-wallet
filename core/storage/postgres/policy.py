"""PgPolicySelectionStore: implements PolicySelectionStore protocol (core.storage.interfaces)."""

from __future__ import annotations

from typing import Any

from core.storage.postgres.common import _iso, _now
from core.storage.postgres.pool import PgPool


class PgPolicySelectionStore:
    """Append-only record of the preset an owner selected, per owner."""

    def __init__(self, pool: PgPool, owner: str) -> None:
        self._pool = pool
        self._owner = owner

    def append(self, preset_id: str, ips_hash: str, signature_hash: str) -> None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("aw:policy:" + self._owner,),
            )
            cur.execute(
                "INSERT INTO policy_selection (owner, seq, preset_id, ips_hash, signature_hash, "
                "created_at) SELECT %(o)s, coalesce(max(seq) + 1, 0), %(p)s, %(i)s, %(s)s, %(t)s "
                "FROM policy_selection WHERE owner = %(o)s",
                {"o": self._owner, "p": preset_id, "i": ips_hash, "s": signature_hash, "t": _now()},
            )

    def latest(self) -> dict[str, Any] | None:
        with self._pool.tenant(self._owner) as cur:
            cur.execute(
                "SELECT seq, preset_id, ips_hash, signature_hash, created_at "
                "FROM policy_selection WHERE owner = %s ORDER BY seq DESC LIMIT 1",
                (self._owner,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "seq": int(row[0]),
            "preset_id": str(row[1]),
            "ips_hash": str(row[2]),
            "signature_hash": str(row[3]),
            "created_at": _iso(row[4]),
        }
