"""Jobs: run, mint and withdraw as polled background jobs (design 8.2, 10).

One active job per owner, rate limits per owner per hour, a single worker pool across owners.
The `jobs` table lives in the owner's own `webapp.sqlite` and holds ids only (job id, order ids,
audit seqs, planner label, error code), never an order body, a note or a key: the audit log is the
truth. This module is the only place that proposes with `origin="owner"` (design 7.3, 13); the
gateway decides, the job only reports what the gateway recorded.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import jsonschema
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import AuthProblem, Session, require_step_up
from api.wallet import current_tenant, get_webapp, problem_invalid
from core.agent import Planner, Reader, ScriptedClient
from core.agent.client import LLMClient, LLMError, OpenAIClient
from core.agent.planner import FallbackPlanner
from core.gateway.exceptions import GatewayBusy, GatewayError, InvalidOrder, ReconcileRequired
from core.gateway.tenant import Tenant
from core.runner.ledger import AuditLedger
from core.runner.portfolio import PortfolioReader
from core.runner.runner import Runner

log = logging.getLogger("agent_wallet.jobs")

ROOT_DIR = Path(__file__).resolve().parent.parent
ORDER_SCHEMA = ROOT_DIR / "contracts" / "schemas" / "order.schema.json"
WEBAPP_DB = "webapp.sqlite"

KINDS = ("run", "mint", "withdraw")
ACTIVE = ("queued", "running")
STOP_CODES = frozenset({"TIER.EMERGENCY_STOP", "TIER.GLOBAL_STOP"})
OWNER_STOP_CODE = "TIER.EMERGENCY_STOP"
GLOBAL_STOP_CODE = "TIER.GLOBAL_STOP"
# Execution error codes of the record schema, reported under the job table's names (design 10).
ERROR_CODE_MAP = {"SIGNER_UNAVAILABLE": "RPC_UNAVAILABLE"}
USDC_UNIT = 10**6


@dataclass(frozen=True)
class JobLimits:
    """Every job limit in one place (design 3, 7.3, 10)."""

    runs_per_hour: int = 6
    mint_calls_per_hour: int = 4
    note_max_chars: int = 2000

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JobLimits:
        env = os.environ if env is None else env
        raw = env.get("AGENT_WALLET_MINT_CALLS_PER_HOUR", "")
        mint = (
            int(raw)
            if re.fullmatch(r"[0-9]{1,6}", raw) and int(raw) > 0
            else cls.mint_calls_per_hour
        )
        return cls(mint_calls_per_hour=mint)

    def per_hour(self, kind: str) -> int | None:
        return {"run": self.runs_per_hour, "mint": self.mint_calls_per_hour}.get(kind)


# ---------------------------------------------------------------- problems (RFC 7807)


def job_in_progress(job_id: str) -> AuthProblem:
    return AuthProblem(
        409,
        "job-in-progress",
        "A job is already running",
        "wait for the active job to finish",
        extra={"job_id": job_id},
    )


def rate_limited(retry_after_s: int) -> AuthProblem:
    return AuthProblem(
        429,
        "rate-limited",
        "Too many requests",
        "the hourly limit for this action is used up",
        headers={"Retry-After": str(max(1, retry_after_s))},
    )


class JobInProgress(Exception):
    def __init__(self, job_id: str) -> None:
        super().__init__(job_id)
        self.job_id = job_id


class RateLimited(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__(retry_after_s)
        self.retry_after_s = retry_after_s


# ---------------------------------------------------------------- jobs table (per owner)


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class SqliteJobs:
    """JobStore for one owner's `webapp.sqlite`. A connection per call; the partial unique index
    is the atomic one-active-job rule (design 5.1, 10)."""

    _COLUMNS = frozenset(
        {"status", "planner_client", "order_ids", "seqs", "error_code", "started_at", "finished_at"}
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
                if limit is not None:
                    since = _ts(created_at - window)
                    rows = db.execute(
                        "SELECT created_at FROM jobs WHERE kind = ? AND created_at > ?"
                        " ORDER BY created_at",
                        (kind, since),
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


# ---------------------------------------------------------------- planners and the owner path


@lru_cache(maxsize=1)
def _order_validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads(ORDER_SCHEMA.read_text(encoding="utf-8")))


def _hostile_plan() -> str:
    """What a compromised LLM might emit: a schema-valid supply far above the per-tx limit."""
    order = {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": "2026-01-01T00:00:00Z",
        "proposed_by": "planner",
        "protocol": "aave-v3",
        "function": "supply",
        "token": "USDC",
        "amount": {"value": "1000000000000", "decimals": 6},
        "recipient": "self",
    }
    return json.dumps({"action": "propose", "orders": [order]})


def propose_with_origin(gateway: Any, order: dict[str, Any], *, origin: str) -> dict[str, Any]:
    """Call `Gateway.propose`, passing `origin` when the gateway accepts it (ticket C3).

    Until C3 lands the keyword does not exist and the call is a plain `propose(order)`, where the
    engine treats the order as planner-origin and denies owner functions: fail closed.
    """
    if "origin" in inspect.signature(gateway.propose).parameters:
        return gateway.propose(order, origin=origin)
    return gateway.propose(order)


def propose_as_owner(gateway: Any, order: dict[str, Any]) -> dict[str, Any]:
    return propose_with_origin(gateway, order, origin="owner")


def _now_iso(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Outcome:
    status: str
    error_code: str | None = None
    order_ids: list[str] | None = None
    seqs: list[int] | None = None
    planner_client: str = "none"


def _rule_codes(record: Mapping[str, Any]) -> list[str]:
    block = record.get("decision") or record.get("no_action") or {}
    return [str(h.get("code")) for h in block.get("rules_hit", [])]


def classify(records: list[dict[str, Any]], kind: str) -> tuple[str, str | None]:
    """Map the gateway's records to a job status and error code (design 10 table)."""
    for rec in records:
        if rec.get("outcome") == "failed":
            code = str((rec.get("execution") or {}).get("error_code") or "EXECUTION_FAILED")
            return "failed", ERROR_CODE_MAP.get(code, code)
    for rec in records:
        stop = STOP_CODES.intersection(_rule_codes(rec))
        if rec.get("outcome") == "denied" and stop:
            return "denied", sorted(stop)[0]
    if kind != "run" and any(rec.get("outcome") == "denied" for rec in records):
        codes = [c for rec in records for c in _rule_codes(rec)]
        return "denied", codes[0] if codes else None
    return "succeeded", None


