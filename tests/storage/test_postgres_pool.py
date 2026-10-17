"""Unit tests for PgPool metrics, sizing, warmup and error behavior."""

from __future__ import annotations

import pathlib
import time
from unittest.mock import MagicMock, patch

import pytest

from core.storage.postgres.pool import (
    DEFAULT_POOL_SIZE,
    PgPool,
    _request_db_acc,
    db_calls,
    db_ms,
    db_stats,
    parse_pool_size,
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


def test_parse_pool_size_defaults_and_validation() -> None:
    assert DEFAULT_POOL_SIZE == 12
    # Default when None or empty
    assert parse_pool_size(None) == 12
    assert parse_pool_size("") == 12
    assert parse_pool_size("   ") == 12

    # Valid values 1..50
    assert parse_pool_size("1") == 1
    assert parse_pool_size("12") == 12
    assert parse_pool_size("25") == 25
    assert parse_pool_size("50") == 50
    assert parse_pool_size("  8  ") == 8

    # Rejects nonsense (falls back to default 12)
    assert parse_pool_size("0") == 12
    assert parse_pool_size("-1") == 12
    assert parse_pool_size("-100") == 12
    assert parse_pool_size("51") == 12
    assert parse_pool_size("100") == 12
    assert parse_pool_size("abc") == 12
    assert parse_pool_size("12.5") == 12
    assert parse_pool_size("None") == 12


def test_pg_pool_default_max_size_and_constructor_arg() -> None:
    pool1 = PgPool("postgresql://fake:5432/fake")
    try:
        assert pool1.max_size == 12
    finally:
        pool1.close()

    pool2 = PgPool("postgresql://fake:5432/fake", max_size=7)
    try:
        assert pool2.max_size == 7
    finally:
        pool2.close()


def test_pg_pool_warm_unreachable_never_raises_and_does_not_block() -> None:
    # Unreachable DSN with short connect timeout
    pool = PgPool("postgresql://u:p@127.0.0.1:1/db?connect_timeout=1")
    try:
        t0 = time.perf_counter()
        pool.warm(4)
        elapsed = time.perf_counter() - t0
        # warm() must return immediately (non-blocking)
        assert elapsed < 0.2
    finally:
        pool.close()


def test_pg_pool_warm_successful_populates_idle() -> None:
    pool = PgPool("postgresql://fake:5432/fake", max_size=10)
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.broken = False

    with patch("psycopg.connect", return_value=mock_conn):
        pool.warm(3)
        # Give daemon thread a brief moment to run
        for _ in range(50):
            if pool._idle.qsize() == 3:
                break
            time.sleep(0.01)
        assert pool._idle.qsize() == 3

    pool.close()


def test_build_backend_postgres_pool_size_env_override_and_warm(tmp_path: pathlib.Path) -> None:
    from api.main import build_backend

    with (
        patch("core.storage.postgres.PostgresBackend.verify_schema", return_value=1),
        patch("core.storage.postgres.pool.PgPool.warm") as mock_warm,
    ):
        # 1. Default pool size 12
        b1 = build_backend({"DATABASE_URL": "postgresql://u:p@host/db"}, tmp_path)
        try:
            assert b1._pool.max_size == 12
            assert mock_warm.called
            mock_warm.assert_called_with(4)
        finally:
            b1.close()

        # 2. Env override with valid value
        mock_warm.reset_mock()
        b2 = build_backend(
            {"DATABASE_URL": "postgresql://u:p@host/db", "AGENT_WALLET_DB_POOL_SIZE": "20"},
            tmp_path,
        )
        try:
            assert b2._pool.max_size == 20
            mock_warm.assert_called_with(4)
        finally:
            b2.close()

        # 3. Env override with nonsense falls back to 12
        mock_warm.reset_mock()
        b3 = build_backend(
            {"DATABASE_URL": "postgresql://u:p@host/db", "AGENT_WALLET_DB_POOL_SIZE": "nonsense"},
            tmp_path,
        )
        try:
            assert b3._pool.max_size == 12
            mock_warm.assert_called_with(4)
        finally:
            b3.close()
