# Architecture: REST API backend

Owner: `api` (`api/app.py`, `api/routes/`, `api/main.py`). The HTTP interface implementing the single API contract between the minimal UI, the eval harness, and the backend as specified in `contracts/openapi.yaml`.

## Component architecture

```mermaid
flowchart TD
  subgraph Clients ["Callers"]
    AG[Agent / Planner / UI]
    OW[Wallet Owner]
    EV[Eval Harness]
  end

  subgraph API ["api (FastAPI)"]
    AUTH{Owner Auth<br/>hmac compare}
    EP_AGENT["Agent Endpoints<br/>POST /orders<br/>GET /portfolio, /ips, /tier, /health"]
    EP_AUDIT["Audit Endpoints<br/>GET /decisions<br/>GET /eval/report"]
    EP_OWNER["Owner Endpoints<br/>PUT /tier<br/>POST /emergency-stop*<br/>POST /orders/{id}/approve|reject"]
  end

  subgraph Core ["core Subsystems"]
    GW[core.gateway.Gateway]
    TV[core.tiers.TierView]
    OC[core.tiers.OwnerControls]
    AUDIT[(core.gateway.audit.AuditSink)]
  end

  AG -->|propose order| EP_AGENT
  EV -->|query state & audit| EP_AUDIT
  OW -->|Bearer token| AUTH
  AUTH -->|if valid| EP_OWNER
  AUTH -. 401 or 503 .-> OW

  EP_AGENT -->|propose| GW
  EP_OWNER -->|mutate tier & approvals| OC
  EP_OWNER -->|append records| AUDIT
  EP_AUDIT -->|read hash chain| AUDIT
  EP_AGENT -->|read tier state| TV
```

## Module boundaries and responsibilities

| Endpoint | Method | Role | Caller | Target subsystem | Response contract |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `/orders` | `POST` | Propose order | Agent / UI | `core.gateway.Gateway` | `decision-record.schema.json` |
| `/orders/{id}/approve` | `POST` | Approve pending order | Owner | `OwnerControls` & `AuditSink` | `decision-record.schema.json` |
| `/orders/{id}/reject` | `POST` | Reject pending order | Owner | `OwnerControls` & `AuditSink` | `decision-record.schema.json` |
| `/emergency-stop` | `POST` | Halt all orders | Owner | `OwnerControls` & `AuditSink` | `decision-record.schema.json` |
| `/emergency-stop/release` | `POST` | Resume orders | Owner | `OwnerControls` & `AuditSink` | `decision-record.schema.json` |
| `/tier` | `GET` | Current tier & stop flag | Any | `core.tiers.TierView` | `tier-state.schema.json` |
| `/tier` | `PUT` | Update tier (T0/T1/T2) | Owner | `core.tiers.OwnerControls` | `tier-state.schema.json` |
| `/portfolio` | `GET` | Snapshot of positions | Any | Portfolio provider | `portfolio-state.schema.json` |
| `/ips` | `GET` | Active IPS and SHA-256 | Any | `core.policy.ips` | `ips.schema.json` + hash |
| `/decisions` | `GET` | Paginated audit chain | Any | `core.gateway.audit.AuditSink` | Page of DecisionRecords |
| `/eval/report` | `GET` | Latest eval metrics | Any | Eval report provider | Eval report envelope |
| `/health` | `GET` | Liveness check | Any | None | `{"status": "ok"}` |

## Enforced rules and invariants

