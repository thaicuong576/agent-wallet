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
    ) -> None:
        self.ips_hash = ips_hash or DEFAULT_IPS_HASH
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

    def _tier_gate(self, order_id: str) -> dict[str, Any] | None:
        """Fail-safe: emergency stop and read-only tiers deny before policy or signer."""
        code = reason = None
        if self.emergency_stop:
            code, reason = "TIER.EMERGENCY_STOP", "Emergency stop is engaged; no order is executed."
        elif self.tier == "T0":
            code, reason = "TIER.READ_ONLY", "Tier T0 is read-only; no order is executed."
        elif self.tier == "T1":
            code, reason = "TIER.PROPOSE_ONLY", "Tier T1 is propose-only; no order is executed."
        if code is None:
            return None
        return {
            "schema_version": 1,
            "order_id": order_id,
            "decision": "deny",
            "rules_hit": [{"code": code, "evidence": {"tier": str(self.tier)}}],
            "reason": reason,
            "ips_hash": self.ips_hash,
            "decided_at": self._now_iso(),
        }

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

        # 2. Idempotency: same order_id never executes twice
        if order_id in self._orders:
            stored_order, stored_record = self._orders[order_id]
            if order == stored_order:
                return copy.deepcopy(stored_record)
            raise OrderIdConflict(
                f"Order ID {order_id} conflict: different order body already recorded"
            )

        if order_id in self._in_flight:
            raise GatewayError(
                f"Order {order_id} reached signing but was not recorded; reconcile before retrying"
            )

        # 3. Policy evaluation (tier and emergency-stop gate first)
        raw_decision = self._tier_gate(order_id)
        if raw_decision is None:
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
        now_str = self._now_iso()
        outcome: str
        execution: dict[str, Any] | None = None

        if dec_outcome == "deny":
            # 4. On deny: outcome denied, simulator and signer never called
            outcome = "denied"
            execution = None
        elif dec_outcome == "escalate":
            # 5. On escalate: outcome pending_approval, nothing signed
            outcome = "pending_approval"
            execution = None
        elif dec_outcome == "allow":
            # 6. On allow: build tx requests, simulate, sign and send
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
        else:
            raise GatewayError(f"Unsupported decision outcome: {dec_outcome}")

        # Validate execution result if present
        if execution is not None:
            exec_validator = _get_validator("execution-result")
            exec_errors = list(exec_validator.iter_errors(execution))
            if exec_errors:
                raise GatewayError(
                    f"ExecutionResult failed schema validation: {exec_errors[0].message}"
                )

        # 7. Construct DecisionRecord and append to audit sink
        record = {
            "schema_version": 1,
            "seq": 0,
            "order_id": order_id,
            "order": copy.deepcopy(order),
            "decision": copy.deepcopy(decision),
            "no_action": None,
            "outcome": outcome,
            "execution": copy.deepcopy(execution),
            "approval": None,
            "tier": self.tier,
            "emergency_stop": self.emergency_stop,
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