# ---------------------------------------------------------------- service


class JobService:
    def __init__(
        self,
        *,
        rpc: Callable[[str, list[Any]], Any],
        gas_state: Callable[[str], tuple[int, int]],
        global_stop: Callable[[], bool],
        limits: JobLimits | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        executor: Executor | None = None,
        llm_factory: Callable[[], LLMClient | None] | None = None,
    ) -> None:
        self._rpc = rpc
        self._gas_state = gas_state
        self._global_stop = global_stop
        self.limits = limits or JobLimits()
        self._clock = clock
        self._executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="job")
        self._llm_factory = llm_factory or _llm_from_env

    # -- storage

    @staticmethod
    def store(tenant: Tenant) -> SqliteJobs:
        return SqliteJobs(tenant.paths.root / WEBAPP_DB)

    def mark_interrupted_on_startup(self, data_dir: Path) -> int:
        """At process start every queued or running job of every owner becomes `interrupted`."""
        total = 0
        for db_file in sorted((Path(data_dir) / "owners").glob(f"*/{WEBAPP_DB}")):
            total += SqliteJobs(db_file).mark_interrupted(self._clock())
        return total

    # -- public API

    def start(self, tenant: Tenant, kind: str, args: Mapping[str, Any]) -> str:
        """Queue a job and return its id. Raises AuthProblem 409, 422 or 429."""
        if kind not in KINDS:
            raise problem_invalid("unknown job kind")
        args = dict(args)
        if kind == "run":
            note = args.get("note") or ""
            if not isinstance(note, str) or len(note) > self.limits.note_max_chars:
                raise problem_invalid(
                    f"note must be at most {self.limits.note_max_chars} characters"
                )
        else:
            args["order"] = self._owner_order(tenant, kind, args)  # validated before queueing
        job_id = uuid.uuid4().hex
        try:
            self.store(tenant).create(job_id, kind, self._clock(), limit=self.limits.per_hour(kind))
        except JobInProgress as exc:
            raise job_in_progress(exc.job_id) from None
        except RateLimited as exc:
            raise rate_limited(exc.retry_after_s) from None
        self._executor.submit(self._execute, tenant, job_id, kind, args)
        return job_id

    def get(self, tenant: Tenant, job_id: str) -> dict[str, Any] | None:
        row = self.store(tenant).get(job_id)
        return None if row is None else self._view(tenant, row)

    def list(self, tenant: Tenant, limit: int = 20) -> list[dict[str, Any]]:
        return [
            self._view(tenant, row, with_records=False) for row in self.store(tenant).list(limit)
        ]

    # -- views

    def _view(
        self, tenant: Tenant, row: dict[str, Any], with_records: bool = True
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "job_id": row["job_id"],
            "kind": row["kind"],
            "status": row["status"],
            "planner_client": row["planner_client"],
            "order_ids": row["order_ids"],
            "error_code": row["error_code"],
            "created_at": _iso(row["created_at"]),
            "finished_at": _iso(row["finished_at"]) if row["finished_at"] else None,
        }
        if with_records:
            out["records"] = [self._summary(tenant, row, seq) for seq in row["seqs"]]
        return out

    @staticmethod
    def _summary(tenant: Tenant, row: dict[str, Any], seq: int) -> dict[str, Any]:
        try:
            rec = tenant.gateway.audit_sink[seq]
        except Exception:  # noqa: BLE001 - a missing record must not break the job view
            return {"seq": seq, "outcome": "unknown", "planner_client": row["planner_client"]}
        order = rec.get("order") or {}
        block = rec.get("decision") or rec.get("no_action") or {}
        execution = rec.get("execution") or {}
        return {
            "seq": rec.get("seq"),
            "order_id": rec.get("order_id"),
            "outcome": rec.get("outcome"),
            "function": order.get("function"),
            "token": order.get("token"),
            "amount": order.get("amount"),
            "rule_codes": _rule_codes(rec),
            "reason": block.get("reason"),
            "tx_hash": execution.get("tx_hash"),
            "planner_client": row["planner_client"],
        }

    # -- owner orders (mint, withdraw)

    def _owner_order(self, tenant: Tenant, kind: str, args: Mapping[str, Any]) -> dict[str, Any]:
        now = _now_iso(self._clock())
        order: dict[str, Any] = {
            "schema_version": 1,
            "order_id": str(uuid.uuid4()),
            "created_at": now,
            "proposed_by": "owner",
        }
        if kind == "mint":
            whole = str(args.get("amount_usdc", ""))
            if not re.fullmatch(r"[1-9][0-9]{0,6}", whole):
                raise problem_invalid("amount_usdc must be a whole number from 1 to 9999999")
            order.update(
                protocol="aave-v3",
                function="mint",
                token="USDC",
                amount={"value": str(int(whole) * USDC_UNIT), "decimals": 6},
                recipient="self",
            )
        else:
            order.update(
                protocol="wallet",
                function="transfer",
                token=args.get("token"),
                amount=args.get("amount"),
                recipient="owner",
            )
        errors = list(_order_validator().iter_errors(order))
        if errors:
            raise problem_invalid(f"order is not valid: {errors[0].message}"[:300])
        return order

    # -- worker

    def _execute(self, tenant: Tenant, job_id: str, kind: str, args: dict[str, Any]) -> None:
        store = self.store(tenant)
        outcome = Outcome("failed", "INTERNAL_ERROR")
        try:
            store.update(job_id, status="running", started_at=_ts(self._clock()))
            outcome = self._dispatch(tenant, store, job_id, kind, args)
        except GatewayBusy:
            outcome = Outcome("failed", "GATEWAY_BUSY")
        except ReconcileRequired:
            outcome = Outcome("failed", "RECONCILE_REQUIRED")
        except (InvalidOrder, GatewayError) as exc:
            log.warning("job %s gateway error: %s", job_id, type(exc).__name__)
            outcome = Outcome("failed", "GATEWAY_ERROR")
        except Exception as exc:  # noqa: BLE001 - never leak text, never leave a job running
            log.warning("job %s failed: %s", job_id, type(exc).__name__)
        finally:
            fields: dict[str, Any] = {
                "status": outcome.status,
                "error_code": outcome.error_code,
                "finished_at": _ts(self._clock()),
            }
            if outcome.order_ids is not None:
                fields["order_ids"] = outcome.order_ids
            if outcome.seqs is not None:
                fields["seqs"] = outcome.seqs
            if outcome.planner_client != "none":
                fields["planner_client"] = outcome.planner_client
            store.update(job_id, **fields)

    def _dispatch(
        self, tenant: Tenant, store: SqliteJobs, job_id: str, kind: str, args: dict[str, Any]
    ) -> Outcome:
        if kind == "run":
            return self._run(tenant, store, job_id, args)
        return self._owner_job(tenant, kind, args["order"])

    def _gas_gate(self, tenant: Tenant) -> Outcome | None:
        """Below the gas floor nothing is proposed (design 8.1)."""
        try:
            balance, floor = self._gas_state(tenant.agent_address)
        except Exception:  # noqa: BLE001
            return Outcome("failed", "RPC_UNAVAILABLE")
        if balance < floor:
            return Outcome("failed", "GAS_BELOW_FLOOR")
        return None

    def _stopped(self, tenant: Tenant, *, owner_stop_counts: bool) -> str | None:
        """Rule code of the stop in force, or None. An unreadable flag fails closed."""
        try:
            if self._global_stop():
                return GLOBAL_STOP_CODE
        except Exception:  # noqa: BLE001
            return GLOBAL_STOP_CODE
        if owner_stop_counts:
            try:
                if tenant.tier_view.is_stopped():
                    return OWNER_STOP_CODE
            except Exception:  # noqa: BLE001
                return OWNER_STOP_CODE
        return None

    def _owner_job(self, tenant: Tenant, kind: str, order: dict[str, Any]) -> Outcome:
        # `transfer` must work under the owner's stop (design 7.3); the gateway still decides.
        code = self._stopped(tenant, owner_stop_counts=False)
        if code is not None:
            return Outcome("denied", code)
        gas_error = self._gas_gate(tenant)
        if gas_error is not None:
            return gas_error
        record = propose_as_owner(tenant.gateway, order)
        status, error = classify([record], kind)
        return Outcome(status, error, [order["order_id"]], [int(record["seq"])])

    # -- run

    def _run(self, tenant: Tenant, store: SqliteJobs, job_id: str, args: dict[str, Any]) -> Outcome:
        now = self._clock()
        code = self._stopped(tenant, owner_stop_counts=True)
        if code is not None:
            reason = (
                "Run not started: the operator's global stop is set."
                if code == GLOBAL_STOP_CODE
                else "Run not started: the emergency stop is engaged."
            )
            record = tenant.gateway.record_no_action(reason, _now_iso(now))
            return Outcome("denied", code, [], [int(record["seq"])])

        gas_error = self._gas_gate(tenant)
        if gas_error is not None:
            return gas_error

        note = args.get("note") or ""
        label, planner = self._select_planner(args.get("planner", "auto"))
        store.update(job_id, planner_client=label)
        with closing(AuditLedger(tenant.paths.audit)) as ledger:
            portfolio = PortfolioReader(self._rpc, tenant.agent_address, ledger)
            ips = tenant.gateway.policy.ips
            try:
                records = self._runner(tenant, portfolio, ips, label, planner).run_once(note, now)
            except LLMError:
                # Nothing is proposed before the LLM answers, so retrying with the rule-based
                # planner cannot duplicate an order.
                label, planner = "fallback", FallbackPlanner()
                store.update(job_id, planner_client=label)
                records = self._runner(tenant, portfolio, ips, label, planner).run_once(note, now)
        status, error = classify(records, "run")
        return Outcome(
            status,
            error,
            [r["order_id"] for r in records if r.get("order_id")],
            [int(r["seq"]) for r in records],
            label,
        )

    def _runner(
        self, tenant: Tenant, portfolio: PortfolioReader, ips: Any, label: str, planner: Any
    ) -> Runner:
        reader_client = planner.client if label == "openai" else None
        return Runner(Reader(reader_client), planner, portfolio, tenant.gateway, ips, self._clock)

    def _select_planner(self, choice: str) -> tuple[str, Any]:
        if choice == "scripted_hostile":
            return "scripted", Planner(ScriptedClient(_hostile_plan()))
        client = self._llm_factory()
        if client is not None:
            return "openai", Planner(client)
        return "fallback", FallbackPlanner()


