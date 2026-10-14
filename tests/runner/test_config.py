"""Tests for runner state paths configuration."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.runner.config import StatePaths, from_env


def test_from_env_default() -> None:
    """When AGENT_WALLET_DATA_DIR is unset, defaults to .data directory."""
    paths = from_env({})
    assert isinstance(paths, StatePaths)
    assert paths.tiers == Path(".data/state.sqlite")
    assert paths.audit == Path(".data/audit.sqlite")
    assert paths.lock == Path(".data/gateway.lock.sqlite")


def test_from_env_custom_dir() -> None:
    """Custom AGENT_WALLET_DATA_DIR sets the base path for all three state files."""
    paths = from_env({"AGENT_WALLET_DATA_DIR": "custom/store"})
    assert paths.tiers == Path("custom/store/state.sqlite")
    assert paths.audit == Path("custom/store/audit.sqlite")
    assert paths.lock == Path("custom/store/gateway.lock.sqlite")


def test_from_env_empty_string_defaults() -> None:
    """Empty string for AGENT_WALLET_DATA_DIR defaults to .data directory."""
    paths = from_env({"AGENT_WALLET_DATA_DIR": ""})
    assert paths.tiers == Path(".data/state.sqlite")
    assert paths.audit == Path(".data/audit.sqlite")
    assert paths.lock == Path(".data/gateway.lock.sqlite")


def test_state_paths_frozen() -> None:
    """StatePaths is an immutable (frozen) dataclass."""
    paths = from_env({})
    with pytest.raises(FrozenInstanceError):
        paths.tiers = Path("other.sqlite")  # type: ignore[misc]


def test_from_env_none_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling from_env() with None/no args reads os.environ."""
    monkeypatch.setenv("AGENT_WALLET_DATA_DIR", "env_dir")
    paths = from_env()
    assert paths.tiers == Path("env_dir/state.sqlite")
    assert paths.audit == Path("env_dir/audit.sqlite")
    assert paths.lock == Path("env_dir/gateway.lock.sqlite")
