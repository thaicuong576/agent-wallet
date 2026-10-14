"""Per-owner tenancy: one directory, one set of stores, one cached signer, one Gateway.

This is the only gateway module the API may import (design 5, 13). Callers outside core.gateway
get an address and a ready Gateway, never a key, a keystore or a path built from their input.

Every per-owner object is built from ``backend.open_tenant(owner)`` (a StorageBackend, design 5
and 13): SQLite files under DATA_DIR, or rows in Postgres. The gateway keeps its behaviour; only
the construction changed.

Layout (SQLite backend): DATA_DIR/owners/<0x lowercase address>/
    state.sqlite, audit.sqlite (chain and intents), gateway.lock.sqlite, webapp.sqlite
    (jobs, top-ups, policy selection), keystore.json
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.gateway.custody import (
    CustodyUnavailable,
    FileKeystoreStore,
    LazySigner,
    _stored_address,
    create_agent_wallet,
    normalize_owner,
)
from core.gateway.gateway import Gateway
from core.gateway.globalstop import file_flag
from core.gateway.intents import IntentStore
from core.gateway.lock import GatewayLock
from core.gateway.policy import EnginePolicy
from core.gateway.txbuilder import AaveTxBuilder, RpcCall
from core.policy.ips import LoadedIps
from core.runner.portfolio import PortfolioReader
from core.storage.interfaces import AuditStore, KeystoreStore, StorageBackend, TenantStores
from core.storage.sqlite.webapp import SqliteBackend


class TenantNotFound(Exception):
    """The owner has no agent wallet (and creation was not requested)."""


@dataclass(frozen=True)
class TenantPaths:
    root: Path
    state: Path
    audit: Path
    lock: Path
    keystore: Path


def sqlite_backend(
    data_dir: Path | str,
    *,
    auth: Callable[[], Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> SqliteBackend:
    """The SQLite StorageBackend. The intent store and the file keystore are gateway-internal, so
    they are supplied here; ``auth`` builds the shared auth store (the API passes its own)."""
    return SqliteBackend(
        data_dir, intents=IntentStore, keystore=FileKeystoreStore, auth=auth, env=env
    )


class AuditSinkView:
    """The sink surface the gateway, the verifier and the job views read, over any AuditStore.

    ``append`` and ``find_by_order_id`` pass through; ``records``, iteration and indexing are
    read-only views built from ``list``, so one class serves the SQLite and Postgres chains alike.
    """

    def __init__(self, store: AuditStore) -> None:
        self._store = store

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._store.append(record)

    def find_by_order_id(self, order_id: str) -> dict[str, Any] | None:
        return self._store.find_by_order_id(order_id)

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(self._store.iter_all())

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._store.iter_all())

    def __len__(self) -> int:
        return sum(1 for _ in self._store.iter_all())

    def __getitem__(self, seq: int) -> dict[str, Any]:
        if not isinstance(seq, int) or seq < 0:
            raise IndexError(seq)
        page = self._store.list(after_seq=seq - 1 if seq else None, limit=1)
        if not page or int(page[0]["seq"]) != seq:
            raise IndexError(seq)
        return page[0]


class _RegisteringKeystore:
    """Keystore handle that registers the owner row before the first keystore write.

    On Postgres ``keystores.owner`` references ``owners``, and the agent address is only known once
    the key exists, so the address is read from the encrypted blob (public field) at write time.
    On SQLite there is no owner table and this is a plain pass-through.
    """

    def __init__(self, inner: KeystoreStore, backend: StorageBackend) -> None:
        self._inner = inner
        self._backend = backend

    def put(self, owner: str, blob: str, *, overwrite: bool = False) -> None:
        _register_owner(self._backend, owner, _stored_address(blob))
        self._inner.put(owner, blob, overwrite=overwrite)

    def get(self, owner: str) -> str | None:
        return self._inner.get(owner)

    def exists(self, owner: str) -> bool:
        return self._inner.exists(owner)

    def owners(self) -> Any:
        return self._inner.owners()


def _register_owner(backend: StorageBackend, owner: str, agent_address: str) -> None:
    """Record the owner in the backend's owner table when it has one (Postgres). Idempotent."""
    owners = getattr(backend, "owners", None)
    if not callable(owners):
        return
    registry = owners()
    if registry.get(owner) is None:
        try:
            registry.create(owner, agent_address)
        except ValueError:
            pass  # registered concurrently


