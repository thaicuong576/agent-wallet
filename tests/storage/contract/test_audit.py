"""AuditStore contract: append-only hash chain per owner."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.audit.verify import export_jsonl, main, verify_chain
from core.storage.interfaces import AuditStore
from tests.storage.contract.conftest import make_record


def test_adapter_satisfies_protocol(stores) -> None:
    assert isinstance(stores.audit, AuditStore)


def test_append_assigns_seq_and_chain_verifies(stores) -> None:
    first = stores.audit.append(make_record())
    second = stores.audit.append(make_record())
    third = stores.audit.append(make_record("denied"))
    assert [first["seq"], second["seq"], third["seq"]] == [0, 1, 2]
    assert first["prev_hash"] == "0" * 64
    assert second["prev_hash"] == first["hash"]
    assert third["prev_hash"] == second["hash"]
    report = verify_chain(list(stores.audit.iter_all()))
    assert report.valid and report.count == 3


def test_update_and_delete_rejected(backend, stores) -> None:
    stores.audit.append(make_record())
    with pytest.raises(backend.append_only_error):
        backend.raw_audit_sql(stores, "UPDATE audit_records SET outcome = 'executed'")
    with pytest.raises(backend.append_only_error):
        backend.raw_audit_sql(stores, "DELETE FROM audit_records")
    assert len(list(stores.audit.iter_all())) == 1


def test_find_by_order_id_returns_latest(stores) -> None:
    stores.audit.append(make_record("pending_approval", order_id="o-1"))
    stores.audit.append(make_record("executed", order_id="o-2"))
    stores.audit.append(make_record("executed", order_id="o-1"))
    found = stores.audit.find_by_order_id("o-1")
    assert found is not None
    assert found["seq"] == 2 and found["outcome"] == "executed"
    assert stores.audit.find_by_order_id("missing") is None


def test_list_filters_and_pages(stores) -> None:
    stores.audit.append(make_record("executed", order_id="o-1"))
    stores.audit.append(make_record("denied", order_id="o-2"))
    stores.audit.append(make_record("executed", order_id="o-3"))
    stores.audit.append(make_record("denied", order_id="o-1"))
    assert [r["seq"] for r in stores.audit.list()] == [0, 1, 2, 3]
    assert [r["seq"] for r in stores.audit.list(after_seq=1)] == [2, 3]
    assert [r["seq"] for r in stores.audit.list(limit=2)] == [0, 1]
    assert [r["seq"] for r in stores.audit.list(order_id="o-1")] == [0, 3]
    assert [r["seq"] for r in stores.audit.list(outcome="denied")] == [1, 3]
    assert [r["seq"] for r in stores.audit.list(after_seq=0, outcome="executed")] == [2]


def test_export_verifies_with_cli(stores, tmp_path: Path) -> None:
    for _ in range(3):
        stores.audit.append(make_record())
    target = tmp_path / "chain.jsonl"
    assert export_jsonl(list(stores.audit.iter_all()), target) == 3
    assert main([str(target)]) == 0
