"""Unit tests for the signer adapter and simulate-before-sign."""

import logging
from typing import Any

import pytest
from eth_account import Account

from core.signer import (
    FakeSigner,
    GuardedSigner,
    LocalSigner,
    Signer,
    SignerKeyError,
    SimulationFailed,
    TxReceiptStub,
)
from core.simulate import (
    RpcSimulator,
    SimulationResult,
    Simulator,
)


def test_fake_signer_records_calls_and_returns_deterministic_hash():
    signer = FakeSigner()
    assert signer.address == "0x1111111111111111111111111111111111111111"
    assert len(signer.calls) == 0

    tx1 = {"to": "0x2222222222222222222222222222222222222222", "value": 100}
    receipt1 = signer.sign_and_send(tx1)

    assert isinstance(receipt1, TxReceiptStub)
    assert receipt1.tx_hash.startswith("0x")
    assert len(signer.calls) == 1
    assert signer.calls[0] == tx1

    tx2 = {"to": "0x3333333333333333333333333333333333333333", "value": 200}
    receipt2 = signer.sign_and_send(tx2)

    assert len(signer.calls) == 2
    assert signer.calls[1] == tx2
    assert receipt1.tx_hash != receipt2.tx_hash

    # Repeating exact sequence on a fresh FakeSigner produces identical deterministic hashes
    fresh = FakeSigner()
    r1 = fresh.sign_and_send(tx1)
    r2 = fresh.sign_and_send(tx2)
    assert r1.tx_hash == receipt1.tx_hash
    assert r2.tx_hash == receipt2.tx_hash


def test_guarded_signer_never_signs_on_failed_simulation():
    inner = FakeSigner()

    class RejectingSimulator:
        def simulate(self, tx: dict[str, Any]) -> SimulationResult:
            return SimulationResult(
                ok=False,
                error="execution reverted: InsufficientBalance",
                gas_used=None,
                raw={"status": "0x0"},
            )

    guarded = GuardedSigner(inner, RejectingSimulator())

    tx = {"to": "0x4444444444444444444444444444444444444444", "value": 500}
    with pytest.raises(SimulationFailed) as exc_info:
        guarded.sign_and_send(tx)

    assert "InsufficientBalance" in str(exc_info.value)
    assert exc_info.value.result is not None
    assert exc_info.value.result.ok is False
    # Crucial guarantee: inner signer was never called
    assert len(inner.calls) == 0


def test_guarded_signer_signs_when_simulation_ok():
    inner = FakeSigner()

    class PassingSimulator:
        def simulate(self, tx: dict[str, Any]) -> SimulationResult:
            return SimulationResult(
                ok=True,
                error=None,
                gas_used=21000,
                raw={"status": "0x1"},
            )

    guarded = GuardedSigner(inner, PassingSimulator())
    tx = {"to": "0x5555555555555555555555555555555555555555", "value": 1000}
    receipt = guarded.sign_and_send(tx)

    assert isinstance(receipt, TxReceiptStub)
    assert len(inner.calls) == 1
    assert inner.calls[0] == tx


def test_signer_repr_str_exception_log_never_contain_test_key(monkeypatch, caplog):
    # Dynamically generate a throwaway key at test time
    account = Account.create()
    test_key_hex = account.key.hex()
    test_key_bare = test_key_hex[2:] if test_key_hex.startswith("0x") else test_key_hex

    monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", test_key_hex)

    with caplog.at_level(logging.DEBUG):
        signer = LocalSigner()
        logging.getLogger("core.signer").info("Created signer for %s", signer.address)

    # Check repr and str of LocalSigner
    assert test_key_hex not in repr(signer)
    assert test_key_bare not in repr(signer)
    assert test_key_hex not in str(signer)
    assert test_key_bare not in str(signer)

    # Check GuardedSigner repr and str
    guarded = GuardedSigner(signer, RpcSimulator("http://mock-rpc"))
    assert test_key_hex not in repr(guarded)
    assert test_key_bare not in repr(guarded)
    assert test_key_hex not in str(guarded)
    assert test_key_bare not in str(guarded)

    # Check captured logs
    assert test_key_hex not in caplog.text
    assert test_key_bare not in caplog.text

    # Check exception raised during signing
    with pytest.raises(Exception) as exc_info:
        # Invalid transaction structure (missing required fields for raw signing)
        signer.sign_and_send({"bad_field": 123})

    assert test_key_hex not in str(exc_info.value)
    assert test_key_bare not in str(exc_info.value)


def test_missing_key_error_does_not_echo_value(monkeypatch):
    monkeypatch.delenv("AGENT_WALLET_PRIVATE_KEY", raising=False)

    with pytest.raises(SignerKeyError) as exc_info:
        LocalSigner()

    msg = str(exc_info.value)
    assert "Missing AGENT_WALLET_PRIVATE_KEY" in msg


