"""Audit sink protocols and in-memory hash chain implementation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable


def canonical(o: Any) -> bytes:
    """Serialize object to canonical JSON (sorted keys, compact separators, UTF-8)."""
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(r: dict[str, Any]) -> str:
    """Calculate SHA-256 hash of a decision record covering prev_hash but excluding hash."""
    body = {k: v for k, v in r.items() if k != "hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


@runtime_checkable
class AuditSink(Protocol):
    """Protocol for append-only audit sinks."""

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Assign seq, prev_hash, and hash, store the record, and return it."""
        ...


class InMemoryAuditSink:
    """In-memory append-only audit sink maintaining a cryptographic hash chain."""

    GENESIS_PREV_HASH = "0" * 64

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    @property
    def records(self) -> list[dict[str, Any]]:
        """Return shallow copies of all stored audit records."""
        return [dict(r) for r in self._records]

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append record to chain, assigning seq, prev_hash, and canonical sha256 hash."""
        rec = dict(record)
        seq = len(self._records)
        if seq == 0:
            prev_hash = self.GENESIS_PREV_HASH
        else:
            prev_hash = self._records[-1]["hash"]

        rec["seq"] = seq
        rec["prev_hash"] = prev_hash
        rec["hash"] = record_hash(rec)

        self._records.append(rec)
        return dict(rec)

    def verify(self) -> bool:
        """Verify the cryptographic integrity of the audit log hash chain."""
        prev = self.GENESIS_PREV_HASH
        for i, rec in enumerate(self._records):
            if rec.get("seq") != i:
                return False
            if rec.get("prev_hash") != prev:
                return False
            if rec.get("hash") != record_hash(rec):
                return False
            prev = rec["hash"]
        return True

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return dict(self._records[index])

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for r in self._records:
            yield dict(r)
