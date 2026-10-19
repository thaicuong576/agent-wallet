"""Protocol registry: one place that says what each protocol is and how it plugs in.

A protocol adapter registers a `ProtocolSpec`. The gateway tx builder, the planner prompt, the
portfolio reader and `GET /protocols` all read the registry, so adding a protocol does not touch
them. The registry never accepts an address, spender or calldata field from an order: those live
in the spec, in code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol


class Call(NamedTuple):
    """An executable Ethereum call object."""

    to: str
    data: str
    value: int = 0


class PositionLeg(NamedTuple):
    """One balance a protocol holds for the wallet, before valuation."""

    token: str
    location: str
    balance: int
    decimals: int


@dataclass(frozen=True)
class BuildContext:
    """Facts the gateway supplies to `build_calls`; never taken from the order."""

    wallet: str
    owner_address: str | None = None
    # JSON-RPC caller of the transaction builder, for specs that read the chain while building
    # (the Uniswap pool quote). None falls back to the spec's own default endpoint.
    rpc: Any = None


class PositionReader(Protocol):
    """What a spec's `positions` may use to read the chain."""

    def balance_of(self, token_address: str) -> int: ...

    def address_for(self, key: str, default: str) -> str: ...

    def call(self, to: str, data: str) -> str: ...

    def eth_call(self, to: str, data: str) -> str: ...


@dataclass(frozen=True)
class ProtocolSpec:
    id: str
    name: str
    category: str  # lending, dex, vault, lp, staking, restaking, perp, paper, wallet
    chain_id: int
    functions: frozenset[str]
    tokens: frozenset[str]
    spenders: frozenset[str]  # checksummed addresses an approve may name
    build_calls: Callable[[Mapping[str, Any], BuildContext], list[Call]]
    positions: Callable[[str, PositionReader], list[PositionLeg]]
    describe: Callable[[Mapping[str, Any]], str]
    paper: bool = False
    owner_functions: frozenset[str] = frozenset()  # never offered to the planner
    recipients: frozenset[str] = frozenset({"self"})
    markets: frozenset[str] = frozenset()
    summary: str = ""

    @property
    def planner_functions(self) -> frozenset[str]:
        return self.functions - self.owner_functions


class ProtocolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ProtocolSpec] = {}

    def register(self, spec: ProtocolSpec) -> None:
        if spec.id in self._specs:
            raise ValueError(f"protocol already registered: {spec.id}")
        self._specs[spec.id] = spec

    def get(self, protocol_id: str) -> ProtocolSpec | None:
        return self._specs.get(protocol_id)

    def all(self) -> list[ProtocolSpec]:
        return list(self._specs.values())

    def by_category(self, category: str) -> list[ProtocolSpec]:
        return [s for s in self._specs.values() if s.category == category]


REGISTRY = ProtocolRegistry()


def register(spec: ProtocolSpec) -> None:
    REGISTRY.register(spec)


def _ensure_loaded() -> None:
    # Adapters register themselves when imported.
    import core.protocols.aave  # noqa: F401
    import core.protocols.aave_stata  # noqa: F401
    import core.protocols.morpho_blue  # noqa: F401
    import core.protocols.uniswap_v3  # noqa: F401


def get(protocol_id: str) -> ProtocolSpec | None:
    _ensure_loaded()
    return REGISTRY.get(protocol_id)


def all_specs() -> list[ProtocolSpec]:
    _ensure_loaded()
    return REGISTRY.all()


def by_category(category: str) -> list[ProtocolSpec]:
    _ensure_loaded()
    return REGISTRY.by_category(category)


def preflight(order: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Registry-level checks that need no IPS or state. Returns rule hits (empty means pass).

    - protocol has no registered spec: ACCESS.PROTOCOL_NOT_ENABLED
    - market present but not listed by the spec, or missing when required: ACCESS.MARKET_NOT_ALLOWED
    """
    spec = get(str(order.get("protocol")))
    if spec is None:
        return [
            {
                "code": "ACCESS.PROTOCOL_NOT_ENABLED",
                "evidence": {"protocol": str(order.get("protocol"))},
            }
        ]
    market = order.get("market")
    if (market is not None and market not in spec.markets) or (
        spec.markets and market is None and order.get("function") != "approve"
    ):
        return [
            {
                "code": "ACCESS.MARKET_NOT_ALLOWED",
                "evidence": {"protocol": spec.id, "market": str(market)},
            }
        ]
    return []
