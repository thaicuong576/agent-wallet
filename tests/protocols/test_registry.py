"""Protocol registry, token registry and valuation (ticket D0)."""

from __future__ import annotations

import copy
import uuid
from decimal import Decimal

import eth_abi
import pytest

from core.agent.planner import PLANNER_SYSTEM_PROMPT, render_system_prompt
from core.gateway import Gateway
from core.gateway.audit import InMemoryAuditSink
from core.gateway.txbuilder import TxBuildError, build_calls
from core.protocols import addresses, registry, tokens
from core.protocols.aave import AAVE_V3_POOL, AAVE_V3_USDC
from core.protocols.registry import BuildContext, ProtocolRegistry
from core.protocols.tokens import PriceUnavailable, usd_value
from core.runner.prices import MAINNET_REFERENCE_PRICES

WALLET = "0x9D1Ae26890c683a52e73A504091b090f7689373a"


def order(**over):
    base = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-10-16T09:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "1000000", "decimals": 6},
        "recipient": "self",
    }
    base.update(over)
    return base


# ----------------------------------------------------------------- registry


def test_aave_registers_itself_and_wallet_comes_first():
    ids = [s.id for s in registry.all_specs()]
    assert ids[:2] == ["wallet", "aave-v3"]
    spec = registry.get("aave-v3")
    assert spec.category == "lending" and not spec.paper
    assert registry.by_category("lending") == [spec]
    assert spec.spenders == frozenset({AAVE_V3_POOL})


def test_unknown_protocol_is_not_found_and_preflight_denies():
    assert registry.get("nope") is None
    hits = registry.preflight(order(protocol="morpho-blue"))
    assert [h["code"] for h in hits] == ["ACCESS.PROTOCOL_NOT_ENABLED"]


def test_duplicate_registration_is_refused():
    reg = ProtocolRegistry()
    spec = registry.get("aave-v3")
    reg.register(spec)
    with pytest.raises(ValueError):
        reg.register(spec)


def test_derived_constants_match_the_pre_registry_values():
    assert {"aave-v3", "wallet"}.issubset(addresses.SUPPORTED_PROTOCOLS)
    assert {"USDC", "ETH"}.issubset(addresses.SUPPORTED_TOKENS)
    assert {"approve", "supply", "withdraw", "mint", "transfer"}.issubset(
        addresses.SUPPORTED_FUNCTIONS
    )
    assert addresses.PROTOCOL_ADDRESSES["aave-v3"] == addresses.AAVE_V3_POOL
    assert addresses.TOKEN_ADDRESSES["USDC"] == addresses.AAVE_V3_USDC


def test_market_not_listed_by_the_spec_is_denied():
    hits = registry.preflight(order(market="usdc-wbtc"))
    assert [h["code"] for h in hits] == ["ACCESS.MARKET_NOT_ALLOWED"]


def test_builder_dispatches_to_the_spec_and_refuses_unregistered_protocols():
    calls = build_calls(order(), WALLET)
    assert calls[0].to == AAVE_V3_POOL
    with pytest.raises(TxBuildError, match="not enabled"):
        build_calls(order(protocol="morpho-blue"), WALLET)


def test_approve_is_bound_to_the_spec_spender_and_exact_amount():
    spec = registry.get("aave-v3")
    (call,) = spec.build_calls(
        order(function="approve", recipient="aave_pool"), BuildContext(WALLET)
    )
    assert call.to == AAVE_V3_USDC
    spender, amount = eth_abi.decode(["address", "uint256"], bytes.fromhex(call.data[10:]))
    assert spender.lower() in {s.lower() for s in spec.spenders}
    assert amount == 1_000_000


# ----------------------------------------------------------------- gateway


class AllowPolicy:
    ips_hash = "0" * 64

    def evaluate(self, o):
        return {
            "schema_version": 1,
            "order_id": o["order_id"],
            "decision": "allow",
            "rules_hit": [],
            "reason": "ok",
            "ips_hash": self.ips_hash,
            "decided_at": "2026-10-16T09:00:00Z",
        }


def test_gateway_denies_an_allowed_order_for_an_unregistered_protocol_and_signs_nothing():
    from core.signer import FakeSigner

    signer = FakeSigner()
    gw = Gateway(
        policy=AllowPolicy(),
        signer=signer,
        audit_sink=InMemoryAuditSink(),
        tx_builder=lambda o: [{"to": "0x" + "22" * 20, "data": "0x", "chainId": 84532}],
    )
    rec = gw.propose(order(protocol="moonwell"))
    assert rec["outcome"] == "denied"
    assert [h["code"] for h in rec["decision"]["rules_hit"]] == ["ACCESS.PROTOCOL_NOT_ENABLED"]
    assert signer.calls == []


# ----------------------------------------------------------------- tokens


def test_data_only_tokens_are_listed_but_have_no_price():
    for symbol in ("WETH", "WBTC", "cbETH", "LINK", "USDbC"):
        assert tokens.get_token(symbol) is not None
        with pytest.raises(PriceUnavailable):
            usd_value(symbol, 10**6)
    assert MAINNET_REFERENCE_PRICES["USDC"] == "1.00"
    assert MAINNET_REFERENCE_PRICES["USDT"] == "1.00"


def test_stable_value_and_peg_band():
    assert usd_value("USDC", 2_500_000) == Decimal("2.50")
    with pytest.raises(PriceUnavailable):
        usd_value("USDC", 1, observed_price="0.50")
    assert usd_value("USDC", 1_000_000, observed_price="0.999") == Decimal("1.00")
    assert usd_value("USDT", 2_000_000) == Decimal("2.00")


def test_oracle_token_reads_once_and_fails_closed(monkeypatch):
    monkeypatch.setitem(
        tokens._TOKENS,
        "WETH",
        __import__("dataclasses").replace(tokens._TOKENS["WETH"], enabled=True),
    )
    seen = []

    def rpc(method, params):
        seen.append((method, params))
        return hex(2_500 * 10**8)

    assert usd_value("WETH", 10**18, rpc=rpc) == Decimal("2500")
    assert len(seen) == 1 and seen[0][1][0]["to"] == tokens.AAVE_ORACLE

    def broken(method, params):
        raise RuntimeError("rpc down")

    with pytest.raises(PriceUnavailable):
        usd_value("WETH", 10**18, rpc=broken)
    with pytest.raises(PriceUnavailable):
        usd_value("WETH", 10**18, rpc=lambda m, p: "0x0")
    with pytest.raises(PriceUnavailable):
        usd_value("WETH", 10**18)  # no reader


def test_native_eth_has_no_price():
    with pytest.raises(PriceUnavailable):
        usd_value("ETH", 10**18)


# ----------------------------------------------------------------- planner prompt


def test_prompt_is_generated_from_the_registry():
    assert '"aave-v3"' in PLANNER_SYSTEM_PROMPT
    assert "mint" not in PLANNER_SYSTEM_PROMPT and "transfer" not in PLANNER_SYSTEM_PROMPT
    assert "Aave v3" in PLANNER_SYSTEM_PROMPT
    # A protocol the IPS does not enable is not offered
    assert "aave-v3" not in render_system_prompt(["morpho-blue"]).split("Enabled protocols")[0]


def test_order_schema_never_carries_an_address_or_free_text():
    import json
    from pathlib import Path

    schema = json.loads(Path("contracts/schemas/order.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert "market" in schema["properties"]
    assert copy.deepcopy(schema["$defs"]["Market"])["enum"] == ["usdc-wbtc", "usdc-cbeth"]
