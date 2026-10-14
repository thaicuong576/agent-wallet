"""Gateway module: propose, policy, simulate, sign, record (default deny).

This is the only module allowed to import `core.signer` (enforced by boundary checks).
"""

from __future__ import annotations

import copy
import functools
import json
import pathlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import jsonschema

from core.gateway.audit import AuditSink, InMemoryAuditSink
from core.gateway.exceptions import GatewayError, InvalidOrder, OrderIdConflict
from core.gateway.policy import DEFAULT_IPS_HASH, DenyAllPolicy, Policy
from core.signer import Signer
from core.simulate import Simulator

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
        self._orders: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        # Orders that reached the signing step but were not recorded: never retried automatically.
        self._in_flight: set[str] = set()

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

    def _tier_gate(self, order_id: str, tier: str, is_stop: bool) -> dict[str, Any] | None:
        """Fail-safe: emergency stop and read-only tiers deny before policy or signer."""
        code = reason = None
        if is_stop:
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
            "rules_hit": [{"code": code, "evidence": {"tier": str(tier)}}],
            "reason": reason,
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
            elif hasattr(self.tier_view, "_store") and hasattr(
                self.tier_view._store, "get_approval"
            ):
                rec = self.tier_view._store.get_approval(order_id)
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
        calls_raw = self.tx_builder(order)
        calls = [calls_raw] if isinstance(calls_raw, dict) else list(calls_raw)
        if not calls:
            raise GatewayError("tx_builder returned no calls")

        # Pre-flight simulation. Calls depend on each other (approve, then supply), so several
        # calls are simulated as one bundle in a single block; a simulator without bundle
        # support fails closed.
        total_gas: int | None = None
        sim_failed = False
        sim_err_msg = ""

        if self.simulator is None:
            sim_failed = True
            sim_err_msg = "Simulator not configured"
        elif len(calls) > 1 and not hasattr(self.simulator, "simulate_bundle"):
            sim_failed = True
            sim_err_msg = "Bundle simulation is not supported by this simulator"
        else:
            try:
                if len(calls) == 1:
                    sim_result = self.simulator.simulate(calls[0])
                else:
                    sim_result = self.simulator.simulate_bundle(calls)
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
                self._in_flight.add(order_id)
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

    def propose(self, order: dict[str, Any]) -> dict[str, Any]:
        """Propose an order through policy evaluation, simulation, signing, and audit recording."""
        # 1. Validate order against order.schema.json
        if not isinstance(order, dict):
            raise InvalidOrder("Order must be a dictionary")

        order_validator = _get_validator("order")
        errors = list(order_validator.iter_errors(order))
        if errors:
            raise InvalidOrder(f"Order failed schema validation: {errors[0].message}")

        order_id = order["order_id"]

        # 2. In-flight check
        if order_id in self._in_flight:
            raise GatewayError(
                f"Order {order_id} reached signing but was not recorded; reconcile before retrying"
            )

        # 3. Read tier and stop status per propose (fail-closed)
        current_tier, emergency_stop = self._read_tier_and_stop()

        # 4. Check emergency stop and read-only tiers
        tier_deny_decision = self._tier_gate(order_id, current_tier, emergency_stop)

        # 5. Check approval status for escalated orders
        approval_status, decided_at, decided_by = self._get_approval_info(order_id)

        # 6. Idempotency: same order_id never executes twice
        if order_id in self._orders:
            stored_order, stored_record = self._orders[order_id]
            if order != stored_order:
                raise OrderIdConflict(
                    f"Order ID {order_id} conflict: different order body already recorded"
                )
            # If the order was denied due to emergency stop, releasing the stop allows again
            is_emergency_stop_denial = stored_record.get("outcome") == "denied" and any(
                h.get("code") == "TIER.EMERGENCY_STOP"
                for h in stored_record.get("decision", {}).get("rules_hit", [])
            )
            # Re-propose of a pending order only advances if stopped, approved, or rejected
            if stored_record["outcome"] == "pending_approval":
                if tier_deny_decision is None and approval_status not in ("approved", "rejected"):
                    return copy.deepcopy(stored_record)
            elif is_emergency_stop_denial and not emergency_stop:
                # Emergency stop was released: allow the order to be re-evaluated
                pass
            else:
                return copy.deepcopy(stored_record)

        now_str = self._now_iso()

        # 7. Fail-safe: tier or emergency stop denial
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
            self._orders[order_id] = (copy.deepcopy(order), copy.deepcopy(stored_record))
            return copy.deepcopy(stored_record)

        # 8. Handling rejected orders: never executes
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
            self._orders[order_id] = (copy.deepcopy(order), copy.deepcopy(stored_record))
            return copy.deepcopy(stored_record)

        # 9. Policy evaluation
        raw_decision = self.policy.evaluate(order)
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
                outcome, execution = self._execute_order(order, order_id)
            else:
                outcome = "pending_approval"
                execution = None
                if self.request_approval is not None and approval_status is None:
                    try:
                        self.request_approval(order_id)
                    except Exception:
                        pass
        elif dec_outcome == "allow":
            outcome, execution = self._execute_order(order, order_id)
        else:
            raise GatewayError(f"Unsupported decision outcome: {dec_outcome}")

        # 10. Construct DecisionRecord and append to audit sink
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
            raise GatewayError(f"DecisionRecord failed schema validation: {dr_errors[0].message}")

        # Store for idempotency; the order is now safely recorded
        self._orders[order_id] = (copy.deepcopy(order), copy.deepcopy(stored_record))
        self._in_flight.discard(order_id)

        return copy.deepcopy(stored_record)
