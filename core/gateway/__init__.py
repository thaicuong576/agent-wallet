"""Gateway: propose, policy, simulate, sign, record (default deny).

This is the only package allowed to import `core.signer` (enforced by
tests/test_signer_boundary.py and CI).
"""

from __future__ import annotations

from core.gateway.audit import (
    AuditSink,
    InMemoryAuditSink,
    canonical,
    record_hash,
)
from core.gateway.exceptions import (
    GatewayError,
    InvalidOrder,
    OrderIdConflict,
)
from core.gateway.gateway import Gateway
from core.gateway.policy import (
    DEFAULT_IPS_HASH,
    DenyAllPolicy,
    Policy,
)

__all__ = [
    "Gateway",
    "Policy",
    "DenyAllPolicy",
    "AuditSink",
    "InMemoryAuditSink",
    "GatewayError",
    "InvalidOrder",
    "OrderIdConflict",
    "canonical",
    "record_hash",
    "DEFAULT_IPS_HASH",
]
