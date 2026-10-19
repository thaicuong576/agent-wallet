"""Job outcomes, status classification, and timestamp formatting."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.gateway.tenant import Tenant

from .constants import ERROR_CODE_MAP, STOP_CODES


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
    intent: str | None = None


def _records(tenant: Tenant, seqs: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seq in seqs:
        try:
            out.append(tenant.gateway.audit_sink[seq])
        except Exception:  # noqa: BLE001 - a missing record must not break history
            continue
    return out


def _limit_denied(records: Sequence[Mapping[str, Any]]) -> bool:
    for rec in records:
        if rec.get("outcome") not in (None, "denied"):
            continue
        decision = rec.get("decision")
        if not isinstance(decision, Mapping):
            continue
        if decision.get("decision") in ("allow", "escalate"):
            continue
        for hit in decision.get("rules_hit", []):
            code = str(hit.get("code") or "") if isinstance(hit, Mapping) else ""
            if code.startswith("LIMIT."):
                return True
    return False


def _rule_codes(record: Mapping[str, Any]) -> list[str]:
    block = record.get("decision") or record.get("no_action") or {}
    return [str(h.get("code")) for h in block.get("rules_hit", [])]


def classify(records: list[dict[str, Any]], kind: str) -> tuple[str, str | None]:
    """Map the gateway's records to a job status and error code."""
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


def _iso(stored: str) -> str:
    return _parse(stored).strftime("%Y-%m-%dT%H:%M:%SZ")
