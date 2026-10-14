"""Global stop: read inside the gateway lock, again after simulation and before signing."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from core.gateway import DEFAULT_IPS_HASH, Gateway, InMemoryAuditSink
from core.gateway.globalstop import file_flag
from core.gateway.intents import IntentStore
from core.signer import FakeSigner
from core.simulate import SimulationResult

OWNER = "0x00000000000000000000000000000000000000a1"


class OriginPolicy:
    """Allow-all policy that records the keyword arguments it receives."""

    def __init__(self) -> None:
        self.ips_hash = DEFAULT_IPS_HASH
        self.seen: list[dict[str, Any]] = []

    def evaluate(
        self,
        order: dict[str, Any],
        *,
        origin: str = "planner",
        tier: str = "T2",
        emergency_stop: bool = False,
        global_stop: bool = False,
    ) -> dict[str, Any]:
        self.seen.append({"origin": origin, "global_stop": global_stop})
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "decision": "allow",
            "rules_hit": [],
            "reason": "ok",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T09:00:01Z",
        }


class FlagSimulator:
    """Simulator that runs a hook after simulating (to flip the flag mid-propose)."""

    def __init__(self, after: Any = None) -> None:
        self.after = after
        self.calls: list[dict[str, Any]] = []

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        self.calls.append(tx)
        if self.after is not None:
            self.after()
        return SimulationResult(ok=True, error=None, gas_used=50_000)


def _builder(order: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
    return [{"to": "0x2222222222222222222222222222222222222222", "data": "0x", "chainId": 84532}]


def _order(function: str = "supply", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": function,
        "token": "USDC",
        "amount": {"value": "80000000", "decimals": 6},
        "recipient": "self",
    }
    base.update(overrides)
    return base


def _transfer_order() -> dict[str, Any]:
    return _order("transfer", proposed_by="owner", protocol="wallet", recipient="owner")


def _gateway(
    flag: Any, simulator: Any = None, **kwargs: Any
) -> tuple[Gateway, FakeSigner, InMemoryAuditSink]:
    signer = FakeSigner()
    sink = InMemoryAuditSink()
    gw = Gateway(
        policy=kwargs.pop("policy", OriginPolicy()),
        simulator=simulator if simulator is not None else FlagSimulator(),
        signer=signer,
        audit_sink=sink,
        tx_builder=_builder,
        global_stop=flag,
        **kwargs,
    )
    return gw, signer, sink


def _codes(record: dict[str, Any]) -> list[str]:
    return [h["code"] for h in record["decision"]["rules_hit"]]


def test_flag_set_between_simulation_and_signing_blocks_signer() -> None:
    state = {"stopped": False}
    sim = FlagSimulator(after=lambda: state.update(stopped=True))
    gw, signer, _ = _gateway(lambda: state["stopped"], simulator=sim)
    rec = gw.propose(_order())
    assert len(sim.calls) == 1
    assert signer.calls == []
    assert rec["outcome"] == "denied"
    assert rec["execution"] is None
    assert _codes(rec) == ["TIER.GLOBAL_STOP"]


def test_blocked_order_writes_no_intent(tmp_path: Path) -> None:
    store = IntentStore(tmp_path / "audit.sqlite")
    state = {"stopped": False}
    sim = FlagSimulator(after=lambda: state.update(stopped=True))
    gw, signer, _ = _gateway(lambda: state["stopped"], simulator=sim, intent_store=store)
    order = _order()
    gw.propose(order)
    assert signer.calls == []
    assert store.get_intent(order["order_id"]) is None
    assert not store.unfinished(order["order_id"])


def test_global_stop_denies_at_start_without_simulating() -> None:
    sim = FlagSimulator()
    gw, signer, _ = _gateway(lambda: True, simulator=sim)
    rec = gw.propose(_order())
    assert rec["outcome"] == "denied"
    assert _codes(rec) == ["TIER.GLOBAL_STOP"]
    assert sim.calls == []
    assert signer.calls == []


def test_unreadable_flag_denies() -> None:
    def broken() -> bool:
        raise OSError("permission denied")

    gw, signer, _ = _gateway(broken)
    rec = gw.propose(_order())
    assert rec["outcome"] == "denied"
    assert _codes(rec) == ["TIER.GLOBAL_STOP"]
    assert signer.calls == []


def test_unreachable_settings_denies(tmp_path: Path) -> None:
    def unreachable() -> bool:
        raise ConnectionError("database unreachable")

    flag = file_flag(tmp_path / "GLOBAL_STOP", {}, settings_reader=unreachable)
    assert flag() is True
    gw, signer, _ = _gateway(flag)
    assert gw.propose(_order())["outcome"] == "denied"
    assert signer.calls == []


def test_settings_row_blocks_signer() -> None:
    row = {"global_stop": "0"}
    flag = file_flag(None, {}, settings_reader=lambda: row["global_stop"] == "1")
    gw, signer, _ = _gateway(flag)
    assert gw.propose(_order())["outcome"] == "executed"
    row["global_stop"] = "1"
    assert gw.propose(_order())["outcome"] == "denied"
    assert len(signer.calls) == 1


def test_file_flag_sources(tmp_path: Path) -> None:
    path = tmp_path / "GLOBAL_STOP"
    env: dict[str, str] = {}
    flag = file_flag(path, env)
    assert flag() is False
    path.write_text("", encoding="utf-8")
    assert flag() is True
    path.unlink()
    assert flag() is False
    env["AGENT_WALLET_GLOBAL_STOP"] = "1"
    assert flag() is True
    env["AGENT_WALLET_GLOBAL_STOP"] = "0"
    assert flag() is False


def test_file_flag_unreadable_path_is_stopped(tmp_path: Path, monkeypatch: Any) -> None:
    def denied(path: Any, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("denied")

    flag = file_flag(tmp_path / "GLOBAL_STOP", {})
    assert flag() is False
    monkeypatch.setattr("core.gateway.globalstop.os.stat", denied)
    assert flag() is True


def test_default_reads_environment(monkeypatch: Any) -> None:
    monkeypatch.delenv("AGENT_WALLET_GLOBAL_STOP", raising=False)
    signer = FakeSigner()
    gw = Gateway(
        policy=OriginPolicy(),
        simulator=FlagSimulator(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=_builder,
    )
    assert gw.propose(_order())["outcome"] == "executed"
    monkeypatch.setenv("AGENT_WALLET_GLOBAL_STOP", "1")
    assert gw.propose(_order())["outcome"] == "denied"
    assert len(signer.calls) == 1


def test_global_stop_blocks_transfer() -> None:
    gw, signer, _ = _gateway(lambda: True, owner_address=OWNER)
    rec = gw.propose(_transfer_order(), origin="owner")
    assert rec["outcome"] == "denied"
    assert _codes(rec) == ["TIER.GLOBAL_STOP"]
    assert signer.calls == []


def test_owner_stop_does_not_block_transfer() -> None:
    gw, signer, _ = _gateway(lambda: False, owner_address=OWNER, emergency_stop=True, tier="T0")
    rec = gw.propose(_transfer_order(), origin="owner")
    assert rec["outcome"] == "executed"
    assert rec["emergency_stop"] is True
    assert len(signer.calls) == 1


def test_owner_stop_still_blocks_mint_and_planner_orders() -> None:
    gw, signer, _ = _gateway(lambda: False, emergency_stop=True)
    mint = _order("mint", proposed_by="owner", recipient="self")
    assert gw.propose(mint, origin="owner")["outcome"] == "denied"
    assert gw.propose(_order())["outcome"] == "denied"
    assert signer.calls == []


def test_global_stop_denial_is_retried_after_release() -> None:
    state = {"stopped": True}
    gw, signer, _ = _gateway(lambda: state["stopped"])
    order = _order()
    assert gw.propose(order)["outcome"] == "denied"
    state["stopped"] = False
    assert gw.propose(order)["outcome"] == "executed"
    assert len(signer.calls) == 1


def test_policy_receives_origin_and_global_stop() -> None:
    policy = OriginPolicy()
    gw, _, _ = _gateway(lambda: False, policy=policy, owner_address=OWNER)
    gw.propose(_order())
    gw.propose(_transfer_order(), origin="owner")
    assert [s["origin"] for s in policy.seen] == ["planner", "owner"]
    assert not any(s["global_stop"] for s in policy.seen)
