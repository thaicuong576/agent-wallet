"""HTTP API entrypoint matching contracts/openapi.yaml.

`AGENT_WALLET_MODE=multi` builds the webapp services (wallet sign-in, per-owner agent wallets,
jobs) from the environment; any other value keeps the single-owner token mode of the original
API. Nothing here touches the network or creates a keystore at import time.

Storage (design 5, 13, 15): `DATABASE_URL` set selects Postgres, unset selects the SQLite backend
under `AGENT_WALLET_DATA_DIR`. With Postgres the app applies migrations only when
`DATABASE_MIGRATE_URL` is set; otherwise it checks the schema version and refuses to start when
the database is behind (fail closed).
"""

import contextvars
import http.client
import json
import logging
import os
import pathlib
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
from core.storage.postgres.pool import _request_db_acc, db_calls, db_ms, db_stats

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_RPC_URL = "https://sepolia.base.org"
EXAMPLES_DIR = ROOT_DIR / "contracts" / "examples"
PRESETS_DIR = ROOT_DIR / "contracts" / "presets"
DEFAULT_IPS_PATH = (
    PRESETS_DIR / "conservative.json"
    if (PRESETS_DIR / "conservative.json").exists()
    else EXAMPLES_DIR / "ips.valid.json"
)


def configure_logging(env: Mapping[str, str] | None = None) -> logging.Logger:
    """Configure stdout logging for the `agent_wallet` logger hierarchy.

    Sets the `agent_wallet` logger (and children) to the level in env `AGENT_WALLET_LOG_LEVEL`
    (default `INFO`), attaches one `StreamHandler` to stdout with format
    `%(asctime)s %(levelname)s %(name)s %(message)s`, `propagate = False`, and is idempotent
    (calling twice does not duplicate handlers). Do not touch the root logger and do not change
    uvicorn's loggers. Never log request bodies, headers, cookies, keys or addresses of owners.
    """
    if env is None:
        env = os.environ
    raw_level = env.get("AGENT_WALLET_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, raw_level, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    logger = logging.getLogger("agent_wallet")
    logger.setLevel(level)
    logger.propagate = False

    has_stdout = any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        for h in logger.handlers
    )
    if not has_stdout:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger


configure_logging()

rpc_calls: contextvars.ContextVar[int] = contextvars.ContextVar("rpc_calls", default=0)
rpc_ms: contextvars.ContextVar[int] = contextvars.ContextVar("rpc_ms", default=0)
_request_rpc_acc: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "_request_rpc_acc", default=None
)

rpc_log = logging.getLogger("agent_wallet.rpc")
http_log = logging.getLogger("agent_wallet.http")


