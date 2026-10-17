"""SQLite stores of the webapp side and the SQLite StorageBackend (design 5, 13).

Per owner, next to the gateway files: ``DATA_DIR/owners/<0x address>/webapp.sqlite`` holds the job,
top-up and policy-selection tables. The keystore is a file, ``keystore.json``. Nothing here changes
the layout the webapp used before the StorageBackend existed, so a data directory keeps working.

The backend never imports ``core.gateway`` (only the gateway package may import the intent store and
the custody module), so those factories are injected by ``core.gateway.tenant.sqlite_backend``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.storage.interfaces import (
    AuthStore,
    IntentStore,
    JobInProgress,
    KeystoreStore,
    RateLimited,
)
from core.storage.sqlite.chat import SqliteChat
from core.storage.sqlite.tenant import SqliteTenantStores, open_tenant, tenant_dir

WEBAPP_DB = "webapp.sqlite"
_TRUTHY = {"1", "true", "yes", "on"}


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class SqliteJobs:
    """JobStore for one owner's ``webapp.sqlite``. A connection per call; the partial unique index
    is the atomic one-active-job rule (design 5.1, 10)."""

    _COLUMNS = frozenset(
        {
            "kind",
            "status",
            "planner_client",
            "order_ids",
            "seqs",
            "error_code",
            "started_at",
            "finished_at",
        }
    )
    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS jobs(
            job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            planner_client TEXT NOT NULL DEFAULT 'none', order_ids TEXT NOT NULL DEFAULT '[]',
            seqs TEXT NOT NULL DEFAULT '[]', error_code TEXT,
            created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active ON jobs((1))
            WHERE status IN ('queued', 'running');
        CREATE INDEX IF NOT EXISTS jobs_kind_created ON jobs(kind, created_at);
    """

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        with self._connect() as db:
            db.executescript(self._SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=30, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            yield db

    def create(
        self,
        job_id: str,
        kind: str,
        created_at: datetime,
        *,
        limit: int | None = None,
        window: timedelta = timedelta(hours=1),
        count_kinds: Sequence[str] | None = None,
        inquiry_limit: int | None = None,
    ) -> None:
        """Insert a queued job. Raises JobInProgress, or RateLimited when `limit` is reached."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                active = db.execute(
                    "SELECT job_id FROM jobs WHERE status IN ('queued', 'running')"
                ).fetchone()
                if active is not None:
                    raise JobInProgress(active["job_id"])
                since = _ts(created_at - window)
                if inquiry_limit is not None:
                    rows_inq = db.execute(
                        "SELECT created_at FROM jobs WHERE kind = 'chat_inquiry'"
                        " AND created_at > ? ORDER BY created_at",
                        (since,),
                    ).fetchall()
                    if len(rows_inq) >= inquiry_limit:
                        oldest = _parse(rows_inq[len(rows_inq) - inquiry_limit]["created_at"])
                        wait = int((oldest + window - created_at).total_seconds()) + 1
                        raise RateLimited(wait)
                if limit is not None:
                    kinds = list(count_kinds or (kind,))
                    marks = ", ".join("?" for _ in kinds)  # placeholders only, values are bound
                    rows = db.execute(
                        f"SELECT created_at FROM jobs WHERE kind IN ({marks})"  # noqa: S608
                        " AND created_at > ? ORDER BY created_at",
                        (*kinds, since),
                    ).fetchall()
                    if len(rows) >= limit:
                        oldest = _parse(rows[len(rows) - limit]["created_at"])
                        wait = int((oldest + window - created_at).total_seconds()) + 1
                        raise RateLimited(wait)
                db.execute(
                    "INSERT INTO jobs(job_id, kind, status, created_at) VALUES (?, ?, 'queued', ?)",
                    (job_id, kind, _ts(created_at)),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def update(self, job_id: str, **fields: Any) -> None:
        unknown = set(fields) - self._COLUMNS
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        sets = ", ".join(f"{name} = ?" for name in fields)  # names come from the allowlist above
        values = [
            json.dumps(v) if name in ("order_ids", "seqs") else v for name, v in fields.items()
        ]
        with self._connect() as db:
            db.execute(f"UPDATE jobs SET {sets} WHERE job_id = ?", (*values, job_id))  # noqa: S608

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["order_ids"] = json.loads(out["order_ids"])
        out["seqs"] = json.loads(out["seqs"])
        return out

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def mark_interrupted(self, now: datetime | None = None) -> int:
        stamp = _ts(now or datetime.now(UTC))
        with self._connect() as db:
            return db.execute(
                "UPDATE jobs SET status = 'interrupted', finished_at = ?"
                " WHERE status IN ('queued', 'running')",
                (stamp,),
            ).rowcount


class SqliteTopups:
    """Top-up rows in one owner's ``webapp.sqlite``. A connection per call, no shared state."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS topups(
            tx_hash TEXT PRIMARY KEY, amount_wei TEXT NOT NULL, status TEXT NOT NULL,
            block_number INTEGER, created_at TEXT NOT NULL)
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute(self._SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self._path), timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def get(self, tx_hash: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM topups WHERE tx_hash = ?", (tx_hash,)).fetchone()
        return dict(row) if row else None

    def put(self, tx_hash: str, amount_wei: int, now: datetime) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO topups(tx_hash, amount_wei, status, created_at)"
                " VALUES (?, ?, 'pending', ?)",
                (tx_hash, str(amount_wei), _ts(now)),
            )
        existing = self.get(tx_hash)
        assert existing is not None
        return existing

    def settle(self, tx_hash: str, status: str, block_number: int | None) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE topups SET status = ?, block_number = ? WHERE tx_hash = ?"
                " AND status = 'pending'",
                (status, block_number, tx_hash),
            )