def _llm_from_env() -> LLMClient | None:
    return OpenAIClient() if os.environ.get("OPENAI_API_KEY", "").strip() else None


def _iso(stored: str) -> str:
    return _parse(stored).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- routes

router = APIRouter()


class MintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_usdc: str = Field(pattern=r"^[1-9][0-9]{0,6}$")


class WithdrawAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str
    decimals: int


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Literal["USDC", "ETH"]
    amount: WithdrawAmount


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)
    planner: Literal["auto", "scripted_hostile"] = "auto"


def _accepted(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "status": "queued"}


@router.post("/agent/mint", status_code=202, tags=["agent"])
def post_mint(
    body: MintRequest, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, str]:
    jobs = get_webapp(request).jobs
    return _accepted(jobs.start(tenant, "mint", {"amount_usdc": body.amount_usdc}))


@router.post("/agent/withdraw", status_code=202, tags=["agent"])
def post_withdraw(
    body: WithdrawRequest,
    request: Request,
    _session: Annotated[Session, Depends(require_step_up("withdraw"))],
    tenant: Annotated[Tenant, Depends(current_tenant)],
) -> dict[str, str]:
    jobs = get_webapp(request).jobs
    return _accepted(jobs.start(tenant, "withdraw", body.model_dump()))


@router.post("/runs", status_code=202, tags=["jobs"])
def post_run(
    request: Request,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    body: RunRequest | None = None,
) -> dict[str, str]:
    body = body or RunRequest()
    jobs = get_webapp(request).jobs
    return _accepted(jobs.start(tenant, "run", {"note": body.note, "planner": body.planner}))


@router.get("/jobs/{job_id}", tags=["jobs"])
def get_job(
    job_id: str, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    job = None
    if len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id):
        job = get_webapp(request).jobs.get(tenant, job_id)
    if job is None:
        raise AuthProblem(404, "not-found", "Not found", "unknown job")
    return job


@router.get("/jobs", tags=["jobs"])
def list_jobs(
    request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    return {"items": get_webapp(request).jobs.list(tenant)}


__all__ = ["JobLimits", "JobService", "SqliteJobs", "router"]