@dataclass
class TenantSettings:
    """Process-wide inputs a tenant needs. Nothing here is per request."""

    data_dir: Path  # where the SQLite backend lives when the registry is given no backend
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
    tier_view: Any = field(repr=False)  # the owner's TierRead
    paths: TenantPaths | None  # SQLite files; None on a backend without a directory per owner
    stores: TenantStores = field(repr=False)
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
    def __init__(
        self,
        settings: TenantSettings,
        rpc: RpcCall,
        max_open: int = 32,
        *,
        backend: StorageBackend | None = None,
    ) -> None:
        self._settings = settings
        self._rpc = rpc
        self._max_open = max(1, max_open)
        self._backend: StorageBackend = (
            backend if backend is not None else sqlite_backend(settings.data_dir)
        )
        self._cache: OrderedDict[str, Tenant] = OrderedDict()
        self._guard = threading.RLock()

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    def _paths(self, owner: str) -> TenantPaths:
        """Files of one owner on the SQLite layout; the path is checked to stay under owners/."""
        addr = normalize_owner(owner)
        owners_root = Path(self._settings.data_dir) / "owners"
        root = owners_root / addr
        if not root.resolve().is_relative_to(owners_root.resolve()):
            raise ValueError("invalid owner address")
        return TenantPaths(
            root=root,
            state=root / "state.sqlite",
            audit=root / "audit.sqlite",
            lock=root / "gateway.lock.sqlite",
            keystore=root / "keystore.json",
        )

    def exists(self, owner: str) -> bool:
        return self._backend.keystore_admin().exists(normalize_owner(owner))

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
        if not self.exists(addr):
            if not create:
                raise TenantNotFound("no agent wallet for this owner")
            if s.keystore_secret is None:
                raise CustodyUnavailable("keystore secret is unset or shorter than 32 bytes")
            create_agent_wallet(
                _RegisteringKeystore(self._backend.keystore_admin(), self._backend),
                addr,
                s.keystore_secret,
                s.kdf,
            )

        stores = self._backend.open_tenant(addr)
        signer = LazySigner(stores.keystore, addr, s.keystore_secret, sender=s.sender)
        wallet = signer.address
        _register_owner(self._backend, addr, wallet)  # repairs a crash after the keystore write

        portfolio = PortfolioReader(self._rpc, wallet, stores.ledger)  # type: ignore[arg-type]
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
            audit_sink=AuditSinkView(stores.audit),
            clock=s.clock,
            ips_hash=ips.hash,
            tx_builder=AaveTxBuilder(self._rpc, wallet, allow_estimate_fallback=not s.live),
            tier_view=stores.tier_read,
            lock=stores.lock,  # type: ignore[arg-type]
            intent_store=stores.intents,  # type: ignore[arg-type]
            # The operator stop the gateway itself reads at signing time: env, the GLOBAL_STOP
            # file or the backend setting (the only one that survives a hosted restart).
            global_stop=file_flag(
                Path(s.data_dir) / "GLOBAL_STOP",
                os.environ,
                settings_reader=self._backend.settings().global_stop,
            ),
        )
        return Tenant(
            owner=addr,
            agent_address=wallet,
            gateway=gateway,
            tier_view=stores.tier_read,
            paths=self._paths(addr) if isinstance(getattr(stores, "path", None), Path) else None,
            stores=stores,
            _lock=stores.lock,  # type: ignore[arg-type]
            _ips_for=s.ips_for,
            _policy_inputs=policy_inputs,
        )
