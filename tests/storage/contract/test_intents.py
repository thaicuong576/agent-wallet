"""IntentStore contract: insert-only intents written before signing."""

from __future__ import annotations

import pytest

from core.storage.interfaces import IntentStore
from tests.storage.contract.conftest import make_record


def test_adapter_satisfies_protocol(stores) -> None:
    assert isinstance(stores.intents, IntentStore)


def test_insert_only_and_unfinished(stores) -> None:
    assert stores.intents.unfinished("o-1") is False
    stores.intents.begin_signing("o-1", "hash-1", "2026-10-14T10:00:00Z")
    assert stores.intents.unfinished("o-1") is True
    stores.audit.append(make_record("executed", order_id="o-1"))
    assert stores.intents.unfinished("o-1") is False


def test_failed_record_also_finishes_intent(stores) -> None:
    stores.intents.begin_signing("o-2", "hash-2", "2026-10-14T10:00:00Z")
    stores.audit.append(make_record("denied", order_id="o-2"))
    assert stores.intents.unfinished("o-2") is True
    stores.audit.append(make_record("failed", order_id="o-2"))
    assert stores.intents.unfinished("o-2") is False


def test_second_intent_for_same_order_rejected(stores) -> None:
    stores.intents.begin_signing("o-3", "hash-3", "2026-10-14T10:00:00Z")
    with pytest.raises(Exception):  # noqa: B017 - backend specific integrity error
        stores.intents.begin_signing("o-3", "hash-3b", "2026-10-14T10:00:01Z")


def test_intent_update_and_delete_rejected(backend, stores) -> None:
    stores.intents.begin_signing("o-4", "hash-4", "2026-10-14T10:00:00Z")
    with pytest.raises(backend.append_only_error):
        backend.raw_audit_sql(stores, "UPDATE order_intents SET body_hash = 'x'")
    with pytest.raises(backend.append_only_error):
        backend.raw_audit_sql(stores, "DELETE FROM order_intents")
    assert stores.intents.unfinished("o-4") is True
