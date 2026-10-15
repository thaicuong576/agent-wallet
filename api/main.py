"""HTTP API entrypoint matching contracts/openapi.yaml.

`AGENT_WALLET_MODE=multi` builds the webapp services (wallet sign-in, per-owner agent wallets,
jobs) from the environment; any other value keeps the single-owner token mode of the original
API. Nothing here touches the network or creates a keystore at import time.

Storage (design 5, 13, 15): `DATABASE_URL` set selects Postgres, unset selects the SQLite backend
under `AGENT_WALLET_DATA_DIR`. With Postgres the app applies migrations only when
`DATABASE_MIGRATE_URL` is set; otherwise it checks the schema version and refuses to start when
the database is behind (fail closed).
"""

import json
import os
import pathlib
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi.staticfiles import StaticFiles

from api.app import create_app
from api.auth import AuthProblem, AuthService, SqliteAuthStore
from api.jobs import JobLimits, JobService
from api.wallet import WalletService, WebappServices
from core.gateway.tenant import (
    CustodyUnavailable,
    TenantRegistry,
    TenantSettings,
    sqlite_backend,
)
from core.policy.ips import LoadedIps, load_ips
from core.simulate import RpcSimulator
from core.storage.factory import POSTGRES, create_backend, selected_backend
from core.storage.interfaces import StorageBackend
from core.storage.migrate import MigrationError

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_RPC_URL = "https://sepolia.base.org"
EXAMPLES_DIR = ROOT_DIR / "contracts" / "examples"
DEFAULT_IPS_PATH = EXAMPLES_DIR / "ips.valid.json"


