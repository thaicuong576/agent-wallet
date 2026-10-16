"""Gateway module: propose, policy, simulate, sign, record (default deny).

This is the only module allowed to import `core.signer` (enforced by boundary checks).
"""

from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import json
import os
import pathlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import jsonschema

from core.gateway.audit import AuditSink, InMemoryAuditSink, canonical
from core.gateway.exceptions import (
    GatewayError,
    InvalidOrder,
    OrderIdConflict,
    ReconcileRequired,
)
from core.gateway.globalstop import file_flag
from core.gateway.intents import IntentStore
from core.gateway.lock import GatewayLock
from core.gateway.policy import DEFAULT_IPS_HASH, DenyAllPolicy, Policy
from core.protocols import registry
from core.signer import Signer
from core.simulate import Simulator

OWNER_FUNCTIONS = frozenset({"mint", "transfer"})
ORIGINS = ("planner", "owner")


class _GlobalStopAtSigning(Exception):
    """Internal: the global stop was set after simulation, before signing."""


def _accepts_kwarg(fn: Any, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


SCHEMAS_DIR = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "schemas"


@functools.lru_cache(maxsize=8)
def _get_validator(schema_name: str) -> jsonschema.Draft202012Validator:
    schema_path = SCHEMAS_DIR / f"{schema_name}.schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Contract schema not found: {schema_path}")
    schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema_data)


def _sanitize_error_detail(err: Any) -> str:
    msg = str(err).replace("\n", " ").strip()
    # Redact any 32-byte hex keys (64 hex characters) to protect secrets
    sanitized = re.sub(r"\b[0-9a-fA-F]{64}\b", "[REDACTED_KEY]", msg).strip()
    return sanitized[:500] if sanitized else "Transaction execution failed"


