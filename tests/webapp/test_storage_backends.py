"""The multi-owner app on both StorageBackends (design 5, 13, 20).

Every test using the ``stack`` fixture runs on ``sqlite`` and on ``postgres``; the Postgres
parameter runs when ``TEST_DATABASE_URL`` is set (CI service container) and is skipped otherwise.
Each Postgres test gets its own migrated schema, so tests never see each other's rows.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthService, SqliteAuthStore
from api.wallet import WebappServices
from core.audit.verify import verify_chain
from core.gateway.tenant import sqlite_backend
from core.storage.interfaces import StorageBackend, TenantStores
from core.storage.migrate import MigrationError
from tests.storage.test_pg_support import SKIP_REASON, TEST_DATABASE_URL, PgTestSchema
from tests.webapp.conftest import ORIGIN, TestWallet, login
from tests.webapp.test_jobs import make_env

NOW_ISO = "2026-10-15T12:00:00Z"


@dataclass
class Stack:
    """One backend flavour: a factory that returns a fresh StorageBackend over the same storage."""

    name: str
    tmp_path: Path
    new_backend: Callable[[], StorageBackend]
    pg: PgTestSchema | None = None

    def env(self, **kwargs: Any):
        return make_env(self.tmp_path, backend=self.new_backend(), **kwargs)


@pytest.fixture(params=["sqlite", "postgres"])
def stack(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Stack]:
    opened: list[StorageBackend] = []
    if request.param == "sqlite":

        def make() -> StorageBackend:
            backend = sqlite_backend(
                tmp_path, auth=lambda: SqliteAuthStore(tmp_path / "auth.sqlite")
            )
            opened.append(backend)
            return backend

        yield Stack("sqlite", tmp_path, make)
    else:
        if not TEST_DATABASE_URL:
            pytest.skip(SKIP_REASON)
        from core.storage.postgres import PostgresBackend

        schema = PgTestSchema()

        def make_pg() -> StorageBackend:
            backend = PostgresBackend(schema.dsn)
            opened.append(backend)
            return backend

        try:
            yield Stack("postgres", tmp_path, make_pg, schema)
        finally:
            for backend in opened:
                backend.close()
            schema.drop()
        return
    for backend in opened:
        backend.close()


def new_owner() -> str:
    return Account.create().address.lower()


def drop_pooled_connections(backend: StorageBackend) -> None:
    """Close every idle pooled connection: the next statement must work on a fresh session."""
    pool = getattr(backend, "_pool", None)
    if pool is None:
        return
    while not pool._idle.empty():
        pool._idle.get_nowait().close()


# ---------------------------------------------------------------- sign-in creates the tenant


def test_sign_in_creates_tenant_and_keystore(stack: Stack, auth_env):
    env = stack.env()
    auth = AuthService(
        env.registry.backend.auth(),
        clock=env.clock,
        on_login=lambda addr: env.registry.open(addr, create=True),
    )
    app = create_app(
        webapp=WebappServices(registry=env.registry, auth=auth, wallet=env.wallet, jobs=env.jobs)
    )
    wallet = TestWallet()
    client = TestClient(app, base_url=ORIGIN)
    assert login(client, wallet).status_code == 200
    me = client.get("/me").json()
    owner = wallet.address.lower()
    assert env.registry.exists(owner)
    assert me["address"].lower() == owner and me["agent_address"].startswith("0x")
    blob = env.registry.backend.keystore_admin().get(owner)
    assert blob is not None and "crypto" in blob.lower()  # encrypted JSON, never a plaintext key
    if stack.pg is not None:
        rows = stack.pg.raw("SELECT owner, kdf FROM keystores")
        assert [r[0] for r in rows] == [owner]
        assert stack.pg.raw("SELECT agent_address FROM owners WHERE address = %s", (owner,))
    else:
        assert (stack.tmp_path / "owners" / owner / "keystore.json").is_file()


# ---------------------------------------------------------------- a mint job leaves an audit record


def test_mint_job_records_an_audit_record(stack: Stack):
    env = stack.env()
    tenant = env.tenant()
    job_id = env.jobs.start(tenant, "mint", {"amount_usdc": "5"})
    env.executor.drain()
    job = env.jobs.get(tenant, job_id)
    assert job is not None and job["status"] in {"succeeded", "denied"}
    assert len(job["records"]) == 1 and job["records"][0]["function"] == "mint"
    records = tenant.stores.audit.list()
    assert len(records) == 1 and records[0]["order_id"] == job["order_ids"][0]
    assert verify_chain(tenant.gateway.audit_sink).valid
    assert [j["job_id"] for j in env.jobs.list(tenant)] == [job_id]


def test_top_up_row_round_trips(stack: Stack):
    env = stack.env()
    tenant = env.tenant()
    tx = "0x" + "c3" * 32
    first = tenant.stores.topups.put(tx, 10**15, env.clock())
    again = tenant.stores.topups.put(tx, 5, env.clock())  # a repeat keeps the first row
    assert first == again and first["status"] == "pending" and first["amount_wei"] == str(10**15)
    tenant.stores.topups.settle(tx, "confirmed", 123)
    tenant.stores.topups.settle(tx, "failed", 124)  # a settled row never changes
    row = tenant.stores.topups.get(tx)
    assert row is not None and row["status"] == "confirmed" and row["block_number"] == 123


# ---------------------------------------------------------------- isolation between owners


def test_second_owner_cannot_read_first_owners_rows(stack: Stack):
    env = stack.env()
    a, b = env.tenant(), env.tenant()
    job_id = env.jobs.start(a, "mint", {"amount_usdc": "5"})
    env.executor.drain()
    tx = "0x" + "d4" * 32
    a.stores.topups.put(tx, 7, env.clock())
    a.gateway.record_no_action("owner a only", NOW_ISO)
    assert env.jobs.get(b, job_id) is None and env.jobs.list(b) == []
    assert b.stores.topups.get(tx) is None
    assert b.stores.audit.list() == [] and b.gateway.audit_sink.records == []
    assert b.stores.audit.find_by_order_id(a.stores.audit.list()[0]["order_id"]) is None
    assert verify_chain(a.gateway.audit_sink).count == 2
    assert verify_chain(b.gateway.audit_sink).count == 0
    assert a.agent_address != b.agent_address
    if stack.pg is not None:
        with pytest.raises(ValueError):  # a keystore handle is bound to its own owner
            b.stores.keystore.get(a.owner)


# ---------------------------------------------------------------- restart keeps keystore and chain


def test_restart_keeps_keystore_and_chain(stack: Stack):
    first = stack.env()
    owner = new_owner()
    t1 = first.registry.open(owner, create=True)
    for note in ("one", "two"):
        t1.gateway.record_no_action(note, NOW_ISO)
    address = t1.agent_address
    first.registry.backend.close()

    second = stack.env()  # a new process: new backend, new registry, empty cache
    t2 = second.registry.open(owner)  # no create: the stored keystore is found
    assert t2.agent_address == address
    assert second.registry.exists(owner)
    report = verify_chain(t2.gateway.audit_sink)
    assert report.valid and report.count == 2
    t2.gateway.record_no_action("three", NOW_ISO)  # the chain continues from the stored head
    assert verify_chain(t2.gateway.audit_sink).count == 3
    assert t2.gateway.signer.address == address


def test_running_job_is_interrupted_after_restart(stack: Stack):
    first = stack.env()
    tenant = first.tenant()
    job_id = first.jobs.start(tenant, "mint", {"amount_usdc": "5"})  # never drained: stays queued
    second = stack.env()
    reopened = second.registry.open(tenant.owner)
    assert second.jobs.mark_interrupted_on_startup(second.registry.backend) == 1
    job = second.jobs.get(reopened, job_id)
    assert job is not None and job["status"] == "interrupted"
    assert second.jobs.mark_interrupted_on_startup(second.registry.backend) == 0


# ---------------------------------------------------------------- pooler style: no session state


def test_every_statement_works_on_a_fresh_connection(stack: Stack):
    """A transaction-mode pooler may hand each statement a different server connection."""
    env = stack.env()
    backend = env.registry.backend
    owner = new_owner()
    tenant = env.registry.open(owner, create=True)
    drop_pooled_connections(backend)
    tenant.gateway.record_no_action("one", NOW_ISO)
    drop_pooled_connections(backend)
    job_id = env.jobs.start(tenant, "mint", {"amount_usdc": "5"})
    drop_pooled_connections(backend)
    env.executor.drain()
    drop_pooled_connections(backend)
    job = env.jobs.get(tenant, job_id)
    assert job is not None and job["status"] in {"succeeded", "denied"}
    assert verify_chain(tenant.gateway.audit_sink).valid
    assert backend.auth() is not None


# ---------------------------------------------------------------- wiring in api/main.py


class StubBackend:
    """Records which schema calls build_backend makes."""

    def __init__(self, behind: bool = False) -> None:
        self.behind, self.calls = behind, []

    def migrate(self) -> None:
        self.calls.append("migrate")

    def verify_schema(self) -> int:
        self.calls.append("verify_schema")
        if self.behind:
            raise MigrationError("Schema is behind the code: pending migrations [3]")
        return 3


def test_app_role_without_migrate_url_only_checks_the_schema(monkeypatch, tmp_path):
    import api.main as main

    stub = StubBackend()
    monkeypatch.setattr(main, "create_backend", lambda env, sqlite_factory=None: stub)
    got = main.build_backend({"DATABASE_URL": "postgresql://x/y"}, tmp_path)
    assert got is stub and stub.calls == ["verify_schema"]  # migrate() was not called


def test_migrate_url_applies_migrations(monkeypatch, tmp_path):
    import api.main as main

    stub = StubBackend()
    monkeypatch.setattr(main, "create_backend", lambda env, sqlite_factory=None: stub)
    env = {"DATABASE_URL": "postgresql://x/y", "DATABASE_MIGRATE_URL": "postgresql://x/z"}
    main.build_backend(env, tmp_path)
    assert stub.calls == ["migrate"]


def test_schema_behind_the_code_fails_closed(monkeypatch, tmp_path):
    import api.main as main

    stub = StubBackend(behind=True)
    monkeypatch.setattr(main, "create_backend", lambda env, sqlite_factory=None: stub)
    with pytest.raises(RuntimeError, match="python -m core.storage.migrate"):
        main.build_backend({"DATABASE_URL": "postgresql://x/y"}, tmp_path)
    assert stub.calls == ["verify_schema"]


def test_unset_database_url_builds_the_sqlite_backend_and_opens_nothing(tmp_path):
    import api.main as main

    backend = main.build_backend({}, tmp_path)
    assert type(backend).__name__ == "SqliteBackend"
    assert list(tmp_path.iterdir()) == []  # no file or connection until a tenant is opened


def test_global_stop_reads_backend_env_and_file_and_fails_closed(tmp_path):
    import api.main as main

    class Settings:
        def __init__(self, value: bool | Exception) -> None:
            self.value = value

        def global_stop(self) -> bool:
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    assert main.global_stop_flag(tmp_path, {}, Settings(False))() is False
    assert main.global_stop_flag(tmp_path, {}, Settings(True))() is True  # backend setting
    assert main.global_stop_flag(tmp_path, {"AGENT_WALLET_GLOBAL_STOP": "1"}, Settings(False))()
    (tmp_path / "GLOBAL_STOP").write_text("1")
    assert main.global_stop_flag(tmp_path, {}, Settings(False))() is True  # file flag
    (tmp_path / "GLOBAL_STOP").unlink()
    assert main.global_stop_flag(tmp_path, {}, Settings(RuntimeError("down")))() is True


def test_postgres_behind_schema_refuses_to_start(tmp_path):
    if not TEST_DATABASE_URL:
        pytest.skip(SKIP_REASON)
    import uuid

    import psycopg

    import api.main as main
    from core.storage.migrate import apply_migrations, load_migrations
    from tests.storage.test_pg_support import schema_dsn

    name = "t_" + uuid.uuid4().hex[:16]
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(name)))
    try:
        dsn = schema_dsn(name)
        apply_migrations(dsn, load_migrations()[:2])  # the database is one migration behind
        with pytest.raises(RuntimeError, match="not ready"):
            main.build_backend({"DATABASE_URL": dsn}, tmp_path)
        backend = main.build_backend({"DATABASE_URL": dsn, "DATABASE_MIGRATE_URL": dsn}, tmp_path)
        assert backend.verify_schema() == len(load_migrations())  # migrate brought it current
        backend.close()
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(name))
            )


def test_build_webapp_runs_from_the_same_code_on_both_backends(stack: Stack, monkeypatch):
    import api.main as main

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: pytest.fail("network used while wiring")
    )
    env: dict[str, str] = {
        "AGENT_WALLET_DATA_DIR": str(stack.tmp_path),
        "AGENT_WALLET_KEYSTORE_SECRET": "w" * 40,
        "AGENT_WALLET_KEYSTORE_KDF": "pbkdf2",
    }
    if stack.pg is not None:
        env["DATABASE_URL"] = stack.pg.dsn
        env["DATABASE_MIGRATE_URL"] = stack.pg.dsn
    services = main.build_webapp(env)
    try:
        owner = new_owner()
        tenant = services.registry.open(owner, create=True)
        assert services.registry.exists(owner) and tenant.agent_address
        assert type(services.registry.backend).__name__ == (
            "PostgresBackend" if stack.pg is not None else "SqliteBackend"
        )
        assert services.mode == "multi"
    finally:
        services.registry.backend.close()


def test_tenant_stores_expose_every_protocol(stack: Stack):
    tenant = stack.env().tenant()
    stores: TenantStores = tenant.stores
    assert isinstance(stores, TenantStores)
    for name in (
        "tier_read",
        "tier_write",
        "audit",
        "intents",
        "ledger",
        "lock",
        "keystore",
        "jobs",
        "policy_selection",
        "topups",
    ):
        assert getattr(stores, name) is not None, name
    assert (tenant.paths is None) == (stack.name == "postgres")


def test_gateway_reads_the_operator_stop_from_the_backend(stack: Stack, monkeypatch):
    monkeypatch.delenv("AGENT_WALLET_GLOBAL_STOP", raising=False)
    env = stack.env()
    tenant = env.tenant()
    assert tenant.gateway.global_stop() is False
    if stack.pg is not None:
        env.registry.backend.settings().set_global_stop(True)  # the row survives a restart
        assert tenant.gateway.global_stop() is True
        env.registry.backend.settings().set_global_stop(False)
        assert tenant.gateway.global_stop() is False
    else:
        (stack.tmp_path / "GLOBAL_STOP").write_text("1")
        assert tenant.gateway.global_stop() is True


def test_health_deep_reads_the_database_and_returns_no_data(tmp_path, monkeypatch):
    """/health/deep is the keep-warm target: ok with a reachable backend, 503 otherwise."""
    from fastapi.testclient import TestClient

    import api.main as main

    monkeypatch.setenv("AGENT_WALLET_MODE", "multi")
    monkeypatch.setenv("AGENT_WALLET_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENT_WALLET_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("AGENT_WALLET_PUBLIC_ORIGIN", "http://localhost:8000")
    app = main.create_configured_app()
    client = TestClient(app)
    r = client.get("/health/deep")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}
