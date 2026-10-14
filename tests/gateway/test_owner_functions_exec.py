"""mint and transfer execute through the fake signer with origin='owner'."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.gateway import Gateway, InMemoryAuditSink
from core.gateway.txbuilder import AaveTxBuilder
from core.policy.engine import evaluate as engine_evaluate
from core.policy.ips import load_ips
from core.protocols.addresses import AAVE_V3_FAUCET, AAVE_V3_USDC
from core.signer import FakeSigner
from core.simulate import SimulationResult

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"
WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"
OWNER = "0x00000000000000000000000000000000000000a1"
NOW = datetime.fromtimestamp(1_700_000_000, tz=UTC)


class EnginePolicyWithOrigin:
    def __init__(self) -> None:
        self.ips = load_ips(EXAMPLES / "ips.valid.json")
        self.ips_hash = self.ips.hash

    def evaluate(
        self,
        order: dict[str, Any],
        *,
        origin: str = "planner",
        tier: str = "T2",
        emergency_stop: bool = False,
        global_stop: bool = False,
    ) -> dict[str, Any]:
        return engine_evaluate(
            self.ips,
            {},
            order,
            risk_inputs=None,
            origin=origin,
            tier=tier,
            emergency_stop=emergency_stop,
            global_stop=global_stop,
        )


class OkSimulator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        self.calls.append(tx)
        return SimulationResult(ok=True, error=None, gas_used=60_000)


def _rpc(balance: int = 10**18) -> Any:
    def rpc(method: str, params: list[Any]) -> Any:
        return {
            "eth_chainId": hex(84532),
            "eth_getBlockByNumber": {
                "timestamp": hex(1_700_000_000),
                "baseFeePerGas": hex(10_000_000_000),
            },
            "eth_getTransactionCount": hex(5),
            "eth_estimateGas": hex(50_000),
            "eth_getBalance": hex(balance),
        }[method]

    return rpc


def _order(function: str, token: str, value: str, decimals: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "owner",
        "protocol": "wallet" if function == "transfer" else "aave-v3",
        "function": function,
        "token": token,
        "amount": {"value": value, "decimals": decimals},
        "recipient": "owner" if function == "transfer" else "self",
    }


def _gateway(balance: int = 10**18) -> tuple[Gateway, FakeSigner, InMemoryAuditSink]:
    signer = FakeSigner(address=WALLET)
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=EnginePolicyWithOrigin(),
        simulator=OkSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=AaveTxBuilder(_rpc(balance), WALLET, clock=lambda: NOW),
        global_stop=lambda: False,
        owner_address=OWNER,
    )
    return gw, signer, sink


def test_mint_executes_via_faucet_with_owner_origin() -> None:
    gw, signer, sink = _gateway()
    rec = gw.propose(_order("mint", "USDC", "1000000000", 6), origin="owner")
    assert rec["outcome"] == "executed"
    assert rec["order"]["function"] == "mint"
    assert len(signer.calls) == 1
    tx = signer.calls[0]
    assert tx["to"].lower() == AAVE_V3_FAUCET.lower()
    assert tx["data"].startswith("0xc6c3bbe6")
    assert AAVE_V3_USDC.lower()[2:] in tx["data"].lower()
    assert WALLET.lower()[2:] in tx["data"].lower()
    assert len(sink.records) == 1


def test_transfer_usdc_resolves_owner_from_gateway() -> None:
    gw, signer, _ = _gateway()
    order = _order("transfer", "USDC", "500000000", 6)
    assert OWNER not in str(order)
    rec = gw.propose(order, origin="owner")
    assert rec["outcome"] == "executed"
    assert rec["order"]["function"] == "transfer"
    tx = signer.calls[0]
    assert tx["to"].lower() == AAVE_V3_USDC.lower()
    assert tx["data"].startswith("0xa9059cbb")
    assert OWNER[2:] in tx["data"].lower()


def test_transfer_eth_is_a_plain_value_transfer() -> None:
    gw, signer, _ = _gateway()
    rec = gw.propose(_order("transfer", "ETH", "100000000000000", 18), origin="owner")
    assert rec["outcome"] == "executed"
    tx = signer.calls[0]
    assert tx["to"].lower() == OWNER
    assert tx["data"] == "0x"
    assert tx["value"] == 100_000_000_000_000


def test_eth_transfer_keeps_gas_floor() -> None:
    # Balance equals the transfer value: nothing is left to pay the fee.
    value = 10**17
    gw, signer, _ = _gateway(balance=value)
    rec = gw.propose(_order("transfer", "ETH", str(value), 18), origin="owner")
    assert rec["outcome"] == "failed"
    assert "insufficient gas funds" in rec["execution"]["detail"]
    assert signer.calls == []


def test_transfer_without_owner_address_fails_and_signs_nothing() -> None:
    signer = FakeSigner(address=WALLET)
    gw = Gateway(
        policy=EnginePolicyWithOrigin(),
        simulator=OkSimulator(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=AaveTxBuilder(_rpc(), WALLET, clock=lambda: NOW),
        global_stop=lambda: False,
    )
    rec = gw.propose(_order("transfer", "USDC", "1000000", 6), origin="owner")
    assert rec["outcome"] == "failed"
    assert signer.calls == []


def test_planner_origin_mint_and_transfer_never_sign() -> None:
    gw, signer, sink = _gateway()
    for order in (
        _order("mint", "USDC", "1000000", 6),
        _order("transfer", "USDC", "1000000", 6),
    ):
        rec = gw.propose(order, origin="planner")
        assert rec["outcome"] == "denied"
    assert signer.calls == []
    assert len(sink.records) == 2
