"""Tests for ReadCache: TTL, version invalidation, single flight, copies, and capacity cap."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from api.readcache import ReadCache


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def test_hit_within_ttl_computes_once_and_after_ttl_computes_again() -> None:
    clock = FakeClock(100.0)
    cache = ReadCache(clock=clock)
    calls = 0

    def compute() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"calls": calls}

    key = ("0xowner1", "portfolio")

    # First call: computes
    res1 = cache.get_or_compute(key, version=1, ttl_s=10.0, compute=compute)
    assert res1 == {"calls": 1}
    assert calls == 1

    # Second call at same time: hits cache
    res2 = cache.get_or_compute(key, version=1, ttl_s=10.0, compute=compute)
    assert res2 == {"calls": 1}
    assert calls == 1

    # Advance clock by 5s (< 10s TTL): hits cache
    clock.advance(5.0)
    res3 = cache.get_or_compute(key, version=1, ttl_s=10.0, compute=compute)
    assert res3 == {"calls": 1}
    assert calls == 1

    # Advance clock by 6s (total 11s elapsed > 10s TTL): expires and recomputes
    clock.advance(6.0)
    res4 = cache.get_or_compute(key, version=1, ttl_s=10.0, compute=compute)
    assert res4 == {"calls": 2}
    assert calls == 2


def test_version_change_forces_recompute() -> None:
    clock = FakeClock(100.0)
    cache = ReadCache(clock=clock)
    calls = 0

    def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    key = ("0xowner1", "portfolio")

    # Version 1 computes
    assert cache.get_or_compute(key, version=1, ttl_s=10.0, compute=compute) == 1
    assert calls == 1

    # Version 1 hits cache
    assert cache.get_or_compute(key, version=1, ttl_s=10.0, compute=compute) == 1
    assert calls == 1

    # Version 2 forces recompute even with 0 seconds elapsed
    assert cache.get_or_compute(key, version=2, ttl_s=10.0, compute=compute) == 2
    assert calls == 2


def test_two_owners_never_share_entry_and_invalidate_drops_only_that_owner() -> None:
    cache = ReadCache()

    calls_a = 0
    calls_b = 0

    def compute_a() -> str:
        nonlocal calls_a
        calls_a += 1
        return f"data_a_{calls_a}"

    def compute_b() -> str:
        nonlocal calls_b
        calls_b += 1
        return f"data_b_{calls_b}"

    key_a = ("0xowner_a", "agent")
    key_b = ("0xowner_b", "agent")

    # Both owners compute their own entries
    assert cache.get_or_compute(key_a, version=1, ttl_s=10.0, compute=compute_a) == "data_a_1"
    assert cache.get_or_compute(key_b, version=1, ttl_s=10.0, compute=compute_b) == "data_b_1"
    assert calls_a == 1
    assert calls_b == 1

    # Second read: hits cache for both
    assert cache.get_or_compute(key_a, version=1, ttl_s=10.0, compute=compute_a) == "data_a_1"
    assert cache.get_or_compute(key_b, version=1, ttl_s=10.0, compute=compute_b) == "data_b_1"
    assert calls_a == 1
    assert calls_b == 1

    # Invalidate owner A only
    cache.invalidate("0xowner_a")

    # Owner A must recompute
    assert cache.get_or_compute(key_a, version=1, ttl_s=10.0, compute=compute_a) == "data_a_2"
    assert calls_a == 2

    # Owner B still hits cache
    assert cache.get_or_compute(key_b, version=1, ttl_s=10.0, compute=compute_b) == "data_b_1"
    assert calls_b == 1


def test_single_flight_eight_threads_call_compute_once() -> None:
    cache = ReadCache()
    compute_count = 0
    start_barrier = threading.Barrier(8)

    def slow_compute() -> dict[str, str]:
        nonlocal compute_count
        compute_count += 1
        time.sleep(0.05)
        return {"result": "ok"}

    def worker() -> dict[str, str]:
        start_barrier.wait()
        return cache.get_or_compute(
            ("0xowner", "slow"), version=1, ttl_s=10.0, compute=slow_compute
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker) for _ in range(8)]
        results = [f.result() for f in futures]

    assert len(results) == 8
    assert all(r == {"result": "ok"} for r in results)
    assert compute_count == 1


def test_single_flight_exception_reaches_all_waiters_and_is_not_cached() -> None:
    cache = ReadCache()
    compute_count = 0
    start_barrier = threading.Barrier(8)

    def failing_compute() -> None:
        nonlocal compute_count
        compute_count += 1
        time.sleep(0.05)
        raise RuntimeError("rpc-failure")

    def worker() -> None:
        start_barrier.wait()
        cache.get_or_compute(("0xowner", "fail"), version=1, ttl_s=10.0, compute=failing_compute)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker) for _ in range(8)]
        errors = []
        for f in futures:
            with pytest.raises(RuntimeError) as exc_info:
                f.result()
            errors.append(str(exc_info.value))

    assert len(errors) == 8
    assert all(e == "rpc-failure" for e in errors)
    assert compute_count == 1

    # Verify exception is not cached: subsequent call succeeds if compute succeeds
    def ok_compute() -> str:
        return "recovered"

    res = cache.get_or_compute(("0xowner", "fail"), version=1, ttl_s=10.0, compute=ok_compute)
    assert res == "recovered"


def test_returned_values_are_copies() -> None:
    cache = ReadCache()
    orig = {"tokens": ["USDC", "WETH"], "limits": {"max": 100}}

    res1 = cache.get_or_compute(("0xowner", "copy_test"), 1, 10.0, lambda: orig)
    assert res1 == orig

    # Mutate returned structure
    res1["tokens"].append("DAI")
    res1["limits"]["max"] = 999

    # Next call returns fresh unmutated copy
    res2 = cache.get_or_compute(("0xowner", "copy_test"), 1, 10.0, lambda: orig)
    assert res2 == {"tokens": ["USDC", "WETH"], "limits": {"max": 100}}
    assert res1 is not res2
    assert res1["tokens"] is not res2["tokens"]


def test_cache_max_entries_cap_drops_oldest() -> None:
    clock = FakeClock(10.0)
    cache = ReadCache(max_entries=3, clock=clock)

    clock.time = 10.0
    cache.get_or_compute(("o1", "k1"), 1, 100.0, lambda: "v1")
    clock.time = 20.0
    cache.get_or_compute(("o2", "k2"), 1, 100.0, lambda: "v2")
    clock.time = 30.0
    cache.get_or_compute(("o3", "k3"), 1, 100.0, lambda: "v3")

    assert len(cache) == 3

    # Add 4th item at t=40.0; oldest ("o1", "k1" at t=10.0) should be dropped
    clock.time = 40.0
    cache.get_or_compute(("o4", "k4"), 1, 100.0, lambda: "v4")

    assert len(cache) == 3

    # Re-reading o1 should recompute because it was dropped
    recomputed = False

    def probe() -> str:
        nonlocal recomputed
        recomputed = True
        return "v1_new"

    clock.time = 45.0
    res = cache.get_or_compute(("o1", "k1"), 1, 100.0, probe)
    assert recomputed is True
    assert res == "v1_new"

    # Reading o3 should hit cache (not recomputed)
    recomputed_o3 = False

    def probe_o3() -> str:
        nonlocal recomputed_o3
        recomputed_o3 = True
        return "v3_new"

    res3 = cache.get_or_compute(("o3", "k3"), 1, 100.0, probe_o3)
    assert recomputed_o3 is False
    assert res3 == "v3"