def http_rpc(url: str, timeout: float = 30.0) -> Callable[[str, list[Any]], Any]:
    """JSON-RPC over HTTPS with the standard library. The URL comes only from the environment."""

    def call(method: str, params: list[Any]) -> Any:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        req = urllib.request.Request(
            url,
            data=payload.encode(),
            headers={"Content-Type": "application/json", "User-Agent": "agent-wallet/0.1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - env URL only
            body = json.loads(resp.read())
        if body.get("error"):
            raise RuntimeError(f"{method} failed")
        return body["result"]

    return call


def reference_risk_inputs() -> dict[str, Any]:
    """Risk inputs for the policy engine, stamped fresh from the repository's reference file.

    Base Sepolia has no indexer for these metrics (see core/risk/provider.py), so the same
    reference values that scripts/live_first_tx.py uses are served, labelled `reference` in the
    data itself. No network call. Without them every `supply` would be denied as RISK.DATA_STALE.
    """
    risk = json.loads((EXAMPLES_DIR / "risk-inputs.valid.json").read_text(encoding="utf-8"))
    risk["as_of"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    risk["data_age_seconds"] = 0
    return risk


def global_stop_flag(
    data_dir: pathlib.Path,
    env: Mapping[str, str],
    settings: Any = None,
) -> Callable[[], bool]:
    """Operator stop: the backend setting, AGENT_WALLET_GLOBAL_STOP=1 or DATA_DIR/GLOBAL_STOP.

    Any one of them stops the agents; any error reading one reads as set (fail closed).
    """

    def stopped() -> bool:
        try:
            return (
                env.get("AGENT_WALLET_GLOBAL_STOP", "") == "1"
                or (data_dir / "GLOBAL_STOP").exists()
                or (settings is not None and bool(settings.global_stop()))
            )
        except Exception:  # noqa: BLE001 - fail closed
            return True

    return stopped


def build_backend(env: Mapping[str, str], data_dir: pathlib.Path) -> StorageBackend:
    """The StorageBackend the environment selects. Opens no connection until first use, except
    the schema check below, which is the point: a hosted app must not run on an old schema."""
    backend = create_backend(
        env,
        sqlite_factory=lambda: sqlite_backend(
            data_dir, auth=lambda: SqliteAuthStore(data_dir / "auth.sqlite"), env=env
        ),
    )
    if selected_backend(env) == POSTGRES:
        try:
            if env.get("DATABASE_MIGRATE_URL", "").strip():
                backend.migrate()  # DDL-capable URL present: apply, then verify
            else:
                backend.verify_schema()  # type: ignore[attr-defined]  # app role: verify only
        except MigrationError as exc:
            raise RuntimeError(
                f"database schema is not ready: {exc}. Run `python -m core.storage.migrate` with "
                "DATABASE_MIGRATE_URL set, or set DATABASE_MIGRATE_URL for this process."
            ) from exc
    return backend


def build_webapp(
    env: Mapping[str, str] | None = None, *, backend: StorageBackend | None = None
) -> WebappServices:
    """Wire the multi-owner services. Reads configuration only; opens no connection."""
    env = os.environ if env is None else env
    data_dir = pathlib.Path(env.get("AGENT_WALLET_DATA_DIR") or ".data")
    if selected_backend(env) != POSTGRES:
        data_dir.mkdir(parents=True, exist_ok=True)
    backend = backend if backend is not None else build_backend(env, data_dir)
    rpc_url = env.get("BASE_SEPOLIA_RPC_URL", DEFAULT_RPC_URL)
    rpc = http_rpc(rpc_url)
    ips_path = pathlib.Path(env.get("IPS_PATH") or DEFAULT_IPS_PATH)

    def ips_for(owner: str, preset_id: str | None) -> LoadedIps:
        return load_ips(ips_path)  # ticket A4 replaces this with the preset files

    secret = env.get("AGENT_WALLET_KEYSTORE_SECRET")
    settings = TenantSettings(
        data_dir=data_dir,
        ips_for=ips_for,
        keystore_secret=secret if secret and len(secret.encode()) >= 32 else None,
        kdf=env.get("AGENT_WALLET_KEYSTORE_KDF", "scrypt"),
        simulator=RpcSimulator(rpc_url),
        risk_inputs=reference_risk_inputs,
        sender=lambda raw: rpc("eth_sendRawTransaction", ["0x" + raw.hex().removeprefix("0x")]),
        live=True,
    )
    registry = TenantRegistry(settings, rpc, backend=backend)
    wallet = WalletService(rpc)
    jobs = JobService(
        rpc=rpc,
        gas_state=wallet.gas_state,
        global_stop=global_stop_flag(data_dir, env, backend.settings()),
        limits=JobLimits.from_env(env),
    )
    jobs.mark_interrupted_on_startup(backend)

    def on_login(address: str) -> None:
        try:
            registry.open(address, create=True)  # first sign-in creates the agent wallet
        except CustodyUnavailable:
            raise AuthProblem(
                503,
                "custody-unavailable",
                "Custody is not available",
                "agent key custody is not ready",
            ) from None

    auth = AuthService(backend.auth(), on_login=on_login)
    return WebappServices(registry=registry, auth=auth, wallet=wallet, jobs=jobs, mode="multi")


def create_configured_app() -> Any:
    multi = os.environ.get("AGENT_WALLET_MODE", "single") == "multi"
    if not multi:
        return create_app(webapp=None)
    env = os.environ
    data_dir = pathlib.Path(env.get("AGENT_WALLET_DATA_DIR") or ".data")
    backend = build_backend(env, data_dir)
    app_ = create_app(webapp=build_webapp(env, backend=backend))

    @app_.get("/health/deep", include_in_schema=False)
    def health_deep() -> Any:
        """Liveness plus one cheap database read, for an external keep-warm ping.

        The ping keeps the free web service awake and the free database project active. It returns
        no data from the database and needs no credential.
        """
        from fastapi.responses import JSONResponse

        try:
            backend.settings().global_stop()
        except Exception:  # noqa: BLE001 - never echo driver text
            return JSONResponse({"status": "degraded", "db": "unreachable"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    return app_


app = create_configured_app()

UI_DIR = ROOT_DIR / "ui"
app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

__all__ = ["app", "create_app", "build_webapp"]
