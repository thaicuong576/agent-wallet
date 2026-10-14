"""Append-only audit log with a SHA-256 hash chain."""

from __future__ import annotations

from core.audit.sink import GENESIS_PREV_HASH, SqliteAuditSink
from core.audit.verify import (
    VerifyReport,
    export_jsonl,
    import_jsonl,
    verify_chain,
)
from core.gateway.audit import AuditSink, canonical, record_hash

__all__ = [
    "AuditSink",
    "SqliteAuditSink",
    "VerifyReport",
    "verify_chain",
    "export_jsonl",
    "import_jsonl",
    "canonical",
    "record_hash",
    "GENESIS_PREV_HASH",
]
