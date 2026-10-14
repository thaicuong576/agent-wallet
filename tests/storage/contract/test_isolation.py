"""Per-owner isolation: two owners never see each other."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from core.audit.verify import verify_chain
from core.storage.sqlite import tenant_dir
from tests.storage.contract.conftest import NOW, OWNER_A, OWNER_B, iso, make_record


def test_owner_a_cannot_see_owner_b(backend) -> None:
    a = backend.open(OWNER_A)
    b = backend.open(OWNER_B)

    a.audit.append(make_record("executed", order_id="a-1", created_at=iso(NOW)))
    a.tier_write.set_tier("T2")
    a.tier_write.request_approval("a-1")
    a.intents.begin_signing("a-1", "h", "2026-10-14T10:00:00Z")

    assert b.audit.find_by_order_id("a-1") is None
    assert list(b.audit.iter_all()) == []
    assert b.tier_read.current() == "T0"
    assert b.tier_read.pending_orders() == []
    assert b.tier_read.get_approval("a-1") is None

    since = NOW - timedelta(hours=1)
    assert a.ledger.executed_count_since(since) == 1
    assert b.ledger.executed_count_since(since) == 0


def test_each_owner_chain_verifies_alone(backend) -> None:
    a = backend.open(OWNER_A)
    b = backend.open(OWNER_B)
    for _ in range(3):
        a.audit.append(make_record())
    b.audit.append(make_record())
    assert [r["seq"] for r in b.audit.iter_all()] == [0]
    assert verify_chain(list(a.audit.iter_all())).valid
    assert verify_chain(list(b.audit.iter_all())).valid


@pytest.mark.parametrize(
    "owner",
    ["0x" + "A" * 40, "../etc", "0x" + "a" * 39, "", "0x" + "a" * 40 + "/../x"],
)
def test_address_is_validated(tmp_path: Path, owner: str) -> None:
    with pytest.raises(ValueError):
        tenant_dir(tmp_path, owner)


def test_path_stays_inside_owners_dir(tmp_path: Path) -> None:
    path = tenant_dir(tmp_path, OWNER_A)
    assert path.parent == (tmp_path / "owners").resolve()
