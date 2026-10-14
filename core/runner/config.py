"""State paths configuration for agent wallet runner."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StatePaths:
    """Filesystem paths for SQLite state files in AGENT_WALLET_DATA_DIR."""

    tiers: Path  # AGENT_WALLET_DATA_DIR/state.sqlite
    audit: Path  # AGENT_WALLET_DATA_DIR/audit.sqlite
    lock: Path  # AGENT_WALLET_DATA_DIR/gateway.lock.sqlite


def from_env(env: Mapping[str, str] | None = None) -> StatePaths:
    """Construct StatePaths from environment mapping.

    If AGENT_WALLET_DATA_DIR is unset or empty, defaults to '.data'.
    """
    if env is None:
        env = os.environ
    data_dir_str = env.get("AGENT_WALLET_DATA_DIR")
    data_dir = Path(data_dir_str) if data_dir_str else Path(".data")
    return StatePaths(
        tiers=data_dir / "state.sqlite",
        audit=data_dir / "audit.sqlite",
        lock=data_dir / "gateway.lock.sqlite",
    )
