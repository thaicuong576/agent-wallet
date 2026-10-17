"""Jobs: run, mint and withdraw as polled background jobs (design 8.2, 10).

One active job per owner, rate limits per owner per hour, a single worker pool across owners.
The jobs live in the owner's `JobStore` (`tenant.stores.jobs`: `webapp.sqlite` on SQLite, the
`jobs` table on Postgres) and hold ids only (job id, order ids, audit seqs, planner label, error
code), never an order body, a note or a key: the audit log is the truth. This module is the only
place that proposes with `origin="owner"` (design 7.3, 13); the gateway decides, the job only
reports what the gateway recorded.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import jsonschema
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import AuthProblem, Session, require_step_up
from api.chat_compose import (
    FALLBACK_LLM_ERROR,
    FALLBACK_UNCONFIGURED,
    compose_agent_text,
    text_for_error,
    user_facing_stop,
)
from api.wallet import current_tenant, get_webapp, problem_invalid
from core.agent import Planner, Reader, ScriptedClient
from core.agent.client import LLMClient, LLMError, OpenAIClient
from core.agent.planner import FallbackPlanner
from core.gateway.exceptions import GatewayBusy, GatewayError, InvalidOrder, ReconcileRequired
from core.gateway.tenant import Tenant
from core.runner.portfolio import PortfolioReader
from core.runner.runner import SKIP_PREFIX, Runner
from core.storage.interfaces import JobInProgress, JobStore, RateLimited
from core.storage.sqlite.webapp import SqliteJobs, mark_jobs_interrupted

log = logging.getLogger("agent_wallet.jobs")

ROOT_DIR = Path(__file__).resolve().parent.parent
ORDER_SCHEMA = ROOT_DIR / "contracts" / "schemas" / "order.schema.json"

KINDS = ("run", "mint", "withdraw", "chat")
# Chat action and run jobs share one hourly limit (ticket 115, chat-v3).
ACTION_KINDS = ("run", "chat_action")
RUN_LIKE = ("run", "chat")
CHAT_MESSAGE_MAX_CHARS = 500
ACTIVE = ("queued", "running")
STOP_CODES = frozenset({"TIER.EMERGENCY_STOP", "TIER.GLOBAL_STOP"})
OWNER_STOP_CODE = "TIER.EMERGENCY_STOP"
# Cancel (contract C2): the job ends `denied` with this code; steps carry CANCEL_REASON.
CANCEL_CODE = "OWNER_CANCELLED"
CANCEL_REASON = "owner-cancelled"
TERMINAL_PHASE = "done"
_SKIP_TAIL = re.compile(rf"^({re.escape(SKIP_PREFIX)} [^:]*): .*$", re.DOTALL)
GLOBAL_STOP_CODE = "TIER.GLOBAL_STOP"
# Execution error codes of the record schema, reported under the job table's names (design 10).
ERROR_CODE_MAP = {"SIGNER_UNAVAILABLE": "RPC_UNAVAILABLE"}
USDC_UNIT = 10**6


@dataclass(frozen=True)
class JobLimits:
    """Every job limit in one place (design 3, 7.3, 10)."""

    runs_per_hour: int = 6
    chat_inquiries_per_hour: int = 60
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
        raw_inq = env.get("AGENT_WALLET_CHAT_INQUIRIES_PER_HOUR", "")
        inq = (
            int(raw_inq)
            if re.fullmatch(r"[0-9]{1,6}", raw_inq) and int(raw_inq) > 0
            else cls.chat_inquiries_per_hour
        )
        return cls(mint_calls_per_hour=mint, chat_inquiries_per_hour=inq)

    @property
    def chat_inquiry_per_hour(self) -> int:
        return self.chat_inquiries_per_hour

    @property
    def chat_actions_per_hour(self) -> int:
        return self.runs_per_hour

    @property
    def chat_action_per_hour(self) -> int:
        return self.runs_per_hour

    def per_hour(self, kind: str) -> int | None:
        return {
            "run": self.runs_per_hour,
            "chat": self.runs_per_hour,
            "chat_action": self.runs_per_hour,
            "chat_inquiry": self.chat_inquiries_per_hour,
            "mint": self.mint_calls_per_hour,
        }.get(kind)


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


# ---------------------------------------------------------------- timestamps


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str) -> datetime:
    """Stored job times: microseconds on SQLite, whole seconds on Postgres rows."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError("unrecognised job timestamp")


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
    chat_text: str | None = None  # the agent message of a chat job, composed by code


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


