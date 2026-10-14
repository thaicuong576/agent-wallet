"""LedgerQueries contract: window semantics over executed records."""

from __future__ import annotations

from decimal import Decimal

from core.storage.interfaces import LedgerQueries
from tests.storage.contract.conftest import HOUR, NOW, iso, make_record


def test_adapter_satisfies_protocol(stores) -> None:
    assert isinstance(stores.ledger, LedgerQueries)


def test_only_executed_counts_approve_excluded(stores) -> None:
    at = iso(NOW - HOUR / 2)
    stores.audit.append(make_record("executed", value="50000000", created_at=at))
    stores.audit.append(make_record("denied", value="900000000", created_at=at))
    stores.audit.append(make_record("failed", value="900000000", created_at=at))
    stores.audit.append(make_record("pending_approval", value="900000000", created_at=at))
    # An executed approve is a transaction but not spend.
    stores.audit.append(
        make_record("executed", function="approve", value="900000000", created_at=at)
    )
    since = NOW - HOUR
    assert stores.ledger.spent_usd_since(since) == Decimal("50")
    assert stores.ledger.executed_count_since(since) == 2


def test_window_excludes_records_at_or_before_since(stores) -> None:
    since = NOW - HOUR
    stores.audit.append(make_record("executed", value="10000000", created_at=iso(since)))
    stores.audit.append(make_record("executed", value="20000000", created_at=iso(since - HOUR)))
    stores.audit.append(make_record("executed", value="30000000", created_at=iso(NOW)))
    assert stores.ledger.spent_usd_since(since) == Decimal("30")
    assert stores.ledger.executed_count_since(since) == 1


def test_empty_ledger_is_zero(stores) -> None:
    assert stores.ledger.spent_usd_since(NOW - HOUR) == Decimal("0")
    assert stores.ledger.executed_count_since(NOW - HOUR) == 0


def test_spend_uses_token_price_and_decimals(stores) -> None:
    stores.audit.append(
        make_record(
            "executed",
            token="WETH",
            value="500000000000000000",
            decimals=18,
            created_at=iso(NOW),
        )
    )
    assert stores.ledger.spent_usd_since(NOW - HOUR) == Decimal("1000")
