"""Tests for policy engine purity: no network, no file I/O, determinism, and immutability."""

from __future__ import annotations

import builtins
import copy
import socket
from datetime import UTC, datetime
from typing import Any

import pytest

from core.policy.engine import evaluate
from core.policy.ips import LoadedIps


def test_engine_purity_blocked_socket_and_network(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evaluate completes successfully even when socket connection raises an error."""

    def _guarded_connect(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Network/socket connection attempted during pure evaluate!")

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)

    decision = evaluate(loaded_ips, valid_state, valid_order)
    assert decision["decision"] == "allow"


def test_engine_purity_blocked_file_opens(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evaluate completes successfully without attempting any filesystem file opens."""
    original_open = builtins.open

    def _guarded_open(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"File open attempted during pure evaluate: {args}")

    monkeypatch.setattr(builtins, "open", _guarded_open)
    try:
        decision = evaluate(loaded_ips, valid_state, valid_order)
        assert decision["decision"] == "allow"
    finally:
        monkeypatch.setattr(builtins, "open", original_open)


def test_engine_purity_deterministic_same_inputs_same_output(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Evaluate produces identical outputs for identical inputs and timestamp."""
    fixed_now = datetime(2026, 10, 16, 9, 0, 0, tzinfo=UTC)

    decision1 = evaluate(loaded_ips, valid_state, valid_order, now=fixed_now)
    decision2 = evaluate(loaded_ips, valid_state, valid_order, now=fixed_now)

    assert decision1 == decision2


def test_engine_purity_does_not_mutate_inputs(
    loaded_ips: LoadedIps,
    valid_state: dict[str, Any],
    valid_order: dict[str, Any],
) -> None:
    """Evaluate does not mutate any of the input objects."""
    state_copy = copy.deepcopy(valid_state)
    order_copy = copy.deepcopy(valid_order)

    evaluate(loaded_ips, valid_state, valid_order)

    assert valid_state == state_copy
    assert valid_order == order_copy
