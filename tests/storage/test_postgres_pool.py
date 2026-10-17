"""Unit tests for PgPool metrics, sizing, warmup and error behavior (Ticket J)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from core.storage.postgres.pool import (
    PgPool,
    _request_db_acc,
    db_calls,
    db_ms,
    db_stats,
)


def test_pool_db_stats_outside_and_inside_accumulator() -> None:

    db_calls.set(0)
    db_ms.set(0)
    assert db_stats() == (0, 0)

    acc = [5, 120]
    token = _request_db_acc.set(acc)
    try:
        assert db_stats() == (5, 120)
    finally:
        _request_db_acc.reset(token)

    assert db_stats() == (0, 0)


def test_pool_connection_increments_db_calls_and_ms() -> None:
    pool = PgPool("postgresql://fake:5432/fake", max_size=5)
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.broken = False

    acc = [0, 0]
    token = _request_db_acc.set(acc)
    try:
        with patch.object(pool, "connect", return_value=mock_conn):
            with pool.connection() as conn:
                assert conn is mock_conn
                time.sleep(0.01)  # small sleep to ensure elapsed_ms >= 0

        assert acc[0] == 1
        assert acc[1] >= 0
        assert mock_conn.commit.called
    finally:
        _request_db_acc.reset(token)
        pool.close()


def test_pool_connection_rollback_on_error() -> None:
    pool = PgPool("postgresql://fake:5432/fake", max_size=5)
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.broken = False

    acc = [0, 0]
    token = _request_db_acc.set(acc)
    try:
        with patch.object(pool, "connect", return_value=mock_conn):
            with pytest.raises(ValueError):
                with pool.connection():
                    raise ValueError("app error")

        assert acc[0] == 1
        assert mock_conn.rollback.called
    finally:
        _request_db_acc.reset(token)
        pool.close()