def http_rpc(url: str, timeout: float = 30.0) -> Callable[[str, list[Any]], Any]:
    """JSON-RPC over HTTPS with the standard library. The URL comes only from the environment."""
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or "localhost"
    port = parsed.port
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    local = threading.local()

    def _create_connection() -> http.client.HTTPConnection:
        if scheme == "https":
            return http.client.HTTPSConnection(host, port=port or 443, timeout=timeout)
        return http.client.HTTPConnection(host, port=port or 80, timeout=timeout)

    def _get_connection() -> http.client.HTTPConnection:
        conn = getattr(local, "conn", None)
        if conn is None:
            conn = _create_connection()
            local.conn = conn
        return conn

    def _reset_connection() -> None:
        conn = getattr(local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            local.conn = None

    def call(method: str, params: list[Any]) -> Any:
        t0 = time.perf_counter()
        status = "error"
        try:
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            ).encode()
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "agent-wallet/0.1",
                "Connection": "keep-alive",
            }

            def _send(conn: http.client.HTTPConnection) -> bytes:
                conn.request("POST", path, body=payload, headers=headers)
                resp = conn.getresponse()
                try:
                    data = resp.read()
                finally:
                    if resp.will_close:
                        _reset_connection()
                if resp.status < 200 or resp.status >= 300:
                    raise RuntimeError(f"{method} failed with HTTP {resp.status}")
                return data

            conn = _get_connection()
            try:
                data = _send(conn)
            except (http.client.HTTPException, ConnectionError, OSError):
                _reset_connection()
                conn = _get_connection()
                data = _send(conn)

            body = json.loads(data)
            if body.get("error"):
                raise RuntimeError(f"{method} failed")
            status = "ok"
            return body["result"]
        finally:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            rpc_log.info("method=%s elapsed_ms=%d status=%s", method, elapsed_ms, status)
            try:
                rpc_calls.set(rpc_calls.get() + 1)
                rpc_ms.set(rpc_ms.get() + elapsed_ms)
                acc = _request_rpc_acc.get()
                if acc is not None:
                    acc[0] += 1
                    acc[1] += elapsed_ms
            except Exception:
                pass

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
    if selected_backend(env) == POSTGRES:
        from core.storage.postgres import PostgresBackend
        from core.storage.postgres.pool import parse_pool_size

        pool_size = parse_pool_size(env.get("AGENT_WALLET_DB_POOL_SIZE"))
        migrate_url = env.get("DATABASE_MIGRATE_URL", "").strip() or None
        backend = PostgresBackend(
            env["DATABASE_URL"].strip(),
            migrate_dsn=migrate_url,
            pool_size=pool_size,
        )
        backend._pool.warm(4)
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

    return create_backend(
        env,
        sqlite_factory=lambda: sqlite_backend(
            data_dir, auth=lambda: SqliteAuthStore(data_dir / "auth.sqlite"), env=env
        ),
    )


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
        if preset_id is None and backend is not None:
            try:
                latest = backend.open_tenant(owner).policy_selection.latest()
                if latest and latest.get("preset_id"):
                    preset_id = latest["preset_id"]
            except Exception:
                pass
        preset = preset_id or "conservative"
        preset_file = PRESETS_DIR / f"{preset}.json"
        if preset_file.exists():
            return load_ips(preset_file)
        if preset == "conservative" and ips_path.exists():
            return load_ips(ips_path)
        raise ValueError(f"Unknown preset_id: {preset}")

    secret = env.get("AGENT_WALLET_KEYSTORE_SECRET")
    risk_source = env.get("AGENT_WALLET_RISK_SOURCE", "onchain").strip().lower()
    if risk_source == "reference":
        risk_inputs_fn: Callable[..., Any] = reference_risk_inputs
    else:
        from core.risk.router import RiskRouter

        risk_inputs_fn = RiskRouter(rpc_callable=rpc, rpc_url=rpc_url)

    settings = TenantSettings(
        data_dir=data_dir,
        ips_for=ips_for,
        keystore_secret=secret if secret and len(secret.encode()) >= 32 else None,
        kdf=env.get("AGENT_WALLET_KEYSTORE_KDF", "scrypt"),
        simulator=RpcSimulator(rpc_url),
        risk_inputs=risk_inputs_fn,
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


class TimingMiddleware:
    """Pure ASGI middleware logging request timing, RPC, and DB metrics.

    Emits `method=<verb> path=<route> status=<code> elapsed_ms=<ms> rpc_calls=<n> rpc_ms=<ms>
    db_calls=<n> db_ms=<ms>` on logger `agent_wallet.http` for all requests except `/health`,
    `/health/deep`, and `/ui/*`. For streaming responses (like `/chat/stream/*`), timing
    completes when the stream ends.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        clean_path = path.rstrip("/")
        if clean_path in ("/health", "/health/deep") or path == "/ui" or path.startswith("/ui/"):
            await self.app(scope, receive, send)
            return

        acc = [0, 0]
        token_acc = _request_rpc_acc.set(acc)
        token_calls = rpc_calls.set(0)
        token_ms = rpc_ms.set(0)
        db_acc = [0, 0]
        token_db_acc = _request_db_acc.set(db_acc)
        token_db_calls = db_calls.set(0)
        token_db_ms = db_ms.set(0)
        t0 = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if acc[0] > 0:
                calls = acc[0]
                ms = acc[1]
            else:
                calls = rpc_calls.get()
                ms = rpc_ms.get()
            if db_acc[0] > 0:
                d_calls = db_acc[0]
                d_ms = db_acc[1]
            else:
                d_calls = db_calls.get()
                d_ms = db_ms.get()
            _request_rpc_acc.reset(token_acc)
            rpc_calls.reset(token_calls)
            rpc_ms.reset(token_ms)
            _request_db_acc.reset(token_db_acc)
            db_calls.reset(token_db_calls)
            db_ms.reset(token_db_ms)
            method = scope.get("method", "GET")
            http_log.info(
                "method=%s path=%s status=%d elapsed_ms=%d rpc_calls=%d rpc_ms=%d "
                "db_calls=%d db_ms=%d",
                method,
                path,
                status_code,
                elapsed_ms,
                calls,
                ms,
                d_calls,
                d_ms,
            )


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles subclass ensuring Cache-Control: no-cache on all static responses."""

    def file_response(
        self,
        full_path: Any,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        resp = super().file_response(full_path, stat_result, scope, status_code=status_code)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_configured_app() -> Any:
    multi = os.environ.get("AGENT_WALLET_MODE", "single") == "multi"
    if not multi:
        app_ = create_app(webapp=None)
    else:
        env = os.environ
        data_dir = pathlib.Path(env.get("AGENT_WALLET_DATA_DIR") or ".data")
        backend = build_backend(env, data_dir)
        app_ = create_app(webapp=build_webapp(env, backend=backend))

        @app_.get("/health/deep", include_in_schema=False)
        def health_deep() -> Any:
            """Liveness plus one cheap database read, for an external keep-warm ping.

            The ping keeps the free web service awake and the free database project active.
            It returns no data from the database and needs no credential.
            """
            from fastapi.responses import JSONResponse

            try:
                backend.settings().global_stop()
            except Exception:  # noqa: BLE001 - never echo driver text
                return JSONResponse({"status": "degraded", "db": "unreachable"}, status_code=503)
            return {"status": "ok", "db": "ok"}

    app_.add_middleware(TimingMiddleware)
    return app_


app = create_configured_app()

UI_DIR = ROOT_DIR / "ui"
app.mount("/ui", NoCacheStaticFiles(directory=UI_DIR, html=True), name="ui")

__all__ = [
    "NoCacheStaticFiles",
    "TimingMiddleware",
    "app",
    "build_webapp",
    "configure_logging",
    "create_app",
    "create_configured_app",
    "db_calls",
    "db_ms",
    "db_stats",
    "http_rpc",
    "rpc_calls",
    "rpc_ms",
]
