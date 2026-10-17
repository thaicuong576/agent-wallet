"""In-process read cache with owner isolation, single-flight compute and deep copy."""

from __future__ import annotations

import concurrent.futures
import copy
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class _CacheEntry:
    value: Any
    stored_at: float
    version: Any


class ReadCache:
    """Short in-process cache with single-flight compute and owner isolation."""

    def __init__(
        self,
        *,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _CacheEntry] = {}
        self._in_flight: dict[tuple[str, str], concurrent.futures.Future[Any]] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_or_compute(
        self,
        key: tuple[str, str],
        version: Any,
        ttl_s: float,
        compute: Callable[[], T],
    ) -> T:
        while True:
            with self._lock:
                entry = self._entries.get(key)
                now = self._clock()
                if (
                    entry is not None
                    and (now - entry.stored_at) < ttl_s
                    and entry.version == version
                ):
                    return copy.deepcopy(entry.value)

                fut = self._in_flight.get(key)
                if fut is None:
                    fut = concurrent.futures.Future()
                    self._in_flight[key] = fut
                    is_leader = True
                else:
                    is_leader = False

            if not is_leader:
                try:
                    fut.result(timeout=35.0)
                except TimeoutError:
                    # Wait at most 35 s, then compute on its own
                    val = compute()
                    with self._lock:
                        self._set_entry(key, val, version)
                    return copy.deepcopy(val)

                with self._lock:
                    entry = self._entries.get(key)
                    now = self._clock()
                    if (
                        entry is not None
                        and (now - entry.stored_at) < ttl_s
                        and entry.version == version
                    ):
                        return copy.deepcopy(entry.value)
                continue

            # Leader executes compute()
            try:
                val = compute()
            except BaseException as exc:
                with self._lock:
                    self._in_flight.pop(key, None)
                fut.set_exception(exc)
                raise
            else:
                with self._lock:
                    self._set_entry(key, val, version)
                    self._in_flight.pop(key, None)
                fut.set_result(val)
                return copy.deepcopy(val)

    def _set_entry(self, key: tuple[str, str], value: Any, version: Any) -> None:
        if key not in self._entries and len(self._entries) >= self._max_entries and self._entries:
            oldest_key = min(self._entries, key=lambda k: self._entries[k].stored_at)
            del self._entries[oldest_key]
        if self._max_entries > 0:
            self._entries[key] = _CacheEntry(
                value=copy.deepcopy(value),
                stored_at=self._clock(),
                version=version,
            )

    def invalidate(self, owner: str) -> None:
        with self._lock:
            to_del = [k for k in self._entries if k[0] == owner]
            for k in to_del:
                del self._entries[k]
