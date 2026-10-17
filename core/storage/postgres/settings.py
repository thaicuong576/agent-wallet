"""PgSettings: implements SettingsStore protocol (core.storage.interfaces)."""

from __future__ import annotations

from collections.abc import Callable

from core.storage.postgres.pool import PgPool


class PgSettings:
    """Operator settings. ``global_stop`` fails closed: any error reads as stopped."""

    def __init__(self, pool: PgPool, env_stop: Callable[[], bool] | None = None) -> None:
        self._pool = pool
        self._env_stop = env_stop

    def global_stop(self) -> bool:
        try:
            if self._env_stop is not None and self._env_stop():
                return True
            with self._pool.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE key = 'global_stop'")
                row = cur.fetchone()
            return row is not None and str(row[0]).strip().lower() in {"1", "true", "yes", "on"}
        except Exception:
            return True

    def set_global_stop(self, stopped: bool) -> None:
        with self._pool.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('global_stop', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                ("true" if stopped else "false",),
            )
