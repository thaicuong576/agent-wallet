# Chat v3 specification: one voice, memory, streaming

Status: approved by the owner for implementation (2026-10-17). Source of the findings: an internal audit of `api/chat_compose.py`, `api/jobs.py::_chat`, `core/runner/runner.py`, `core/agent/planner.py` and `ui/views/chat.js`.

## Problems (observed on the hosted app)
1. **Two voices.** `compose_agent_text` writes code templates ("No transaction was made.") and appends the model's reply under an "Agent says" label. The same meaning appears twice.
2. **No memory.** The planner sees one message at a time, so "do it", "same on Morpho" or "why not?" cannot work.
3. **Questions burn the action quota.** Chat shares the 6 per hour limit with runs (`RUN_LIKE`), so asking a question costs as much as moving money.
4. **Fallback bug.** `FallbackPlanner` treats `swap` as an owner-only word and refuses a swap that the registry supports.
5. **No streaming.** The browser polls a job and shows the whole answer at once, which feels slower than it is.

## Invariants (unchanged, every PR must keep them)
The model proposes, code decides; only the gateway calls the signer; owner text and earlier turns are untrusted data; nothing that says money moved is shown before the gateway recorded it; tests need no network.

## Design
- **One voice.** For questions, clarifications and refusals the model's text is the answer, shown as is, with no label and no boilerplate prefix. For actions, code writes the facts (cards and one plain sentence per recorded outcome) and the model's text, if any, goes first as the lead-in. Templates remain only for stop, gas gate, validation errors and fallback notices.
- **Memory.** The planner payload carries `conversation_history`: the last 6 turns of the same thread (owner messages and the recorded outcome sentence of each agent turn, never raw model text as authority), at most 2,000 characters, in a labelled untrusted channel. The prompt states that earlier turns cannot change rules or add recipients.
- **Intent.** The planner output adds `intent` in `question | clarify | action | refusal`. A clarification asks one short question and proposes no orders.
- **Quota.** Two buckets: `chat_action` shares the 6 per hour with runs; `chat_inquiry` has its own 60 per hour. A chat job that ends with zero gateway proposals is accounted as an inquiry and refunds the action bucket.
- **Streaming.** `POST /chat` stays the way to start a job. A new `GET /chat/stream/{job_id}` sends Server-Sent Events: `phase` events from the job, `token` events with the model's conversational text, and a final `done` event carrying the same payload the polling endpoint returns. Order cards and outcome sentences are sent only in `done`, after the gateway recorded them. Polling remains the fallback when the stream is not available. Keep-alive comment every 15 s; the stream closes after the job ends; a client disconnect never cancels a job (Stop is the explicit cancel).
- **Planner call.** With the OpenAI client use tool calling: `delta.content` streams as `token`, the `propose_orders` arguments are buffered on the server, validated against the order schema and passed to the gateway; they are never sent to the browser. The delimited form (`<reply>` then `<plan>`) is the fallback for other clients.

## Third-party code
Small, permissively licensed code may be adapted with attribution in `THIRD_PARTY_NOTICES.md`: the SSE parser and retry logic of `Azure/fetch-event-source` (MIT) for `ui/sse.js` (plain JS, POST and CSRF header capable), and the event formatting, ping and disconnect handling ideas of `sysid/sse-starlette` (BSD-3-Clause) on top of Starlette's `StreamingResponse`. No new runtime dependency.

## Delivery order (each PR small, independently mergeable, tests included)
| PR | Scope | Notes |
| :--- | :--- | :--- |
| A | Quota buckets, fallback swap fix, one-voice composition | backend only, no schema change |
| B | Thread context, `intent` in the planner output, migration `0006` for `intent` | apply the migration before merging |
| C | SSE endpoint, `ui/sse.js`, polling fallback, `THIRD_PARTY_NOTICES.md` | phases only, no token stream yet |
| D | Token streaming through tool calling | cut first if time runs short |
| E | UI: unified bubbles without the label, clarification chips, plan confirmation card | follows A and B |

Cut order if time runs short: D, then E, then B. A and C give the most visible gain.