def test_malformed_key_error_does_not_echo_value(monkeypatch):
    secret_bad_payload = "super_secret_malformed_payload_abcdef123456"
    monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", secret_bad_payload)

    with pytest.raises(SignerKeyError) as exc_info:
        LocalSigner()

    msg = str(exc_info.value)
    assert secret_bad_payload not in msg
    assert "Malformed AGENT_WALLET_PRIVATE_KEY" in msg

    short_bad_payload = "0x1234"
    monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", short_bad_payload)

    with pytest.raises(SignerKeyError) as exc_info:
        LocalSigner()

    msg2 = str(exc_info.value)
    assert short_bad_payload not in msg2
    assert "Malformed AGENT_WALLET_PRIVATE_KEY" in msg2


def test_local_signer_signs_and_sends_with_injected_sender(monkeypatch):
    account = Account.create()
    test_key_hex = account.key.hex()
    monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", test_key_hex)

    sent_payloads: list[Any] = []

    def fake_sender(raw_tx: bytes) -> str:
        sent_payloads.append(raw_tx)
        return "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

    signer = LocalSigner(sender=fake_sender)
    assert signer.address == account.address

    tx = {
        "to": "0x1111111111111111111111111111111111111111",
        "value": 0,
        "gas": 21000,
        "gasPrice": 1000000000,
        "nonce": 0,
        "chainId": 84532,
    }
    receipt = signer.sign_and_send(tx)

    assert receipt.tx_hash == "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    assert len(sent_payloads) == 1
    assert isinstance(sent_payloads[0], (bytes, bytearray))


def test_rpc_simulator_eth_simulate_v1_success():
    def fake_transport(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["method"] == "eth_simulateV1"
        assert payload["params"][0]["validation"] is True
        return {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": [
                {
                    "gasUsed": "0x5208",
                    "status": "0x1",
                    "calls": [
                        {
                            "status": "0x1",
                            "gasUsed": "0x5208",
                            "returnData": "0x",
                        }
                    ],
                }
            ],
        }

    sim = RpcSimulator("http://mock-rpc", transport=fake_transport)
    tx = {
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x2222222222222222222222222222222222222222",
        "value": 1,
    }
    res = sim.simulate(tx)

    assert res.ok is True
    assert res.error is None
    assert res.gas_used == 21000


def test_rpc_simulator_eth_simulate_v1_revert():
    def fake_transport(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": [
                {
                    "calls": [
                        {
                            "status": "0x0",
                            "gasUsed": "0x5c9b",
                            "error": {
                                "code": 3,
                                "message": "execution reverted",
                                "data": "0x",
                            },
                        }
                    ]
                }
            ],
        }

    sim = RpcSimulator("http://mock-rpc", transport=fake_transport)
    res = sim.simulate({"to": "0x2222222222222222222222222222222222222222"})

    assert res.ok is False
    assert res.error == "execution reverted"
    assert res.gas_used == 23707


def test_rpc_simulator_fallback_to_eth_call_on_method_not_found():
    calls_made: list[str] = []

    def fake_transport(payload: dict[str, Any]) -> dict[str, Any]:
        method = payload["method"]
        calls_made.append(method)
        if method == "eth_simulateV1":
            return {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {
                    "code": -32601,
                    "message": "the method eth_simulateV1 does not exist/is not available",
                },
            }
        if method == "eth_call":
            return {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": "0x",
            }
        raise ValueError(f"Unexpected method: {method}")

    sim = RpcSimulator("http://mock-rpc", transport=fake_transport)
    res = sim.simulate({"to": "0x2222222222222222222222222222222222222222"})

    assert calls_made == ["eth_simulateV1", "eth_call"]
    assert res.ok is True
    assert res.error is None


def test_rpc_simulator_fallback_to_eth_call_revert():
    calls_made: list[str] = []

    def fake_transport(payload: dict[str, Any]) -> dict[str, Any]:
        method = payload["method"]
        calls_made.append(method)
        if method == "eth_simulateV1":
            return {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                },
            }
        if method == "eth_call":
            return {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {
                    "code": -32003,
                    "message": "EVM error: OutOfFunds",
                },
            }
        raise ValueError(f"Unexpected method: {method}")

    sim = RpcSimulator("http://mock-rpc", transport=fake_transport)
    res = sim.simulate({"to": "0x2222222222222222222222222222222222222222"})

    assert calls_made == ["eth_simulateV1", "eth_call"]
    assert res.ok is False
    assert "OutOfFunds" in (res.error or "")


def test_protocols_and_stubs():
    fake = FakeSigner()
    assert isinstance(fake, Signer)

    account = Account.create()
    local = LocalSigner(sender=None, private_key=account.key.hex())
    assert isinstance(local, Signer)

    rpc_sim = RpcSimulator("http://mock-rpc")
    assert isinstance(rpc_sim, Simulator)

    guarded = GuardedSigner(fake, rpc_sim)
    assert isinstance(guarded, Signer)
    assert guarded.inner_signer is fake
    assert guarded.simulator is rpc_sim
