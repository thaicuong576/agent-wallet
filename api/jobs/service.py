"""Job service managing background job lifecycle."""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api.chat_compose import (
    FALLBACK_LLM_ERROR,
    FALLBACK_UNCONFIGURED,
    compose_agent_text,
    text_for_error,
    user_facing_stop,
)
from api.wallet import problem_invalid
from core.agent import Planner, Reader, ScriptedClient
from core.agent.client import LLMClient, LLMError
from core.agent.planner import FallbackPlanner
from core.gateway.exceptions import GatewayBusy, GatewayError, InvalidOrder, ReconcileRequired
from core.gateway.tenant import Tenant
from core.runner.portfolio import PortfolioReader
from core.runner.runner import Runner
from core.storage.interfaces import JobInProgress, JobStore, RateLimited
from core.storage.sqlite.webapp import SqliteJobs, mark_jobs_interrupted

from .constants import (
    ACTION_KINDS,
    ACTIVE,
    CANCEL_CODE,
    CHAT_MESSAGE_MAX_CHARS,
    GLOBAL_STOP_CODE,
    KINDS,
    OWNER_STOP_CODE,
    TERMINAL_PHASE,
    USDC_UNIT,
)
from .gateway_calls import (
    _hostile_plan,
    _llm_from_env,
    _order_validator,
    propose_as_owner,
)
from .history import _build_chat_history
from .limits import JobLimits, job_in_progress, rate_limited
from .live import _CancelGate, _Live
from .outcome import Outcome, _iso, _now_iso, _rule_codes, _ts, classify

log = logging.getLogger("agent_wallet.jobs")
timing_log = logging.getLogger("agent_wallet.timing")


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
                tenant.stores.chat.append(
                    "user",
                    args["message"],
                    job_id,
                    self._clock(),
                    thread_id=args.get("thread_id"),
                )
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

    def tokens_since(self, job_id: str, index: int) -> list[str]:
        with self._live_lock:
            live = self._live.get(job_id)
            if live is None:
                return []
            return list(live.tokens[index:])

    def list(
        self, tenant: Tenant, limit: int = 20, *, with_records: bool = False
    ) -> list[dict[str, Any]]:
        return [
            self._view(tenant, row, with_records=with_records)
            for row in self.store(tenant).list(limit)
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
        return Outcome(
            "denied", CANCEL_CODE, [], [int(record["seq"])], chat_text=text, intent="refusal"
        )

    def _append_agent_message(self, tenant: Tenant, job_id: str, outcome: Outcome) -> None:
        """The agent's chat message, written before the job turns terminal so a poller that sees
        the job finish also finds the message."""
        text = outcome.chat_text or text_for_error(outcome.error_code or "INTERNAL_ERROR")
        try:
            tenant.stores.chat.append("agent", text, job_id, self._clock(), intent=outcome.intent)
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
        """Below the gas floor nothing is proposed."""
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
        # `transfer` must work under the owner's stop; the gateway still decides.
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
        t0 = time.perf_counter()
        try:
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
                    intent="refusal",
                )
            gas_error = self._gas_gate(tenant)
            if gas_error is not None:
                gas_error.intent = "refusal"
                return gas_error

            thread_id = args.get("thread_id")
            if thread_id is None:
                try:
                    thread_id = tenant.stores.chat.get_thread_id(job_id)
                except Exception:  # noqa: BLE001
                    thread_id = None
            history = _build_chat_history(tenant, job_id, thread_id, store)

            label, planner = self._select_planner("auto")
            fallback = FALLBACK_UNCONFIGURED if label == "fallback" else None
            store.update(job_id, planner_client=label)
            portfolio = PortfolioReader(self._rpc, tenant.agent_address, tenant.stores.ledger)  # type: ignore[arg-type]
            ips = tenant.gateway.policy.ips
            message = args["message"]

            first_token_seen = False

            def on_token(token: str) -> None:
                nonlocal first_token_seen
                if not token:
                    return
                if not first_token_seen:
                    first_token_seen = True
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    timing_log.info(
                        "job=%s phase=planner_first_token elapsed_ms=%d",
                        job_id,
                        elapsed_ms,
                    )
                with self._live_lock:
                    live = self._live.get(job_id)
                    if live is None:
                        return
                    current_len = sum(len(t) for t in live.tokens)
                    if current_len >= 4000:
                        return
                    avail = 4000 - current_len
                    live.tokens.append(token[:avail])

            try:
                runner = self._runner(tenant, job_id, portfolio, ips, label, planner)
                result = runner.run_instruction(
                    message, now, conversation_history=history, on_token=on_token
                )
            except LLMError:
                with self._live_lock:
                    live = self._live.get(job_id)
                    if live is not None:
                        live.tokens.clear()
                # Nothing is proposed before the model answers, so the fallback cannot duplicate.
                label, planner = "fallback", FallbackPlanner()
                fallback = FALLBACK_LLM_ERROR
                store.update(job_id, planner_client=label)
                runner = self._runner(tenant, job_id, portfolio, ips, label, planner)
                result = runner.run_instruction(
                    message, now, conversation_history=history, on_token=on_token
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
                intent=result.intent,
            )
        finally:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            timing_log.info("job=%s phase=chat_total elapsed_ms=%d", job_id, elapsed_ms)

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


__all__ = ["JobService", "SqliteJobs", "mark_jobs_interrupted"]
