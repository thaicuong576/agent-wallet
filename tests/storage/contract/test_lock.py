"""GatewayLockProvider contract: one holder at a time, timeout fails closed."""

from __future__ import annotations

import threading

import pytest

from core.gateway.exceptions import GatewayBusy
from core.storage.interfaces import GatewayLockProvider
from tests.storage.contract.conftest import OWNER_A, OWNER_B


def test_adapter_satisfies_protocol(stores) -> None:
    assert isinstance(stores.lock, GatewayLockProvider)


def test_exclusion(backend, stores) -> None:
    other = backend.open(OWNER_A)  # a second handle on the same owner's lock
    inside = threading.Event()
    release = threading.Event()
    entered: list[str] = []

    def holder() -> None:
        with stores.lock.held():
            inside.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert inside.wait(5)
    try:
        with pytest.raises(GatewayBusy):
            with other.lock.held(timeout_s=0.2):
                entered.append("entered")
    finally:
        release.set()
        t.join(5)
    assert entered == []


def test_timeout_raises_gateway_busy(backend, stores) -> None:
    other = backend.open(OWNER_A)
    with stores.lock.held():
        with pytest.raises(GatewayBusy):
            with other.lock.held(timeout_s=0.1):
                pytest.fail("a second holder must not enter")


def test_released_on_exception(stores) -> None:
    with pytest.raises(RuntimeError):
        with stores.lock.held():
            raise RuntimeError("boom")
    with stores.lock.held(timeout_s=0.5):
        pass


def test_locks_are_per_owner(backend, stores) -> None:
    other = backend.open(OWNER_B)
    with stores.lock.held():
        with other.lock.held(timeout_s=0.5):
            pass
