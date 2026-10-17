"""Postgres-only behaviour: lock, keystore, auth, jobs, settings, owners, outage."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from core.gateway.exceptions import GatewayBusy
from core.storage.factory import create_backend, selected_backend
from core.storage.interfaces import (
    AuthStore,
    JobStore,
    KeystoreStore,
    OwnerRegistry,
    PolicySelectionStore,
    SettingsStore,
    StorageBackend,
    StorageUnavailable,
)
from core.storage.postgres import ActiveJobExists, PostgresBackend
from tests.storage.contract.conftest import NOW, OWNER_A, OWNER_B, iso, make_record
from tests.storage.test_pg_support import pg_schema, requires_pg  # noqa: F401

LATER = NOW + timedelta(hours=1)


# -- no database needed ----------------------------------------------------------------------
def test_backend_selected_by_database_url() -> None:
    assert selected_backend({}) == "sqlite"
    assert selected_backend({"DATABASE_URL": ""}) == "sqlite"
    assert selected_backend({"DATABASE_URL": "postgresql://u:p@h/db"}) == "postgres"


def test_unset_database_url_uses_the_supplied_sqlite_factory() -> None:
    sentinel = object()
    assert create_backend({}, sqlite_factory=lambda: sentinel) is sentinel  # type: ignore[arg-type,return-value]
    with pytest.raises(RuntimeError):
        create_backend({})


def test_set_database_url_builds_postgres_without_connecting() -> None:
    backend = create_backend({"DATABASE_URL": "postgresql://u:p@127.0.0.1:1/db"})
    try:
        assert isinstance(backend, StorageBackend)
        assert isinstance(backend, PostgresBackend)
    finally:
        backend.close()


def test_unreachable_database_is_storage_unavailable_and_global_stop_fails_closed() -> None:
    backend = PostgresBackend("postgresql://u:p@127.0.0.1:1/db?connect_timeout=1")
    try:
        tenant = backend.open_tenant(OWNER_A)
        with pytest.raises(StorageUnavailable):
            tenant.audit.append(make_record())
        with pytest.raises(StorageUnavailable):
            with tenant.lock.held(timeout_s=0.1):
                pytest.fail("the body must not run without a database")
        assert backend.settings().global_stop() is True
        assert isinstance(backend.settings(), SettingsStore)
    finally:
        backend.close()


def test_pg_chat_store_newest_empty_thread_offline() -> None:
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from core.storage.postgres.chat import PgChatStore

    pool = MagicMock()
    cur = MagicMock()

    @contextmanager
    def mock_tenant(owner):
        yield cur

    pool.tenant = mock_tenant
    store = PgChatStore(pool, OWNER_A)

    # 1. No threads
    cur.fetchone.side_effect = [None]
    assert store.newest_empty_thread() is None

    # 2. Thread exists and is empty
    cur.fetchone.side_effect = [("t1", "New chat"), None]
    assert store.newest_empty_thread() == {"id": "t1", "title": "New chat"}

    # 3. Thread exists and has messages
    cur.fetchone.side_effect = [("t1", "New chat"), (1,)]
    assert store.newest_empty_thread() is None


def test_pg_chat_store_intent_and_thread_lookup_offline() -> None:
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from core.storage.postgres.chat import PgChatStore

    pool = MagicMock()
    cur = MagicMock()

    @contextmanager
    def mock_tenant(owner):
        yield cur

    pool.tenant = mock_tenant
    store = PgChatStore(pool, OWNER_A)

    # append with intent
    cur.fetchone.return_value = None
    appended = store.append("agent", "I supplied 5 USDC.", "job1", NOW, intent="action")
    assert appended["intent"] == "action"
    assert appended["role"] == "agent"

    # list with intent row
    cur.fetchall.return_value = [
        ("m1", "user", "hi", "job0", NOW, None),
        ("m2", "agent", "hello", "job0", NOW, "question"),
    ]
    rows = store.list()
    assert len(rows) == 2
    assert "intent" not in rows[0]
    assert rows[1]["intent"] == "question"

    # get_thread_id
    cur.fetchone.return_value = ("th_123",)
    assert store.get_thread_id("job1") == "th_123"
    cur.fetchone.return_value = None
    assert store.get_thread_id("job_missing") is None


# -- needs Postgres ----------------------------------------------------------------------------
@pytest.fixture
def be(pg_schema):  # noqa: F811
    backend = PostgresBackend(pg_schema.dsn)
    try:
        yield backend
    finally:
        backend.close()


@requires_pg
def test_lock_timeout_fails_closed_before_the_body_and_records_nothing(be) -> None:
    holder, waiter = be.open_tenant(OWNER_A), be.open_tenant(OWNER_A, lock_timeout_s=0.2)
    entered = threading.Event()
    with holder.lock.held():
        with pytest.raises(GatewayBusy):
            with waiter.lock.held():
                entered.set()
    assert not entered.is_set()
    assert list(waiter.audit.iter_all()) == []
    with waiter.lock.held(timeout_s=1):  # free again once the holder let go
        pass


@requires_pg
def test_lock_survives_pool_exhaustion(be) -> None:
    tenant = be.open_tenant(OWNER_A)
    with tenant.lock.held():
        for _ in range(8):  # the lock holds a dedicated connection, not a pool slot
            tenant.audit.append(make_record())
    assert len(list(tenant.audit.iter_all())) == 8


@requires_pg
def test_derived_columns_equal_the_payload(be, pg_schema) -> None:  # noqa: F811
    tenant = be.open_tenant(OWNER_A)
    tenant.audit.append(
        make_record("executed", function="supply", value="123", created_at=iso(NOW))
    )
    tenant.audit.append(make_record("denied", token="WETH", value="9", decimals=18))
    tenant.audit.append({"schema_version": 1, "outcome": "denied", "created_at": iso(NOW)})
    for payload, function, token, value, decimals, created in pg_schema.raw(
        "SELECT payload, function, token, amount_value, decimals, created_at "
        "FROM audit_records ORDER BY seq"
    ):
        order = json.loads(payload).get("order")
        if order is None:
            assert (function, token, value, decimals) == (None, None, None, None)
            continue
        assert function == order["function"]
        assert token == order["token"]
        assert int(value) == int(order["amount"]["value"])
        assert decimals == order["amount"]["decimals"]
        assert created == datetime.fromisoformat(json.loads(payload)["created_at"])


@requires_pg
def test_ledger_counts_only_the_owners_rows(be) -> None:
    a, b = be.open_tenant(OWNER_A), be.open_tenant(OWNER_B)
    a.audit.append(make_record("executed", value="5000000", created_at=iso(NOW)))
    b.audit.append(make_record("executed", value="7000000", created_at=iso(NOW)))
    b.audit.append(make_record("executed", function="mint", value="7000000", created_at=iso(NOW)))
    since = NOW - timedelta(hours=1)
    assert a.ledger.executed_count_since(since) == 1
    assert b.ledger.executed_count_since(since) == 2  # mint counts as a transaction
    assert b.ledger.spent_usd_since(since) == 7  # but not as spend


@requires_pg
def test_keystore_roundtrip_no_overwrite_no_plaintext(be, pg_schema) -> None:  # noqa: F811
    store = be.open_tenant(OWNER_A).keystore
    assert isinstance(store, KeystoreStore)
    blob = json.dumps({"version": 3, "crypto": {"kdf": "pbkdf2", "ciphertext": "ab" * 16}})
    assert store.exists(OWNER_A) is False and store.get(OWNER_A) is None
    store.put(OWNER_A, blob)
    assert store.get(OWNER_A) == blob and store.exists(OWNER_A)
    with pytest.raises(FileExistsError):
        store.put(OWNER_A, "{}")
    assert store.get(OWNER_A) == blob
    store.put(OWNER_A, '{"crypto": {"kdf": "scrypt"}}', overwrite=True)
    assert pg_schema.raw("SELECT kdf FROM keystores") == [("scrypt",)]
    with pytest.raises(ValueError):
        store.put(OWNER_B, blob)
    dump = repr(pg_schema.raw("SELECT * FROM keystores"))
    assert "0x" + "11" * 32 not in dump


@requires_pg
def test_nonce_single_use(be) -> None:
    auth = be.auth()
    assert isinstance(auth, AuthStore)
    auth.put_nonce(OWNER_A, "n1", LATER)
    assert auth.consume_nonce(OWNER_A, "n1", NOW) is True
    assert auth.consume_nonce(OWNER_A, "n1", NOW) is False
    auth.put_nonce(OWNER_A, "n2", NOW)
    assert auth.consume_nonce(OWNER_A, "n2", LATER) is False  # expired
    assert auth.consume_nonce(OWNER_B, "n1", NOW) is False


@requires_pg
def test_stepup_single_use(be) -> None:
    auth = be.auth()
    payload = {"action": "set_tier", "params_hash": "h", "address": OWNER_A}
    auth.put_stepup("c1", payload, LATER)
    assert auth.consume_stepup("c1", NOW) == payload
    assert auth.consume_stepup("c1", NOW) is None
    auth.put_stepup("c2", payload, NOW)
    assert auth.consume_stepup("c2", LATER) is None
    assert auth.consume_stepup("c2", NOW - timedelta(minutes=1)) is None  # gone even if expired


@requires_pg
def test_revoked_session(be) -> None:
    auth = be.auth()
    auth.put_session("j1", OWNER_A, LATER)
    got = auth.get_session("j1", NOW)
    assert got is not None and got["address"] == OWNER_A and got["expires_at"] == LATER
    assert auth.get_session("j1", LATER + timedelta(seconds=1)) is None
    auth.revoke("j1")
    assert auth.get_session("j1", NOW) is None
    assert auth.get_session("unknown", NOW) is None


@requires_pg
def test_purge_expired(be) -> None:
    auth = be.auth()
    auth.put_nonce(OWNER_A, "old", NOW)
    auth.put_session("old", OWNER_A, NOW)
    auth.put_stepup("old", {"a": 1}, NOW)
    auth.put_nonce(OWNER_A, "new", LATER)
    assert auth.purge_expired(LATER - timedelta(minutes=1)) == 3
    assert auth.consume_nonce(OWNER_A, "new", NOW) is True


@requires_pg
def test_one_active_job_per_owner(be) -> None:
    a, b = be.open_tenant(OWNER_A).jobs, be.open_tenant(OWNER_B).jobs
    assert isinstance(a, JobStore)
    a.create("j1", "run", NOW)
    with pytest.raises(ActiveJobExists):
        a.create("j2", "run", NOW)
    b.create("j1", "run", NOW)  # another owner is unaffected, ids are per owner
    a.update("j1", status="running", started_at=NOW, planner_client="scripted")
    with pytest.raises(ActiveJobExists):
        a.create("j2", "mint", NOW)
    a.update("j1", status="succeeded", finished_at=LATER, order_ids=["o-1", "o-2"])
    a.create("j2", "mint", LATER)
    job = a.get("j1")
    assert job is not None
    assert job["status"] == "succeeded" and job["order_ids"] == ["o-1", "o-2"]
    assert job["planner_client"] == "scripted" and job["started_at"] == iso(NOW)
    assert [j["job_id"] for j in a.list()] == ["j2", "j1"]
    assert b.get("j2") is None


@requires_pg
def test_job_update_validates_fields(be) -> None:
    jobs = be.open_tenant(OWNER_A).jobs
    jobs.create("j1", "run", NOW)
    with pytest.raises(ValueError):
        jobs.update("j1", owner=OWNER_B)
    with pytest.raises(ValueError):
        jobs.update("j1", order_ids=[{"order": "body"}])
    with pytest.raises(KeyError):
        jobs.update("missing", status="running")
    jobs.update("j1", error_code="boom", status="failed")
    jobs.update("j1", error_code=None)
    job = jobs.get("j1")
    assert job is not None and job["error_code"] is None and job["status"] == "failed"


@requires_pg
def test_running_jobs_marked_interrupted(be) -> None:
    jobs = be.open_tenant(OWNER_A).jobs
    jobs.create("j1", "run", NOW)
    jobs.update("j1", status="running")
    assert jobs.mark_interrupted() == 1
    job = jobs.get("j1")
    assert job is not None and job["status"] == "interrupted"
    assert jobs.mark_interrupted() == 0
    jobs.create("j2", "run", NOW)  # no longer blocked


@requires_pg
def test_policy_selection_is_appended_per_owner(be) -> None:
    a, b = be.open_tenant(OWNER_A).policy_selection, be.open_tenant(OWNER_B).policy_selection
    assert isinstance(a, PolicySelectionStore)
    assert a.latest() is None
    a.append("conservative", "ips1", "sig1")
    a.append("balanced", "ips2", "sig2")
    b.append("growth", "ips3", "sig3")
    latest = a.latest()
    assert latest is not None
    assert (latest["seq"], latest["preset_id"], latest["ips_hash"]) == (1, "balanced", "ips2")
    assert b.latest()["seq"] == 0  # type: ignore[index]


@requires_pg
def test_owner_registry(be) -> None:
    owners = be.owners()
    assert isinstance(owners, OwnerRegistry)
    agent = "0x" + "c" * 40
    assert owners.get(OWNER_A) is None
    owners.create(OWNER_A, agent)
    row = owners.get(OWNER_A)
    assert row is not None and row["agent_address"] == agent
    with pytest.raises(ValueError):
        owners.create(OWNER_A, agent)
    assert owners.count_created_since(datetime.now(UTC) - timedelta(minutes=1)) == 1
    assert owners.count_created_since(datetime.now(UTC) + timedelta(minutes=1)) == 0
    with pytest.raises(ValueError):
        owners.create("0xABC", agent)


@requires_pg
def test_global_stop_setting_and_env(be, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_WALLET_GLOBAL_STOP", raising=False)
    settings = be.settings()
    assert settings.global_stop() is False
    settings.set_global_stop(True)
    assert be.settings().global_stop() is True
    settings.set_global_stop(False)
    assert be.settings().global_stop() is False
    monkeypatch.setenv("AGENT_WALLET_GLOBAL_STOP", "1")
    assert be.settings().global_stop() is True


@requires_pg
def test_backend_migrate_and_verify(be) -> None:
    be.migrate()
    assert be.verify_schema() >= 1