class SqlitePolicySelection:
    """Append-only policy choices of one owner (the table has insert and select only)."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS policy_selection(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, preset_id TEXT NOT NULL,
            ips_hash TEXT NOT NULL, signature_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS policy_selection_no_update BEFORE UPDATE ON policy_selection
            BEGIN SELECT RAISE(ABORT, 'policy_selection is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS policy_selection_no_delete BEFORE DELETE ON policy_selection
            BEGIN SELECT RAISE(ABORT, 'policy_selection is append-only'); END;
    """

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        with self._connect() as db:
            db.executescript(self._SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=30, isolation_level=None)) as db:
            yield db

    def append(self, preset_id: str, ips_hash: str, signature_hash: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO policy_selection(preset_id, ips_hash, signature_hash, created_at)"
                " VALUES (?, ?, ?, ?)",
                (preset_id, ips_hash, signature_hash, _ts(datetime.now(UTC))),
            )

    def latest(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT seq, preset_id, ips_hash, signature_hash, created_at"
                " FROM policy_selection ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "seq": row[0],
            "preset_id": row[1],
            "ips_hash": row[2],
            "signature_hash": row[3],
            "created_at": row[4],
        }


class SqliteSettings:
    """Operator stop: ``AGENT_WALLET_GLOBAL_STOP=1`` or the ``GLOBAL_STOP`` file. Fails closed."""

    def __init__(self, data_dir: Path | str, env: Mapping[str, str] | None = None) -> None:
        self._data_dir = Path(data_dir)
        self._env = env

    def global_stop(self) -> bool:
        try:
            env = os.environ if self._env is None else self._env
            return (
                env.get("AGENT_WALLET_GLOBAL_STOP", "").strip().lower() in _TRUTHY
                or (self._data_dir / "GLOBAL_STOP").exists()
            )
        except Exception:  # noqa: BLE001 - fail closed
            return True


def mark_jobs_interrupted(data_dir: Path | str, now: datetime | None = None) -> int:
    """Mark the queued and running jobs of every owner directory under ``data_dir``."""
    total = 0
    root = Path(data_dir) / "owners"
    if not root.is_dir():
        return 0
    for child in sorted(root.iterdir()):
        db_file = child / WEBAPP_DB
        if db_file.is_file():
            total += SqliteJobs(db_file).mark_interrupted(now)
    return total


class SqliteBackend:
    """StorageBackend over the per-owner SQLite layout. No connection is opened at construction.

    ``intents`` and ``keystore`` are injected by ``core.gateway.tenant.sqlite_backend``: only the
    gateway package may import the intent store and the custody module. ``auth`` builds the shared
    auth store on first use.
    """

    def __init__(
        self,
        data_dir: Path | str,
        *,
        intents: Callable[[Path], IntentStore],
        keystore: Callable[[Path], KeystoreStore],
        auth: Callable[[], AuthStore] | None = None,
        env: Mapping[str, str] | None = None,
        lock_timeout_s: float = 15.0,
        price_fn: Callable[[str], Decimal] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self._intents = intents
        self._keystore = keystore(self.data_dir / "owners")
        self._auth_factory = auth
        self._auth: AuthStore | None = None
        self._guard = threading.Lock()
        self._settings = SqliteSettings(self.data_dir, env)
        self._lock_timeout_s = lock_timeout_s
        self._price_fn = price_fn

    def open_tenant(self, owner: str) -> SqliteTenantStores:
        base = open_tenant(
            self.data_dir,
            owner,
            intents=self._intents,
            lock_timeout_s=self._lock_timeout_s,
            price_fn=self._price_fn,
        )
        webapp_db = base.path / WEBAPP_DB
        return replace(
            base,
            keystore=self._keystore,
            jobs=SqliteJobs(webapp_db),
            policy_selection=SqlitePolicySelection(webapp_db),
            topups=SqliteTopups(webapp_db),
            chat=SqliteChat(webapp_db),
        )

    def keystore_admin(self) -> KeystoreStore:
        """Unbound keystore handle: asking about an owner creates nothing."""
        return self._keystore

    def auth(self) -> AuthStore:
        with self._guard:
            if self._auth is None:
                if self._auth_factory is None:
                    raise RuntimeError("this SQLite backend was built without an auth store")
                self._auth = self._auth_factory()
            return self._auth

    def settings(self) -> SqliteSettings:
        return self._settings

    def mark_jobs_interrupted(self, now: datetime | None = None) -> int:
        """Startup recovery: every queued or running job of every owner becomes interrupted."""
        return mark_jobs_interrupted(self.data_dir, now)

    def migrate(self) -> None:
        """Nothing to migrate: every SQLite store creates its own tables."""

    def close(self) -> None:
        close = getattr(self._auth, "close", None)
        if callable(close):
            close()


__all__ = [
    "WEBAPP_DB",
    "SqliteBackend",
    "SqliteJobs",
    "SqlitePolicySelection",
    "SqliteSettings",
    "SqliteTopups",
    "mark_jobs_interrupted",
    "tenant_dir",
]
