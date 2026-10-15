# Webapp v2: API and work-split contract

Status: proposed 2026-10-16 (owner reviews the UI mockups first; the API parts below do not depend on them). All additions are additive under `schema_version` 1. No change to the IPS schema, permission tiers, signer, or the gateway pipeline.

## Goals
Make the agent's work visible while it runs, let the owner keep several conversations, show the limit that matters at the moment of action, and let the owner cancel a run. Everything here reads existing data or adds owner-scoped conversation bookkeeping.

## Contracts

### C1. Job phases (read-only addition)
`GET /jobs/{job_id}` and each agent message in `GET /chat` gain `phase`:

| `phase` | Meaning |
| :--- | :--- |
| `queued` | accepted, not started |
| `rules_check` | planner done, gateway checking the rules |
| `sent` | transaction submitted, no receipt yet |
| `confirming` | receipt pending |
| `done` | terminal (see `status`) |

Derived from existing job status and audit records; no new signer path. Unknown values must be tolerated by clients.

### C2. Cancel a running job
`POST /jobs/{job_id}/cancel` (owner session, CSRF as other POSTs) → `202 {"job_id", "status": "cancelling"}`; `409 {"error": "not-cancellable"}` once the job is `sent` or later. Cancelling stops further steps of a multi-order plan; an order already submitted is never undone. Remaining steps are recorded as `skipped` with reason `owner-cancelled`. Cancel does not call the signer.

### C3. Conversations
New table `chat_threads` (migration 0005, RLS like `chat_messages`); `chat_messages` gains nullable `thread_id`; existing rows belong to an implicit default thread.

| Route | Request | Response |
| :--- | :--- | :--- |
| `GET /chat/threads` | `?limit=` (default 30) | `{"items":[{"id","title","last_message_at","status"}]}` newest first; `status` is `running`, `done`, `blocked`, `pending` from the latest job |
| `POST /chat/threads` | `{}` | `201 {"id","title":"New chat"}` |
| `POST /chat` | adds optional `thread_id`; absent = default thread | unchanged |
| `GET /chat` | adds optional `thread_id` | unchanged shape |

Title: first 40 characters of the first owner message, set by code (not by the LLM). Per-owner run rate limit (6/hour) unchanged and shared.

### C4. Limits read model
`GET /limits` (owner session) → `{"per_order_usd","per_day_usd","used_today_usd","approval_threshold_usd","protocols":["aave_v3"],"emergency_stop":"ready|stopped","as_of"}` derived from the active IPS, the rolling-24h sum and the stop flag. Read-only. Editing limits is out of scope and is an IPS decision for the owner.

### C5. Suggested prompts
`GET /chat/suggestions` → `{"items":[{"text","kind"}]}` chosen by code from state: no funds, funded, stopped, pending approval. No LLM call.

## Work split (parallel, no overlapping files)

| Ticket | Scope | Files (owner) | Depends on |
| :--- | :--- | :--- | :--- |
| B1 | C1 phases + C2 cancel | `api/jobs.py`, `api/chat_compose.py`, tests | none |
| B2 | C3 threads + migration 0005 | `core/storage/*`, `api/chat.py`, tests | none |
| B3 | C4 limits + C5 suggestions | `api/wallet.py` (new `api/limits.py`), tests | none |
| F1 | Chat UI: phase strip, Stop button, limit line on cards, quick-fix chips | `ui/views/chat.js`, `cards.js`, `ui/style.css` | B1, B3, mockups A, B |
| F2 | History rail, New chat, mobile drawer | `ui/views/side.js`, `chat.js`, `ui/api.js` | B2, mockups A, D |
| F3 | Rules page, emergency-stop confirm, skeleton loaders | `ui/views/rules.js`, `portfolio.js`, `activity.js`, `ui/views/actions.js` | B3, mockups C, E |

B1 to B3 start now. F1 to F3 start when the mockups are signed off and the matching backend ticket is merged; they may begin against fixtures in `ui/fixtures`. Each ticket ships tests and updates `docs/architecture/api.md` and `docs/architecture/ui.md`.

## Rules for every ticket
- Reuse existing validation and the gateway; no new path to the signer; no new dependency.
- Tenant isolation: every query is scoped to the owner; add a cross-tenant test.
- A migration is applied to the hosted database by the orchestrator before the PR is merged.
- UI: no `localStorage`, no external URL except BaseScan, 1280 px and 375 px without horizontal scroll, keyboard operable.