1. **Fail-closed owner authentication** (single mode; in multi mode see [Owner routes in multi mode](#owner-routes-in-multi-mode)): All owner endpoints (`/emergency-stop*`, `/orders/{id}/approve|reject`, `PUT /tier`) require an owner token via HTTP `Authorization: Bearer <token>` or `X-Owner-Token`. The token is compared against `AGENT_WALLET_OWNER_TOKEN` in constant time (`hmac.compare_digest`). If `AGENT_WALLET_OWNER_TOKEN` is unset in the environment, all owner endpoints immediately return HTTP 503 (`urn:agent-wallet:problem:service-unavailable`).
2. **Agent isolation**: Agent-facing callers cannot reach owner mutators. Requests without the owner token are rejected with HTTP 401 or 503. Agent orders (`POST /orders`) pass solely through `core.gateway.Gateway`.
3. **No signer calls**: As verified by AST boundary tests, `api` does not import `core.signer` and never invokes signing operations directly.
4. **RFC 7807 problem details**: All client and server error responses return `application/problem+json` matching the OpenAPI `components/schemas/Problem` schema with fields `type`, `title`, `status`, and optional `detail` and `errors`.
5. **OpenAPI contract equivalence**: The set of contract endpoints exposed on the FastAPI application strictly equals the path set declared in `contracts/openapi.yaml`.
6. **Deny-all default app**: Invoking `create_app()` without parameters creates an in-memory deny-all `T0` app so `uvicorn api.main:app` starts safely out-of-the-box.

## Proving checks

| Claim | Proving check |
| :--- | :--- |
| OpenAPI paths equal app routes | `pytest tests/api/test_routes.py::test_routes_equal_openapi_paths -q` |
| Default app starts and denies orders under T0 | `pytest tests/api/test_routes.py::test_default_app_instantiation_without_arguments -q` |
| Owner endpoints reject missing or bad token | `pytest tests/api/test_owner.py::test_owner_endpoints_reject_missing_token -q`, `test_owner_endpoints_reject_wrong_token` |
| Owner endpoints fail closed on unset env token | `pytest tests/api/test_owner.py::test_owner_endpoints_fail_closed_when_token_unset -q` |
| Agent cannot reach approve or stop | `pytest tests/api/test_owner.py::test_agent_facing_cannot_reach_approve_or_stop -q` |
| Responses validate against contract schemas | `pytest tests/api/test_state_and_audit.py -q` |
| Independent check passes | `pytest tests/api -q` (31 passed) |

## Webapp routes (ticket A3)

The wallet-session routes of design 12.2 are mounted by `create_app` on every start; the existing routes above are unchanged and the single-owner token mode keeps working. Without `AGENT_WALLET_MODE=multi` no webapp services are attached and every session route answers `503 service-unavailable` (fail closed); `/presets` and `/health` stay public. Detail, the ERD and the job table are in [`webapp.md`](webapp.md); sign-in and step-up are in [`auth.md`](auth.md).

| Route | Method | Auth | Module |
| :--- | :--- | :--- | :--- |
| `/auth/nonce`, `/auth/verify` | `POST` | none | `api/auth.py` |
| `/auth/logout` | `POST` | session | `api/auth.py` |
| `/auth/step-up` | `POST` | session | `api/auth.py` |
| `/me`, `/agent` | `GET` | session | `api/wallet.py` |
| `/topups` | `POST` | session | `api/wallet.py` |
| `/topups/{tx_hash}` | `GET` | session | `api/wallet.py` |
| `/presets` | `GET` | none | `api/wallet.py` (one placeholder preset; ticket A4 replaces it) |
| `/audit/verify` | `GET` | session | `api/wallet.py` (`verify_chain` on the owner's chain) |
| `/agent/mint`, `/runs` | `POST` | session | `api/jobs/` |
| `/agent/withdraw` | `POST` | session plus step-up `withdraw` | `api/jobs/` |
| `/jobs`, `/jobs/{job_id}` | `GET` | session | `api/jobs/` |
| `/jobs/{job_id}/cancel` | `POST` | session (CSRF header as every write) | `api/jobs/` (ticket 122) |
| `/chat` | `POST` | session (CSRF header as every write) | `api/chat.py` (starts a job of kind `chat` in `api/jobs/`) |
| `/chat?limit=50&thread_id=` | `GET` | session | `api/chat.py` |
| `/chat/stream/{job_id}` | `GET` | session | `api/sse.py` (Server-Sent Events: `phase`, `done`, 15 s comment ping) |
| `/chat/threads?limit=30` | `GET` | session | `api/chat.py` |
| `/chat/threads` | `POST` | session (CSRF header as every write) | `api/chat.py` |
| `/limits` | `GET` | session | `api/limits.py` (read model: IPS limits, rolling-24h spend, stop flag; contract C4) |
| `/protocols` | `GET` | session | `api/limits.py` (registry entries the owner's IPS enables; see `protocols.md`) |
| `/chat/suggestions` | `GET` | session | `api/limits.py` (prompts chosen by code from state, no LLM call; contract C5) |

Rules enforced here: the owner address comes only from `require_session` (no route has an owner parameter or body field; extra body fields are `422`); a foreign `job_id` or `tx_hash` is `404`; problems use the 12.1 slugs (`job-in-progress` 409 with the active `job_id`, `rate-limited` 429 with `Retry-After`, `custody-unavailable` 503); `origin="owner"` appears only in `api/jobs/`.

| Claim | Proving check |
| :--- | :--- |
### Limits and suggestions (ticket 124)

`GET /protocols` answers `{items: [{id, name, category, tokens, functions, paper}]}`: the protocol registry entries (`docs/architecture/protocols/protocols.md`) that are in the owner's IPS `universe.protocols`. Read-only, owner-scoped, no addresses; owner-only functions are not listed.

`GET /limits` answers `{per_order_usd, per_day_usd, used_today_usd, approval_threshold_usd, protocols, emergency_stop, as_of}`. Limits and `protocols` come from the owner's active IPS (`limits.per_tx_usd`, `limits.per_day_usd`, `approval.escalate_above_usd`, `universe.protocols` as written in the IPS); `used_today_usd` is the rolling 24 hour executed spend from the owner's ledger (approve, mint and transfer excluded); `emergency_stop` is `ready` or `stopped`. Money is a string with two decimals. Read-only: there is no write path and no limit editing.

`GET /chat/suggestions` answers `{items: [{text, kind}]}` (`kind` is `question` or `action`). Code picks the set by state, in this order: stopped, pending approval, no funds (USDC and aUSDC both zero), funded. If the chain RPC cannot be read the neutral set is returned. No LLM call. Both routes are scoped to the session owner; proof is `pytest tests/webapp/test_limits.py -q`.

### Chat (ticket 115)

`POST /chat` takes `{message}` (1 to 500 characters, no other field) and answers `202 {job_id, status}`. It starts a job of kind `chat` under the same rules as `POST /runs`: one active job per owner (`409 job-in-progress` with the active `job_id`) and quota split into `chat_action` (6 per owner per hour, shared with runs) and `chat_inquiry` (60 per hour; chat jobs ending with zero gateway proposals are recorded as inquiries and refund the action bucket, `429 rate-limited` with `Retry-After`). `GET /chat` returns `{items: [{id, role, text, job_id, created_at, steps}]}` oldest first (`limit` 1 to 200, default 50). A step is `{order_id, function, token, amount, outcome, reason, rule_codes, tx_hash, explanation}` rebuilt from the audit records of the message's job by `api/chat_compose.py`; `outcome` is `executed`, `denied`, `pending_approval`, `failed` or `skipped`. The agent `text` is composed with one voice: when no orders were recorded the model's reply is the whole text (no `Agent says` label and no `No transaction was made.` prefix); when orders were recorded, the model reply is a lead-in line before the code facts. The chat route proposes with `origin="planner"` through the same gateway, so mint, transfer, withdraw and tier changes are not reachable from it. Detail is in [`webapp.md`](webapp.md#chat-ticket-115-chat-v3).

### Conversation threads (ticket 123, contract C3)

`GET /chat/threads?limit=30` (1 to 100) returns `{items: [{id, title, last_message_at, status}]}`, newest first. `status` is `running`, `pending`, `done` or `blocked` and is read from the latest job of the thread. `POST /chat/threads` takes `{}` and answers `201 {id, title: "New chat"}` when opening a new conversation, or `200 {id, title}` reusing the owner's newest thread if it has no messages. `POST /chat` and `GET /chat` take an optional `thread_id`; absent means the implicit default thread (id `default`, the messages written before threads existed, listed only when it has any). The response shape of both is unchanged. A thread id that does not belong to the session's owner is unknown (`404 not-found`, nothing is started). The title is the first 40 characters of the thread's first owner message, set by code, never by the LLM. The hourly run limit (6) is per owner and is shared by all threads. Storage and the migration are in [`storage.md`](storage.md#conversation-threads-and-migration-0005-ticket-123).

| Claim | Proving check |
| :--- | :--- |
| Chat: multi-order success, stop at the first failure, hostile message denied with nothing signed, question-only reply, T0 text, 409, shared 429, owner isolation | `pytest tests/webapp/test_chat.py -q` |

| Claim | Proving check |
| :--- | :--- |
| App routes and methods equal `contracts/openapi.yaml` | `pytest tests/api/test_routes.py -q` (reads the app's generated schema, since included routers hide their routes from `app.routes`) |
| Session routes reject anonymous callers; withdraw needs a bound step-up | `pytest tests/webapp/test_routes.py -q` |
| A session for A cannot read or act on B | `pytest tests/webapp/test_routes.py -k "session_a or foreign or owner_query or chain_verifies" -q` |

### Job phases and cancel (ticket 122, contract C1 and C2)

`GET /jobs`, `GET /jobs/{job_id}` and each message of `GET /chat` carry `phase`: `queued` (accepted, worker not started), `rules_check` (the worker is planning and the gateway is checking the rules), `sent` (the gateway holds its signing intent for the order in flight and has not yet appended the record) and `done` (every terminal `status`; also the phase of a user message). `confirming` is part of the contract but is not emitted today: the gateway signs and waits for the receipt inside one call, so nothing outside it can see a receipt pending. Clients must tolerate unknown values. The phase is derived from the stored job `status` and the gateway's intent record (`JobService.phase`); it adds no signer path and stores nothing.

`POST /jobs/{job_id}/cancel` answers `202 {job_id, status: "cancelling"}` while the job is `queued` or `rules_check`, `404` for an unknown or foreign job, and `409 not-cancellable` (problem slug, plus `error: "not-cancellable"`) once the job is `sent` or later or already finished. The flag lives in the process that runs the job and is applied at exactly these cancellation points, all before an order reaches the gateway:

| Point | Where | Effect |
| :--- | :--- | :--- |
| 1 | worker start (`JobService._execute`), before gas gate, planner or any read | job ends without a plan; one `no_action` record; chat text "You cancelled this request before I acted." |
| 2 | before the single order of a `mint` or `withdraw` job | same as point 1 |
| 3 | before every `propose` of a run or chat plan (`_CancelGate`), so between the orders of a multi-order plan | the order and each later one are recorded as `no_action` steps `Skipped <function> (<amount>): owner-cancelled`; the gateway is not called |

An order already inside `propose` is never interrupted or undone, and cancel never calls the signer: the gate only withholds the next `propose`. A job on which the cancel took effect ends `status: "denied"`, `error_code: "OWNER_CANCELLED"` (no new status value, so clients that know the existing terminal statuses keep working); a job whose last order was already past the gateway when the cancel arrived finishes under its normal status. Cancelled steps appear in `GET /chat` as `outcome: "skipped"` with `reason` ending `owner-cancelled`.

| Claim | Proving check |
| :--- | :--- |
| Phase per status and per intent, on jobs and chat; cancel queued, between two and three orders, `409` after `sent` and when finished, `404`, CSRF, cross-tenant, signer never named in `api/jobs/` | `pytest tests/webapp/test_job_phases.py -q` |

### Chat progress stream (ticket C-C)

`GET /chat/stream/{job_id}` returns a `text/event-stream` response via Starlette `StreamingResponse` in `api/sse.py` (`Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`). It sends:
- `event: phase`: emitted on each job phase change (`queued`, `rules_check`, `sent`, `confirming`) with `data: {"phase": "<phase>", "elapsed_seconds": <float>}` and an incremental `id`.
- `: ping\r\n\r\n`: emitted every 15 seconds to keep the connection alive.
- `event: done`: emitted once when the job finishes, carrying data identical to `GET /jobs/{job_id}` for that job; the stream closes immediately after `done`.

A client disconnect cleanly terminates the generator stream and never cancels the underlying job. Unknown or foreign `job_id` responds with `404 not-found`.

| Claim | Proving check |
| :--- | :--- |
| SSE formatting, route tests (404 foreign, ping, done payload equals polling payload), client disconnect without cancelling job | `pytest tests/webapp/test_sse.py -q` |

## Owner routes in multi mode

When `app.state.webapp` is set (`AGENT_WALLET_MODE=multi`) the nine routes below stop using `AGENT_WALLET_OWNER_TOKEN` and the one global gateway. They authenticate with the verified session cookie only (the shared token is never accepted, no path, query or body field names an owner), resolve the tenant of the session address and act on that tenant's tier store, approval queue, audit chain and gateway. Single mode is unchanged (token for owner actions, no credential for reads).

| Route | Method | Multi mode auth (design 6.2) | Acts on |
| :--- | :--- | :--- | :--- |
| `/tier` | `GET` | session | tenant tier state |
| `/tier` | `PUT` | session; **step-up `tier_raise`** bound to `{"tier": <new tier>}` only when the tier is raised (T0 to T1, T1 to T2, T0 to T2); lowering or keeping needs the session only | tenant tier store |
| `/emergency-stop` | `POST` | session only (one click) | tenant stop flag; appends the `TIER.EMERGENCY_STOP` record to the tenant chain |
| `/emergency-stop/release` | `POST` | session plus **step-up `release_stop`** (params `{}`) | tenant stop flag; appends `TIER.EMERGENCY_STOP_RELEASED` |
| `/decisions` | `GET` | session | tenant audit chain (filters, cursor and schema as before) |
| `/portfolio` | `GET` | session | live read through the tenant's portfolio reader (balances over RPC, limits from the ledger), `price_source: mainnet_reference`; RPC failure is `503` with no error text |
| `/ips` | `GET` | session | the tenant's active IPS and its hash |
| `/orders/{order_id}/approve` | `POST` | session plus **step-up `approve`** bound to `{"order_id": <id>}` | tenant approval queue; the stored order is proposed again so the gateway runs the whole path (tier, stops, policy, simulate, sign, audit) |
| `/orders/{order_id}/reject` | `POST` | session only | tenant approval queue; appends the rejection record |

Notes. Raising the tier is the only tier change that widens authority, so only it asks for a fresh signature; like the single-owner route, a tier change is held in the tier store and does not add an audit record (stops do). The tenant gateway has no approval callback, so an order left `pending_approval` is queued the first time its owner approves or rejects it; an order id that is not in the session owner's chain is `404`. An approved order is proposed again with planner origin, so an owner-only function stays denied (fail closed). If that proposal fails (gateway busy, reconcile required) the queue row is already approved but the chain still says `pending_approval`; the retry (with a fresh step-up) skips the queue write and proposes again, so the order is never stuck. Step-up is checked right after the session, before any state lookup, and a failed check consumes the challenge.

| Claim | Proving check |
| :--- | :--- |
| Every owner route rejects anonymous callers and the shared owner token in multi mode | `pytest tests/webapp/test_owner_routes.py -k "anonymous or shared_owner_token" -q` |
| Raising the tier needs a step-up bound to the new tier; lowering needs the session only | `test_owner_routes.py::test_raising_the_tier_needs_a_step_up_signature`, `test_step_up_is_bound_to_the_new_tier_action_and_single_use`, `test_lowering_the_tier_needs_the_session_only` |
| Stop is session only and audited; release needs a step-up | `test_owner_routes.py::test_stop_needs_the_session_only_and_is_audited`, `test_release_needs_a_step_up_signature` |
| Approve needs a step-up bound to the order id; reject is session only | `test_owner_routes.py::test_approve_needs_a_step_up_bound_to_the_order_id`, `test_reject_needs_the_session_only_and_records_a_denial`, `test_approve_that_hits_a_busy_gateway_can_be_retried` |
| A session for A cannot read or change B's tier, stop or approvals | `test_owner_routes.py::test_tier_and_stop_are_isolated_per_owner`, `test_an_order_of_owner_a_is_404_for_owner_b`, `test_decisions_are_the_session_owners_own` |
| After a raise to T2 a mint job executes with the fake signer | `test_owner_routes.py::test_mint_is_denied_at_t0_then_executes_after_the_owner_raises_to_t2` |