class Gateway:
    """Core transaction execution gateway with default-deny policy."""

    def __init__(
        self,
        policy: Policy | None = None,
        simulator: Simulator | None = None,
        signer: Signer | None = None,
        audit_sink: AuditSink | None = None,
        clock: Any = None,
        ips_hash: str | None = None,
        tx_builder: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
        tier: str = "T2",
        emergency_stop: bool = False,
        tier_view: Any | None = None,
        request_approval: Callable[[str], None] | None = None,
        lock: GatewayLock | None = None,
        intent_store: IntentStore | None = None,
        global_stop: Callable[[], bool] | None = None,
        owner_address: str | None = None,
    ) -> None:
        self.ips_hash = ips_hash or getattr(policy, "ips_hash", None) or DEFAULT_IPS_HASH
        self.policy: Policy = (
            policy if policy is not None else DenyAllPolicy(ips_hash=self.ips_hash)
        )
        self.simulator = simulator
        self.signer = signer
        self.audit_sink: AuditSink = audit_sink if audit_sink is not None else InMemoryAuditSink()
        self.tx_builder = tx_builder
        self.clock = clock
        self.tier = tier
        self.emergency_stop = emergency_stop
        self.tier_view = tier_view
        self.request_approval = request_approval
        # Default reads AGENT_WALLET_GLOBAL_STOP at call time; a hosted deployment injects a
        # reader backed by its settings store. Never "not stopped" by default.
        self.global_stop: Callable[[], bool] = (
            global_stop if global_stop is not None else file_flag(None, os.environ)
        )
        self.owner_address = owner_address

        if lock is not None:
            self.lock = lock
        elif hasattr(self.audit_sink, "db_path") and self.audit_sink.db_path != ":memory:":
            lock_path = pathlib.Path(self.audit_sink.db_path).parent / "gateway.lock.sqlite"
            self.lock = GatewayLock(lock_path)
        else:
            self.lock = GatewayLock(":memory:")

        if intent_store is not None:
            self.intent_store = intent_store
        elif hasattr(self.audit_sink, "db_path"):
            self.intent_store = IntentStore(self.audit_sink.db_path)
        else:
            self.intent_store = IntentStore(":memory:")

    def _now_iso(self) -> str:
        if self.clock is not None:
            if callable(self.clock):
                val = self.clock()
            else:
                val = self.clock
            if isinstance(val, datetime):
                return val.strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(val, str):
                return val
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _read_tier_and_stop(self) -> tuple[str, bool]:
        """Read tier and emergency stop per propose, fail-closed."""
        if self.tier_view is None:
            return self.tier, self.emergency_stop
        try:
            is_stop = bool(self.tier_view.is_stopped())
            current_tier = str(self.tier_view.current())
            return current_tier, is_stop
        except Exception:
            # Fail closed: any error reading the view means deny with TIER.EMERGENCY_STOP
            return "T0", True

    def _read_global_stop(self) -> bool:
        """Read the operator global stop, fail-closed: any error means stopped."""
        try:
            return bool(self.global_stop())
        except Exception:
            return True

    def _tier_gate(
        self, order_id: str, tier: str, is_stop: bool, global_stop: bool = False
    ) -> dict[str, Any] | None:
        """Fail-safe: stops and read-only tiers deny before policy or signer."""
        code = reason = None
        if global_stop:
            code = "TIER.GLOBAL_STOP"
            reason = "Global stop is engaged; no order is executed."
        elif is_stop:
            code = "TIER.EMERGENCY_STOP"
            reason = "Emergency stop is engaged; no order is executed."
        elif tier == "T0":
            code, reason = "TIER.READ_ONLY", "Tier T0 is read-only; no order is executed."
        elif tier == "T1":
            code, reason = "TIER.PROPOSE_ONLY", "Tier T1 is propose-only; no order is executed."

        if code is None:
            return None

        return {
            "schema_version": 1,
            "order_id": order_id,
            "decision": "deny",
            "rules_hit": [
                {
                    "code": code,
                    "evidence": (
                        {"global_stop": True} if code == "TIER.GLOBAL_STOP" else {"tier": str(tier)}
                    ),
                }
            ],
            "reason": reason,
            "ips_hash": self.ips_hash,
            "decided_at": self._now_iso(),
        }

    def _owner_only_denial(self, order: dict[str, Any], origin: str) -> dict[str, Any]:
        """Defence in depth: owner functions from any other origin never reach the policy."""
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "decision": "deny",
            "rules_hit": [
                {
                    "code": "ACCESS.OWNER_ONLY_FUNCTION",
                    "evidence": {"function": str(order["function"]), "origin": origin},
                }
            ],
            "reason": f"Owner function '{order['function']}' requires owner origin.",
            "ips_hash": self.ips_hash,
            "decided_at": self._now_iso(),
        }

    def _registry_denial(
        self, order: dict[str, Any], rules_hit: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Protocol not registered or market not listed: denied before policy, nothing built."""
        return {
            "schema_version": 1,
            "order_id": order["order_id"],
            "decision": "deny",
            "rules_hit": rules_hit,
            "reason": "Order targets a protocol or market that is not enabled.",
            "ips_hash": self.ips_hash,
            "decided_at": self._now_iso(),
        }

    def _get_approval_info(self, order_id: str) -> tuple[str | None, str | None, str | None]:
        """Return (status, decided_at, decided_by) from tier_view or None if unknown."""
        if self.tier_view is None:
            return None, None, None
        try:
            status = self.tier_view.approval_status(order_id)
            decided_at = None
            decided_by = "owner"
            if hasattr(self.tier_view, "get_approval"):
                rec = self.tier_view.get_approval(order_id)
                if rec:
                    decided_at = rec.get("decided_at")
                    decided_by = rec.get("decided_by") or "owner"
            return status, decided_at, decided_by
        except Exception:
            return None, None, None

    def _execute_order(self, order: dict[str, Any], order_id: str) -> tuple[str, dict[str, Any]]:
        """Build tx requests, simulate, sign and send. Returns (outcome, execution)."""
        if self.tx_builder is None:
            raise GatewayError("tx_builder is not configured; no default transaction is built")
        try:
            if order.get("function") == "transfer" and _accepts_kwarg(
                self.tx_builder, "owner_address"
            ):
                calls_raw = self.tx_builder(order, owner_address=self.owner_address)
            else:
                calls_raw = self.tx_builder(order)
            calls = [calls_raw] if isinstance(calls_raw, dict) else list(calls_raw)
            if not calls:
                raise GatewayError("tx_builder returned no calls")
        except Exception as e:
            detail = _sanitize_error_detail(f"tx build: {e}")
            execution = {
                "schema_version": 1,
                "order_id": order_id,
                "status": "failed",
                "tx_hash": None,
                "chain_id": 84532,
                "block_number": None,
                "gas_used": None,
                "error_code": "SIMULATION_FAILED",
                "detail": detail,
                "executed_at": self._now_iso(),
            }
            exec_validator = _get_validator("execution-result")
            exec_errors = list(exec_validator.iter_errors(execution))
            if exec_errors:
                raise GatewayError(
                    f"ExecutionResult failed schema validation: {exec_errors[0].message}"
                ) from e
            return "failed", execution

        # Pre-flight simulation. Calls depend on each other (approve, then supply), so several
        # calls are simulated as one bundle in a single block; a simulator without bundle
        # support fails closed.
        total_gas: int | None = None
        sim_failed = False
        sim_err_msg = ""

        # Simulate as the account that will sign: without `from` an RPC simulates from the zero
        # address and judges balances and allowances of the wrong account.
        sender = getattr(self.signer, "address", None)
        sim_calls = [
            {**call, "from": sender} if sender and "from" not in call else call for call in calls
        ]

        if self.simulator is None:
            sim_failed = True
            sim_err_msg = "Simulator not configured"
        elif len(calls) > 1 and not hasattr(self.simulator, "simulate_bundle"):
            sim_failed = True
            sim_err_msg = "Bundle simulation is not supported by this simulator"
        else:
            try:
                if len(calls) == 1:
                    sim_result = self.simulator.simulate(sim_calls[0])
                else:
                    sim_result = self.simulator.simulate_bundle(sim_calls)
            except Exception as e:
                sim_failed = True
                sim_err_msg = _sanitize_error_detail(e)
            else:
                if not sim_result.ok:
                    sim_failed = True
                    sim_err_msg = _sanitize_error_detail(
                        sim_result.error or "Simulation rejected transaction"
                    )
                total_gas = sim_result.gas_used

        if sim_failed:
            outcome = "failed"
            execution = {
                "schema_version": 1,
                "order_id": order_id,
                "status": "failed",
                "tx_hash": None,
                "chain_id": 84532,
                "block_number": None,
                "gas_used": total_gas,
                "error_code": "SIMULATION_FAILED",
                "detail": sim_err_msg,
                "executed_at": self._now_iso(),
            }
        else:
            # Sign and send each call with the injected Signer
            sign_failed = False
            sign_err_msg = ""
            sign_err_code = "SIGNER_UNAVAILABLE"
            last_tx_hash = None

            if self.signer is None:
                sign_failed = True
                sign_err_msg = "Signer not configured"
            else:
                # Global stop, read again after simulation and immediately before the intent
                # row and the signer; fail-closed. Nothing is written or signed if it is set.
                if self._read_global_stop():
                    raise _GlobalStopAtSigning
                if self.intent_store is not None:
                    body_hash = hashlib.sha256(canonical(order)).hexdigest()
                    self.intent_store.begin_signing(order_id, body_hash, self._now_iso())
                for call in calls:
                    try:
                        receipt = self.signer.sign_and_send(call)
                        if hasattr(receipt, "tx_hash"):
                            th = str(receipt.tx_hash)
                        elif isinstance(receipt, dict) and "tx_hash" in receipt:
                            th = str(receipt["tx_hash"])
                        else:
                            th = str(receipt)
                        if not th.startswith("0x"):
                            th = f"0x{th}"
                        last_tx_hash = th.lower()
                    except Exception as e:
                        sign_failed = True
                        sign_err_msg = _sanitize_error_detail(e)
                        if "revert" in str(e).lower():
                            sign_err_code = "TX_REVERTED"
                        else:
                            sign_err_code = "SIGNER_UNAVAILABLE"
                        break

            if sign_failed:
                outcome = "failed"
                execution = {
                    "schema_version": 1,
                    "order_id": order_id,
                    "status": "failed",
                    "tx_hash": None,
                    "chain_id": 84532,
                    "block_number": None,
                    "gas_used": total_gas,
                    "error_code": sign_err_code,
                    "detail": sign_err_msg,
                    "executed_at": self._now_iso(),
                }
            else:
                outcome = "executed"
                execution = {
                    "schema_version": 1,
                    "order_id": order_id,
                    "status": "executed",
                    "tx_hash": last_tx_hash,
                    "chain_id": 84532,
                    "block_number": None,
                    "gas_used": total_gas,
                    "error_code": None,
                    "detail": None,
                    "executed_at": self._now_iso(),
                }

        # Validate execution result if present
        exec_validator = _get_validator("execution-result")
        exec_errors = list(exec_validator.iter_errors(execution))
        if exec_errors:
            raise GatewayError(
                f"ExecutionResult failed schema validation: {exec_errors[0].message}"
            )

        return outcome, execution

    def _execute_or_stop(
        self, order: dict[str, Any], order_id: str, decision: dict[str, Any]
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        """Execute; if the global stop is hit at signing time, swap in a denial."""
        try:
            outcome, execution = self._execute_order(order, order_id)
        except _GlobalStopAtSigning:
            denial = {
                "schema_version": 1,
                "order_id": order_id,
                "decision": "deny",
                "rules_hit": [{"code": "TIER.GLOBAL_STOP", "evidence": {"global_stop": True}}],
                "reason": "Global stop is engaged; the order was not signed.",
                "ips_hash": self.ips_hash,
                "decided_at": self._now_iso(),
            }
            return "denied", None, denial
        return outcome, execution, decision

    def record_no_action(self, reason: str, now: str) -> dict[str, Any]:
        """Record a no_action decision directly into the audit sink."""
        current_tier, emergency_stop = self._read_tier_and_stop()
        clean_reason = str(reason).strip()[:500] if str(reason).strip() else "no action"
        record = {
            "schema_version": 1,
            "seq": 0,
            "order_id": None,
            "order": None,
            "decision": None,
            "no_action": {
                "rules_hit": [],
                "reason": clean_reason,
            },
            "outcome": "no_action",
            "execution": None,
            "approval": None,
            "tier": current_tier,
            "emergency_stop": emergency_stop,
            "ips_hash": self.ips_hash,
            "created_at": now,
            "prev_hash": "0" * 64,
            "hash": "0" * 64,
        }
        stored_record = self.audit_sink.append(record)
        dr_validator = _get_validator("decision-record")
        dr_errors = list(dr_validator.iter_errors(stored_record))
        if dr_errors:
            raise GatewayError(f"DecisionRecord failed schema validation: {dr_errors[0].message}")
        return copy.deepcopy(stored_record)

    def _evaluate_policy(
        self, order: dict[str, Any], origin: str, tier: str, emergency_stop: bool
    ) -> dict[str, Any]:
        """Call the policy; pass origin, tier and stops to policies that accept them."""
        evaluate = self.policy.evaluate
        extra: dict[str, Any] = {}
        for name, value in (
            ("origin", origin),
            ("tier", tier),
            ("emergency_stop", emergency_stop),
            ("global_stop", self._read_global_stop()),
        ):
            if _accepts_kwarg(evaluate, name):
                extra[name] = value
        return evaluate(order, **extra)

    def _find_audit_record(self, order_id: str) -> dict[str, Any] | None:
        """Find latest audit record for order_id from audit sink."""
        if hasattr(self.audit_sink, "find_by_order_id"):
            return self.audit_sink.find_by_order_id(order_id)
        if hasattr(self.audit_sink, "records"):
            for rec in reversed(self.audit_sink.records):
                if rec.get("order_id") == order_id:
                    return rec
        return None

    def propose(
        self, order: dict[str, Any], *, origin: Literal["planner", "owner"] = "planner"
    ) -> dict[str, Any]:
        """Propose an order through policy evaluation, simulation, signing, and audit recording.

        `origin` is set by code only (never read from the order body): "planner" for runner and
        multi-mode `POST /orders`, "owner" for the API's mint and withdraw handlers.
        """
        if origin not in ORIGINS:
            raise InvalidOrder(f"Unknown origin: {origin!r}")
        # 1. Validate order against order.schema.json
        if not isinstance(order, dict):
            raise InvalidOrder("Order must be a dictionary")

        order_validator = _get_validator("order")
        errors = list(order_validator.iter_errors(order))
        if errors:
            raise InvalidOrder(f"Order failed schema validation: {errors[0].message}")

        order_id = order["order_id"]

        with self.lock.held():
            # 2. Idempotency check via audit log and intent store
            stored_record = self._find_audit_record(order_id)
            if stored_record is not None:
                stored_order = stored_record.get("order")
                if order != stored_order:
                    raise OrderIdConflict(
                        f"Order ID {order_id} conflict: different order body already recorded"
                    )

                current_tier, emergency_stop = self._read_tier_and_stop()
                tier_deny_decision = self._tier_gate(order_id, current_tier, emergency_stop)
                approval_status, decided_at, decided_by = self._get_approval_info(order_id)

                is_emergency_stop_denial = stored_record.get("outcome") == "denied" and any(
                    h.get("code") == "TIER.EMERGENCY_STOP"
                    for h in stored_record.get("decision", {}).get("rules_hit", [])
                )
                if stored_record.get("outcome") in ("executed", "failed"):
                    return copy.deepcopy(stored_record)
                elif stored_record.get("outcome") == "pending_approval":
                    if tier_deny_decision is None and approval_status not in (
                        "approved",
                        "rejected",
                    ):
                        return copy.deepcopy(stored_record)
                elif is_emergency_stop_denial and not emergency_stop:
                    pass
                elif stored_record.get("outcome") == "denied" and any(
                    h.get("code") == "TIER.GLOBAL_STOP"
                    for h in stored_record.get("decision", {}).get("rules_hit", [])
                ):
                    if self._read_global_stop():
                        return copy.deepcopy(stored_record)
                else:
                    return copy.deepcopy(stored_record)
            elif self.intent_store is not None and self.intent_store.unfinished(order_id):
                intent = self.intent_store.get_intent(order_id)
                if intent:
                    body_h = hashlib.sha256(canonical(order)).hexdigest()
                    if intent.get("body_hash") != body_h:
                        raise OrderIdConflict(
                            f"Order ID {order_id} conflict: "
                            "different order body already recorded in intent"
                        )
                raise ReconcileRequired(
                    f"Order {order_id} has an unfinished signing intent; manual reconcile required"
                )

            # 3. Read tier and stop status per propose (fail-closed)
            current_tier, emergency_stop = self._read_tier_and_stop()

            # 4. Check global stop (fast denial), emergency stop and read-only tiers. A transfer
            # to the owner is exempt from the owner stop and from T0/T1 (design 7.3): getting
            # funds out must work when the agent is stopped; the global stop still denies it.
            owner_transfer = origin == "owner" and order["function"] == "transfer"
            if owner_transfer:
                tier_deny_decision = self._tier_gate(
                    order_id, "T2", False, self._read_global_stop()
                )
            else:
                tier_deny_decision = self._tier_gate(
                    order_id, current_tier, emergency_stop, self._read_global_stop()
                )

            # 5. Check approval status for escalated orders
            approval_status, decided_at, decided_by = self._get_approval_info(order_id)

            now_str = self._now_iso()

            # 6. Fail-safe: tier or emergency stop denial
            if tier_deny_decision is not None:
                record = {
                    "schema_version": 1,
                    "seq": 0,
                    "order_id": order_id,
                    "order": copy.deepcopy(order),
                    "decision": copy.deepcopy(tier_deny_decision),
                    "no_action": None,
                    "outcome": "denied",
                    "execution": None,
                    "approval": None,
                    "tier": current_tier,
                    "emergency_stop": emergency_stop,
                    "ips_hash": self.ips_hash,
                    "created_at": now_str,
                    "prev_hash": "0" * 64,
                    "hash": "0" * 64,
                }
                stored_record = self.audit_sink.append(record)
                dr_validator = _get_validator("decision-record")
                dr_errors = list(dr_validator.iter_errors(stored_record))
                if dr_errors:
                    raise GatewayError(
                        f"DecisionRecord failed schema validation: {dr_errors[0].message}"
                    )
                return copy.deepcopy(stored_record)

            # 7. Handling rejected orders: never executes
            if approval_status == "rejected":
                reject_decision = {
                    "schema_version": 1,
                    "order_id": order_id,
                    "decision": "deny",
                    "rules_hit": [
                        {
                            "code": "TIER.APPROVAL_REJECTED",
                            "evidence": {"order_id": order_id},
                        }
                    ],
                    "reason": "The owner rejected the pending order.",
                    "ips_hash": self.ips_hash,
                    "decided_at": now_str,
                }
                record = {
                    "schema_version": 1,
                    "seq": 0,
                    "order_id": order_id,
                    "order": copy.deepcopy(order),
                    "decision": reject_decision,
                    "no_action": None,
                    "outcome": "denied",
                    "execution": None,
                    "approval": {
                        "status": "rejected",
                        "by": decided_by or "owner",
                        "at": decided_at or now_str,
                    },
                    "tier": current_tier,
                    "emergency_stop": emergency_stop,
                    "ips_hash": self.ips_hash,
                    "created_at": now_str,
                    "prev_hash": "0" * 64,
                    "hash": "0" * 64,
                }
                stored_record = self.audit_sink.append(record)
                dr_validator = _get_validator("decision-record")
                dr_errors = list(dr_validator.iter_errors(stored_record))
                if dr_errors:
                    raise GatewayError(
                        f"DecisionRecord failed schema validation: {dr_errors[0].message}"
                    )
                return copy.deepcopy(stored_record)

            # 8. Policy evaluation
            if order["function"] in OWNER_FUNCTIONS and origin != "owner":
                raw_decision = self._owner_only_denial(order, origin)
            else:
                raw_decision = self._evaluate_policy(order, origin, current_tier, emergency_stop)
            # The registry never widens what the policy allowed: an allow or escalate for a
            # protocol with no adapter, or a market the adapter does not list, becomes a deny.
            if isinstance(raw_decision, dict) and raw_decision.get("decision") != "deny":
                registry_hits = registry.preflight(order)
                if registry_hits:
                    raw_decision = self._registry_denial(order, registry_hits)
            if not isinstance(raw_decision, dict):
                raise GatewayError("Policy evaluate must return a dictionary")

            decision = dict(raw_decision)
            if "schema_version" not in decision:
                decision["schema_version"] = 1
            if "order_id" not in decision or decision["order_id"] is None:
                decision["order_id"] = order_id
            if "ips_hash" not in decision or decision["ips_hash"] is None:
                decision["ips_hash"] = self.ips_hash
            if "decided_at" not in decision or decision["decided_at"] is None:
                decision["decided_at"] = self._now_iso()

            decision_validator = _get_validator("decision")
            dec_errors = list(decision_validator.iter_errors(decision))
            if dec_errors:
                raise GatewayError(f"Decision failed schema validation: {dec_errors[0].message}")

            dec_outcome = decision["decision"]
            outcome: str
            execution: dict[str, Any] | None = None
            approval: dict[str, Any] | None = None

            if dec_outcome == "deny":
                outcome = "denied"
                execution = None
            elif dec_outcome == "escalate":
                if approval_status == "approved":
                    approval = {
                        "status": "approved",
                        "by": decided_by or "owner",
                        "at": decided_at or now_str,
                    }
                    outcome, execution, decision = self._execute_or_stop(order, order_id, decision)
                else:
                    outcome = "pending_approval"
                    execution = None
                    if self.request_approval is not None and approval_status is None:
                        try:
                            self.request_approval(order_id)
                        except Exception:
                            pass
            elif dec_outcome == "allow":
                outcome, execution, decision = self._execute_or_stop(order, order_id, decision)
            else:
                raise GatewayError(f"Unsupported decision outcome: {dec_outcome}")

            # 9. Construct DecisionRecord and append to audit sink
            record = {
                "schema_version": 1,
                "seq": 0,
                "order_id": order_id,
                "order": copy.deepcopy(order),
                "decision": copy.deepcopy(decision),
                "no_action": None,
                "outcome": outcome,
                "execution": copy.deepcopy(execution),
                "approval": copy.deepcopy(approval),
                "tier": current_tier,
                "emergency_stop": emergency_stop,
                "ips_hash": self.ips_hash,
                "created_at": now_str,
                "prev_hash": "0" * 64,
                "hash": "0" * 64,
            }

            stored_record = self.audit_sink.append(record)

            # Validate returned record against decision-record.schema.json
            dr_validator = _get_validator("decision-record")
            dr_errors = list(dr_validator.iter_errors(stored_record))
            if dr_errors:
                raise GatewayError(
                    f"DecisionRecord failed schema validation: {dr_errors[0].message}"
                )

            return copy.deepcopy(stored_record)
