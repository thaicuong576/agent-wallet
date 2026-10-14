"""Per-owner tenancy: one directory, one set of stores, one cached signer, one Gateway.

This is the only gateway module the API may import (design 5, 13). Callers outside core.gateway
get an address and a ready Gateway, never a key, a keystore or a path built from their input.

Layout (SQLite backend): DATA_DIR/owners/<0x lowercase address>/
    state.sqlite, audit.sqlite (chain and intents), gateway.lock.sqlite, keystore.json
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.audit.sink import SqliteAuditSink
from core.gateway.custody import (
    CustodyUnavailable,
    FileKeystoreStore,
    LazySigner,
    create_agent_wallet,
    normalize_owner,
)
from core.gateway.gateway import Gateway
from core.gateway.intents import IntentStore
from core.gateway.lock import GatewayLock
from core.gateway.policy import EnginePolicy
from core.gateway.txbuilder import AaveTxBuilder, RpcCall
from core.policy.ips import LoadedIps
from core.runner.ledger import AuditLedger
from core.runner.portfolio import PortfolioReader
from core.tiers.store import TierStore
from core.tiers.view import TierView


class TenantNotFound(Exception):
    """The owner has no agent wallet (and creation was not requested)."""


@dataclass(frozen=True)
class TenantPaths:
    root: Path
    state: Path
    audit: Path
    lock: Path
    keystore: Path


@dataclass
class TenantSettings:
    """Process-wide inputs a tenant needs. Nothing here is per request."""

    data_dir: Path
    ips_for: Callable[[str, str | None], LoadedIps]  # (owner, preset_id) -> IPS for that owner
    keystore_secret: str | None = None  # unset or short: reads work, create and sign fail closed
    kdf: str = "scrypt"
    simulator: Any = None
    risk_inputs: Callable[[], Mapping[str, Any] | None] | None = None
    sender: Callable[[bytes], Any] | None = None  # broadcast hook handed to the signer
    live: bool = False  # live: no gas-estimate fallback in the tx builder
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


@dataclass
class Tenant:
    owner: str
    agent_address: str
    gateway: Gateway = field(repr=False)
    tier_view: TierView = field(repr=False)
    paths: TenantPaths
    _lock: GatewayLock = field(repr=False)
    _ips_for: Callable[[str, str | None], LoadedIps] = field(repr=False)
    _policy_inputs: Callable[[], Any] = field(repr=False)

    def reload_policy(self, preset_id: str) -> str:
        """Swap the owner's policy under the gateway lock; return the new ips_hash."""
        ips = self._ips_for(self.owner, preset_id)
        policy = EnginePolicy(ips, *self._policy_inputs())
        with self._lock.held():
            self.gateway.policy = policy
            self.gateway.ips_hash = ips.hash
        return ips.hash


class TenantRegistry:
    def __init__(self, settings: TenantSettings, rpc: RpcCall, max_open: int = 32) -> None:
        self._settings = settings
        self._rpc = rpc
        self._max_open = max(1, max_open)
        self._owners_root = Path(settings.data_dir) / "owners"
        self._keystores = FileKeystoreStore(self._owners_root)
        self._cache: OrderedDict[str, Tenant] = OrderedDict()
        self._guard = threading.RLock()

    def _paths(self, owner: str) -> TenantPaths:
        addr = normalize_owner(owner)
        root = self._owners_root / addr
        if not root.resolve().is_relative_to(self._owners_root.resolve()):
            raise ValueError("invalid owner address")
        return TenantPaths(
            root=root,
            state=root / "state.sqlite",
            audit=root / "audit.sqlite",
            lock=root / "gateway.lock.sqlite",
            keystore=root / "keystore.json",
        )

    def exists(self, owner: str) -> bool:
        return self._keystores.exists(normalize_owner(owner))

    def open(self, owner: str, *, create: bool = False) -> Tenant:
        addr = normalize_owner(owner)
        with self._guard:
            cached = self._cache.get(addr)
            if cached is not None:
                self._cache.move_to_end(addr)
                return cached
            tenant = self._build(addr, create)
            self._cache[addr] = tenant
            while len(self._cache) > self._max_open:
                self._cache.popitem(last=False)  # eviction drops the signer reference
            return tenant

    def _build(self, addr: str, create: bool) -> Tenant:
        s = self._settings
        if not self._keystores.exists(addr):
            if not create:
                raise TenantNotFound("no agent wallet for this owner")
            if s.keystore_secret is None:
                raise CustodyUnavailable("keystore secret is unset or shorter than 32 bytes")
            create_agent_wallet(self._keystores, addr, s.keystore_secret, s.kdf)

        paths = self._paths(addr)
        signer = LazySigner(self._keystores, addr, s.keystore_secret, sender=s.sender)
        wallet = signer.address

        store = TierStore(paths.state)
        audit = SqliteAuditSink(paths.audit)
        ledger = AuditLedger(paths.audit)
        lock = GatewayLock(paths.lock)
        intents = IntentStore(paths.audit)
        portfolio = PortfolioReader(self._rpc, wallet, ledger)
        ips = s.ips_for(addr, None)

        def policy_inputs() -> tuple[Callable[[], Any], Any, Any]:
            return (
                lambda: portfolio.read(s.clock()).state,
                s.risk_inputs,
                s.clock,
            )

        gateway = Gateway(
            policy=EnginePolicy(ips, *policy_inputs()),
            simulator=s.simulator,
            signer=signer,
            audit_sink=audit,
            clock=s.clock,
            ips_hash=ips.hash,
            tx_builder=AaveTxBuilder(self._rpc, wallet, allow_estimate_fallback=not s.live),
            tier_view=TierView(store),
            lock=lock,
            intent_store=intents,
        )
        return Tenant(
            owner=addr,
            agent_address=wallet,
            gateway=gateway,
            tier_view=TierView(store),
            paths=paths,
            _lock=lock,
            _ips_for=s.ips_for,
            _policy_inputs=policy_inputs,
        )
