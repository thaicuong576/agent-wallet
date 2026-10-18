"""Job chat history preparation."""

from __future__ import annotations

from api.chat_compose import outcome_sentence_from_records
from core.agent.planner import (
    HISTORY_MAX_CHARS,
    HISTORY_MAX_TURNS,
    clean_history_text,
)
from core.gateway.tenant import Tenant
from core.storage.interfaces import JobStore

from .outcome import _limit_denied, _records

NEUTRAL_LIMIT_REFUSAL_SENTENCE = (
    "That earlier request was not executed. Limits are re-checked by the system"
    " for every new request."
)


def _build_chat_history(
    tenant: Tenant,
    job_id: str,
    thread_id: str | None,
    store: JobStore,
) -> list[dict[str, str]]:
    raw_messages = tenant.stores.chat.list(limit=50, thread_id=thread_id)
    # Exclude current job's messages
    earlier = [m for m in raw_messages if m.get("job_id") != job_id]
    turns: list[dict[str, str]] = []
    for m in earlier:
        role = m.get("role")
        if role == "user":
            turns.append({"role": "user", "text": clean_history_text(m.get("text") or "", 400)})
        elif role == "agent":
            agent_text = clean_history_text(m.get("text") or "", 400)
            agent_job_id = m.get("job_id")
            seqs: list[int] = []
            if agent_job_id:
                job_row = store.get(agent_job_id)
                if job_row and job_row.get("seqs"):
                    seqs = [int(s) for s in job_row["seqs"]]
            recs = _records(tenant, seqs)
            intent = str(m["intent"]) if m.get("intent") is not None else None
            if intent == "refusal" or _limit_denied(recs):
                agent_text = NEUTRAL_LIMIT_REFUSAL_SENTENCE
            else:
                outcome_text = outcome_sentence_from_records(recs)
                if (
                    outcome_text
                    and outcome_text != "No orders were proposed."
                    and outcome_text not in agent_text
                ):
                    agent_text = (
                        f"{agent_text} {outcome_text}".strip() if agent_text else outcome_text
                    )
            turn: dict[str, str] = {"role": "agent", "text": agent_text}
            if intent is not None:
                turn["intent"] = intent
            turns.append(turn)
    if len(turns) > HISTORY_MAX_TURNS:
        turns = turns[-HISTORY_MAX_TURNS:]
    while turns and sum(len(t["text"]) for t in turns) > HISTORY_MAX_CHARS:
        turns.pop(0)
    return turns
