"""Operator-level global stop flag, read by the gateway. Fail closed on any error."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

GLOBAL_STOP_ENV = "AGENT_WALLET_GLOBAL_STOP"

_CLEAR_VALUES = frozenset({"", "0"})


def file_flag(
    path: Path | str | None,
    env: Mapping[str, str] | None = None,
    *,
    settings_reader: Callable[[], bool] | None = None,
) -> Callable[[], bool]:
    """Return a callable that reports whether the global stop is set.

    The stop is set when any source says so: `AGENT_WALLET_GLOBAL_STOP` in `env` (any value
    other than empty or `0`), the flag file at `path` exists, or the optional `settings_reader`
    returns true. `env` is read at call time. Any error while reading a source, including a
    file permission error or a failing settings reader, means the stop is set.
    """
    source_env: Mapping[str, str] = os.environ if env is None else env
    flag_path = Path(path) if path is not None else None

    def is_stopped() -> bool:
        try:
            if str(source_env.get(GLOBAL_STOP_ENV, "")).strip() not in _CLEAR_VALUES:
                return True
            if flag_path is not None:
                try:
                    os.stat(flag_path)
                except FileNotFoundError:
                    pass
                else:
                    return True
            if settings_reader is not None and bool(settings_reader()):
                return True
        except Exception:
            return True
        return False

    return is_stopped