# ---------------------------------------------------------------- cancel (contract C2)


def _order_label(order: Mapping[str, Any]) -> str:
    """'supply (10 USDC)': the same shape the runner uses for a skipped step."""
    amount = order.get("amount") or {}
    try:
        units, decimals = int(amount["value"]), int(amount["decimals"])
        whole, frac = divmod(units, 10**decimals)
        text = f"{whole}.{frac:0{decimals}d}".rstrip("0").rstrip(".") if decimals else str(whole)
    except (KeyError, TypeError, ValueError):
        text = "?"
    token = str(order.get("token") or "")
    return f"{order.get('function', 'step')} ({f'{text} {token}'.strip()})"


class _CancelGate:
    """The tenant's gateway as the job's runner sees it, with the owner's cancel applied.

    Cancellation points are exactly the calls to `propose`: once the owner has cancelled, no
    further order reaches the real gateway (so nothing is checked, simulated or signed), and each
    one is recorded as a skipped step with reason `owner-cancelled`. An order already inside
    `propose` is never interrupted. Everything else is delegated untouched.
    """

    def __init__(
        self,
        gateway: Any,
        cancelled: Callable[[], bool],
        on_order: Callable[[Any], None],
        on_skip: Callable[[], None],
        clock: Callable[[], datetime],
    ) -> None:
        self._gateway = gateway
        self._cancelled = cancelled
        self._on_order = on_order
        self._on_skip = on_skip
        self._clock = clock

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gateway, name)

    def propose(self, order: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if self._cancelled():
            self._on_skip()
            reason = f"{SKIP_PREFIX} {_order_label(order)}: {CANCEL_REASON}"
            return self._gateway.record_no_action(reason, _now_iso(self._clock()))
        self._on_order(order.get("order_id"))
        return self._gateway.propose(order, **kwargs)

    def record_no_action(self, reason: str, now: str) -> dict[str, Any]:
        # The runner words the steps after a blocked one itself; after a cancel they say why.
        if self._cancelled():
            reason = _SKIP_TAIL.sub(lambda m: m.group(1) + ": " + CANCEL_REASON, reason)
        return self._gateway.record_no_action(reason, now)


@dataclass
class _Live:
    """In-process state of one job that is queued or running (never stored, never an order body)."""

    cancel: bool = False
    skipped_by_cancel: bool = False
    order_id: str | None = None


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
        self._live: dict[str, _Live] = {}
        self._live_lock = threading.Lock()

    # -- storage

    @staticmethod
    def store(tenant: Tenant) -> JobStore:
        return tenant.stores.jobs

    def mark_interrupted_on_startup(self, target: Any) -> int:
        """At process start every queued or running job of every owner becomes `interrupted`.

        `target` is the StorageBackend, or a SQLite data directory.
        """
        if isinstance(target, (str, Path)):
            return mark_jobs_interrupted(target, self._clock())
        return int(target.mark_jobs_interrupted())

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
        elif kind == "chat":
            message = args.get("message")
            if not isinstance(message, str) or not (
                1 <= len(message.strip()) <= CHAT_MESSAGE_MAX_CHARS
            ):
                raise problem_invalid(f"message must be 1 to {CHAT_MESSAGE_MAX_CHARS} characters")
            args["message"] = message.strip()
        else:
            args["order"] = self._owner_order(tenant, kind, args)  # validated before queueing
        job_id = uuid.uuid4().hex
        try:
            if kind == "chat":
                create_kind = "chat_action"
                limit = self.limits.runs_per_hour
                count_kinds = ACTION_KINDS
                inquiry_limit = self.limits.chat_inquiries_per_hour
            elif kind == "run":
                create_kind = "run"
                limit = self.limits.runs_per_hour
                count_kinds = ACTION_KINDS
                inquiry_limit = None
            else:
                create_kind = kind
                limit = self.limits.per_hour(kind)
                count_kinds = None
                inquiry_limit = None
            self.store(tenant).create(
                job_id,
                create_kind,
                self._clock(),
                limit=limit,
                count_kinds=count_kinds,
                inquiry_limit=inquiry_limit,
            )
        except JobInProgress as exc:
            raise job_in_progress(exc.job_id) from None
        except RateLimited as exc:
            raise rate_limited(exc.retry_after_s) from None
        with self._live_lock:
            self._live[job_id] = _Live()
        if kind == "chat":
            try:
                tenant.stores.chat.append("user", args["message"], job_id, self._clock())
            except Exception:
                self.store(tenant).update(
                    job_id,
                    status="failed",
                    error_code="INTERNAL_ERROR",
                    finished_at=_ts(self._clock()),
                )
                self._forget(job_id)
                raise
        self._executor.submit(self._execute, tenant, job_id, kind, args)
        return job_id

    def get(self, tenant: Tenant, job_id: str) -> dict[str, Any] | None:
        row = self.store(tenant).get(job_id)
        return None if row is None else self._view(tenant, row)

    def phase(self, tenant: Tenant, row: Mapping[str, Any]) -> str:
        """Where a job is, from its status and the gateway's intent record (contract C1).

        `queued` until the worker takes it; `rules_check` while it plans and the gateway checks
        the rules; `sent` from the moment the gateway has written its signing intent for the
        order in flight until the record is appended; `done` for every terminal status. The
        gateway signs and waits for the receipt in one call, so `confirming` is not observable
        from outside it and is not emitted today.
        """
        status = row["status"]
        if status == "queued":
            return "queued"
        if status != "running":
            return TERMINAL_PHASE
        with self._live_lock:
            live = self._live.get(row["job_id"])
            order_id = live.order_id if live else None
        if order_id is not None:
            try:
                if tenant.gateway.intent_store.unfinished(order_id):
                    return "sent"
            except Exception:  # noqa: BLE001 - a phase must never break the job view
                pass
        return "rules_check"

    def cancel(self, tenant: Tenant, job_id: str) -> bool:
        """Ask a job to stop before its next order. False when it is not cancellable (409).

        Owner-scoped: the job is read from the owner's own store. Never touches the signer; the
        worker applies the flag at its cancellation points (`_CancelGate`, `_execute`).
        """
        row = self.store(tenant).get(job_id)
        if row is None or row["status"] not in ACTIVE:
            return False
        if self.phase(tenant, row) not in ("queued", "rules_check"):
            return False
        with self._live_lock:
            live = self._live.get(job_id)
            if live is None:
                return False
            live.cancel = True
        return True

    def _cancelled(self, job_id: str) -> bool:
        with self._live_lock:
            live = self._live.get(job_id)
            return live is not None and live.cancel

    def _was_cancelled(self, job_id: str) -> bool:
        """True when the cancel took effect: at least one order was skipped because of it."""
        with self._live_lock:
            live = self._live.get(job_id)
            return live is not None and live.skipped_by_cancel

    def _gate(self, tenant: Tenant, job_id: str) -> _CancelGate:
        def on_order(order_id: Any) -> None:
            with self._live_lock:
                live = self._live.get(job_id)
                if live is not None:
                    live.order_id = str(order_id) if order_id else None

        def on_skip() -> None:
            with self._live_lock:
                live = self._live.get(job_id)
                if live is not None:
                    live.skipped_by_cancel = True

        return _CancelGate(
            tenant.gateway, lambda: self._cancelled(job_id), on_order, on_skip, self._clock
        )

    def _forget(self, job_id: str) -> None:
        with self._live_lock:
            self._live.pop(job_id, None)

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
            "kind": (
                "chat" if row["kind"] in ("chat", "chat_action", "chat_inquiry") else row["kind"]
            ),
            "status": row["status"],
            "planner_client": row["planner_client"],
            "order_ids": row["order_ids"],
            "error_code": row["error_code"],
            "phase": self.phase(tenant, row),
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
            if self._cancelled(job_id):  # cancellation point 1: before the worker does anything
                outcome = self._cancelled_before_start(tenant)
            else:
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
            if kind == "chat":
                self._append_agent_message(tenant, job_id, outcome)
            fields: dict[str, Any] = {
                "status": outcome.status,
                "error_code": outcome.error_code,
                "finished_at": _ts(self._clock()),
            }
            if kind == "chat":
                fields["kind"] = "chat_inquiry" if not outcome.order_ids else "chat_action"
            if outcome.order_ids is not None:
                fields["order_ids"] = outcome.order_ids
            if outcome.seqs is not None:
                fields["seqs"] = outcome.seqs
            if outcome.planner_client != "none":
                fields["planner_client"] = outcome.planner_client
            store.update(job_id, **fields)
            self._forget(job_id)

    def _cancelled_before_start(self, tenant: Tenant) -> Outcome:
        record = tenant.gateway.record_no_action(
            "Cancelled by the owner before the agent acted.", _now_iso(self._clock())
        )
        text = "You cancelled this request before I acted. Nothing was sent."
        return Outcome("denied", CANCEL_CODE, [], [int(record["seq"])], chat_text=text)

    def _append_agent_message(self, tenant: Tenant, job_id: str, outcome: Outcome) -> None:
        """The agent's chat message, written before the job turns terminal so a poller that sees
        the job finish also finds the message."""
        text = outcome.chat_text or text_for_error(outcome.error_code or "INTERNAL_ERROR")
        try:
            tenant.stores.chat.append("agent", text, job_id, self._clock())
        except Exception as exc:  # noqa: BLE001 - never leave a job running
            log.warning("job %s chat message not stored: %s", job_id, type(exc).__name__)

    def _dispatch(
        self, tenant: Tenant, store: JobStore, job_id: str, kind: str, args: dict[str, Any]
    ) -> Outcome:
        if kind == "run":
            return self._run(tenant, store, job_id, args)
        if kind == "chat":
            return self._chat(tenant, store, job_id, args)
        return self._owner_job(tenant, job_id, kind, args["order"])

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

    def _owner_job(self, tenant: Tenant, job_id: str, kind: str, order: dict[str, Any]) -> Outcome:
        # `transfer` must work under the owner's stop (design 7.3); the gateway still decides.
        code = self._stopped(tenant, owner_stop_counts=False)
        if code is not None:
            return Outcome("denied", code)
        gas_error = self._gas_gate(tenant)
        if gas_error is not None:
            return gas_error
        if self._cancelled(job_id):  # cancellation point 2: before the single order is proposed
            return self._cancelled_before_start(tenant)
        record = propose_as_owner(tenant.gateway, order)
        status, error = classify([record], kind)
        return Outcome(status, error, [order["order_id"]], [int(record["seq"])])

    # -- run

    def _run(self, tenant: Tenant, store: JobStore, job_id: str, args: dict[str, Any]) -> Outcome:
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
        portfolio = PortfolioReader(self._rpc, tenant.agent_address, tenant.stores.ledger)  # type: ignore[arg-type]
        ips = tenant.gateway.policy.ips
        try:
            records = self._runner(tenant, job_id, portfolio, ips, label, planner).run_once(
                note, now
            )
        except LLMError:
            # Nothing is proposed before the LLM answers, so retrying with the rule-based
            # planner cannot duplicate an order.
            label, planner = "fallback", FallbackPlanner()
            store.update(job_id, planner_client=label)
            records = self._runner(tenant, job_id, portfolio, ips, label, planner).run_once(
                note, now
            )
        status, error = self._apply_cancel(job_id, *classify(records, "run"))
        return Outcome(
            status,
            error,
            [r["order_id"] for r in records if r.get("order_id")],
            [int(r["seq"]) for r in records],
            label,
        )

    # -- chat

    def _chat(self, tenant: Tenant, store: JobStore, job_id: str, args: dict[str, Any]) -> Outcome:
        """One owner message: plan, then run the orders one by one through the gateway.

        The agent text is composed from what the gateway recorded; the model's `reply` only rides
        along under its own label.
        """
        now = self._clock()
        code = self._stopped(tenant, owner_stop_counts=True)
        if code is not None:
            reason = (
                "Chat request not handled: the operator's global stop is set."
                if code == GLOBAL_STOP_CODE
                else "Chat request not handled: the emergency stop is engaged."
            )
            record = tenant.gateway.record_no_action(reason, _now_iso(now))
            return Outcome(
                "denied",
                code,
                [],
                [int(record["seq"])],
                chat_text=compose_agent_text([record], stop_text=user_facing_stop(code)),
            )
        gas_error = self._gas_gate(tenant)
        if gas_error is not None:
            return gas_error

        label, planner = self._select_planner("auto")
        fallback = FALLBACK_UNCONFIGURED if label == "fallback" else None
        store.update(job_id, planner_client=label)
        portfolio = PortfolioReader(self._rpc, tenant.agent_address, tenant.stores.ledger)  # type: ignore[arg-type]
        ips = tenant.gateway.policy.ips
        message = args["message"]
        try:
            result = self._runner(tenant, job_id, portfolio, ips, label, planner).run_instruction(
                message, now
            )
        except LLMError:
            # Nothing is proposed before the model answers, so the fallback cannot duplicate.
            label, planner = "fallback", FallbackPlanner()
            fallback = FALLBACK_LLM_ERROR
            store.update(job_id, planner_client=label)
            result = self._runner(tenant, job_id, portfolio, ips, label, planner).run_instruction(
                message, now
            )
        records = result.records
        status, error = self._apply_cancel(job_id, *classify(records, "chat"))
        text = compose_agent_text(
            records,
            reply=result.reply,
            plan_reason=result.plan_reason,
            validation_errors=result.validation_errors,
            fallback=fallback,
        )
        return Outcome(
            status,
            error,
            [r["order_id"] for r in records if r.get("order_id")],
            [int(r["seq"]) for r in records],
            label,
            chat_text=text,
        )

    def _apply_cancel(self, job_id: str, status: str, error: str | None) -> tuple[str, str | None]:
        """A cancel that skipped orders ends the job `denied` (code OWNER_CANCELLED), unless an
        order already failed, which stays the truth."""
        if status != "failed" and self._was_cancelled(job_id):
            return "denied", CANCEL_CODE
        return status, error

    def _runner(
        self,
        tenant: Tenant,
        job_id: str,
        portfolio: PortfolioReader,
        ips: Any,
        label: str,
        planner: Any,
    ) -> Runner:
        reader_client = planner.client if label == "openai" else None
        gateway = self._gate(tenant, job_id)  # cancellation point 3: before every order
        return Runner(Reader(reader_client), planner, portfolio, gateway, ips, self._clock)

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


@router.post("/jobs/{job_id}/cancel", status_code=202, tags=["jobs"])
def cancel_job(
    job_id: str, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, str]:
    jobs = get_webapp(request).jobs
    job = None
    if len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id):
        job = jobs.store(tenant).get(job_id)
    if job is None:
        raise AuthProblem(404, "not-found", "Not found", "unknown job")
    if not jobs.cancel(tenant, job_id):
        raise AuthProblem(
            409,
            "not-cancellable",
            "Job cannot be cancelled",
            "the job has already sent a transaction or finished",
            extra={"job_id": job_id, "error": "not-cancellable"},
        )
    return {"job_id": job_id, "status": "cancelling"}


@router.get("/jobs", tags=["jobs"])
def list_jobs(
    request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    return {"items": get_webapp(request).jobs.list(tenant)}


__all__ = ["JobInProgress", "JobLimits", "JobService", "RateLimited", "SqliteJobs", "router"]
