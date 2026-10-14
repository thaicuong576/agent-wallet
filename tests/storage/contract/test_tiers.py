"""TierRead and TierWrite contract."""

from __future__ import annotations

import pytest

from core.storage.interfaces import TierRead, TierWrite
from core.tiers.errors import (
    EmergencyStopActiveError,
    InvalidTierError,
    OrderAlreadyDecidedError,
    OrderNotFoundError,
)
from tests.storage.contract.conftest import OWNER_A


def test_adapter_satisfies_protocols(stores) -> None:
    assert isinstance(stores.tier_read, TierRead)
    assert isinstance(stores.tier_write, TierWrite)


def test_default_t0(stores) -> None:
    assert stores.tier_read.current() == "T0"
    assert stores.tier_read.is_stopped() is False
    assert stores.tier_read.pending_orders() == []


def test_set_tier_and_reject_invalid(stores) -> None:
    stores.tier_write.set_tier("T2")
    assert stores.tier_read.current() == "T2"
    with pytest.raises(InvalidTierError):
        stores.tier_write.set_tier("T9")
    assert stores.tier_read.current() == "T2"


def test_stop_persists(backend, stores) -> None:
    stores.tier_write.engage_stop()
    assert stores.tier_read.is_stopped() is True
    reopened = backend.open(OWNER_A)
    assert reopened.tier_read.is_stopped() is True
    reopened.tier_write.release_stop()
    assert reopened.tier_read.is_stopped() is False


def test_approval_lifecycle(stores) -> None:
    stores.tier_write.request_approval("o-1")
    stores.tier_write.request_approval("o-2")
    assert sorted(stores.tier_read.pending_orders()) == ["o-1", "o-2"]
    assert stores.tier_read.approval_status("o-1") == "pending"
    stores.tier_write.approve("o-1")
    stores.tier_write.reject("o-2")
    assert stores.tier_read.approval_status("o-1") == "approved"
    assert stores.tier_read.approval_status("o-2") == "rejected"
    assert stores.tier_read.pending_orders() == []
    rec = stores.tier_read.get_approval("o-1")
    assert rec is not None and rec["status"] == "approved"
    assert stores.tier_read.get_approval("nope") is None
    assert stores.tier_read.approval_status("nope") is None


def test_second_decision_raises(stores) -> None:
    stores.tier_write.request_approval("o-1")
    stores.tier_write.approve("o-1")
    with pytest.raises(OrderAlreadyDecidedError):
        stores.tier_write.reject("o-1")
    with pytest.raises(OrderNotFoundError):
        stores.tier_write.approve("unknown")


def test_approve_blocked_under_stop(stores) -> None:
    stores.tier_write.request_approval("o-1")
    stores.tier_write.engage_stop()
    with pytest.raises(EmergencyStopActiveError):
        stores.tier_write.approve("o-1")
    assert stores.tier_read.approval_status("o-1") == "pending"


def test_tier_read_after_write_across_two_connections(backend, stores) -> None:
    reader = backend.open(OWNER_A)
    assert reader is not stores
    stores.tier_write.set_tier("T1")
    assert reader.tier_read.current() == "T1"
    stores.tier_write.request_approval("o-9")
    assert reader.tier_read.pending_orders() == ["o-9"]
