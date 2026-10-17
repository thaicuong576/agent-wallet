# Webapp: agent wallet routes, jobs and top-ups (ticket A3)

Source of truth: `docs/design/webapp/system-design.md` sections 5, 8, 10, 11, 12 and 13. Testnet only. Custodial: the server holds each agent key (see [`custody-tenancy.md`](custody-tenancy.md)). The LLM proposes; the gateway decides.

## Modules

| Module | Role |
| :--- | :--- |
| `api/wallet.py` | `WebappServices`, `current_tenant` (the session owner's tenant), `WalletService` (live balances, gas floor, top-up verification through an injected RPC callable), the top-up service over `tenant.stores.topups`, routes `/me`, `/agent`, `/topups`, `/presets`, `/audit/verify` |
| `api/readcache.py` | `ReadCache`: in-process cache for state reads (`/portfolio`, `/agent`, `/audit/verify`) with single flight, tenant isolation, version invalidation, and bounded memory (ticket G) |
| `api/jobs/` | `JobService` (`start`, `get`, `list`, `mark_interrupted_on_startup`), `JobLimits` (the job rows live in `tenant.stores.jobs`; `SqliteJobs` is re-exported from `core/storage/sqlite/webapp.py`), routes `/agent/mint`, `/agent/withdraw`, `/runs`, `/jobs` |
| `api/chat.py`, `api/chat_compose.py` | `POST /chat`, `GET /chat`; step and message text composed by code from audit records (ticket 115) |
| `core/agent/planner/` | `FallbackPlanner`: deterministic, labelled `fallback`; reads an owner chat message by keywords |
| `core/runner/runner.py` | `Runner.run_instruction`: one owner chat message, planned and proposed order by order |
| `api/main.py` | In `AGENT_WALLET_MODE=multi`, builds the services from the environment (RPC URL, keystore secret, `DATABASE_URL` or data directory) and the `StorageBackend` |
| `core/gateway/tenant.py` | `TenantRegistry(settings, rpc, backend=...)` builds every per-owner object from `backend.open_tenant(owner)` |

## Flow

```mermaid
flowchart LR
  B[Browser] -->|cookie, CSRF header| R[routes]
  R -->|owner from session only| T[TenantRegistry.open]
  R --> J[JobService.start]
  J -->|one active job, hourly count| DB[(JobStore: webapp.sqlite or jobs table)]
  J --> W[worker pool, 4 threads]
  W -->|gas floor, stop checks| G{gate}
  G -->|run| RN[Runner: Reader, Planner or Fallback]
  G -->|mint, withdraw| OW[propose origin=owner]
  RN --> GW[Gateway]
  OW --> GW
  GW --> AU[(audit chain)]
  W -->|ids only| DB
  B -->|poll| R
```

## Entity-relationship diagram (SQLite backend, per owner directory)

Auth rows are global; the others live in `DATA_DIR/owners/<0x address>/`. The Postgres layout of design section 11 is the same entities with an `owner` column (ticket P2); since ticket W1 the app runs on either, chosen by `DATABASE_URL` (see [`storage.md`](storage.md)).

```mermaid
erDiagram
  OWNERS ||--o{ SESSIONS : has
  OWNERS ||--o{ STEPUP_CHALLENGES : requests
  OWNERS ||--|| KEYSTORES : "encrypted key blob (keystore.json)"
  OWNERS ||--o{ AUDIT_RECORDS : "one hash chain (audit.sqlite)"
  OWNERS ||--o{ ORDER_INTENTS : "closed by a record (audit.sqlite)"
  OWNERS ||--|| TIER_STATE : "state.sqlite"
  OWNERS ||--o{ APPROVAL_QUEUE : "state.sqlite"
  OWNERS ||--o{ JOBS : "webapp.sqlite"
  OWNERS ||--o{ TOPUPS : "webapp.sqlite"
  JOBS }o--o{ AUDIT_RECORDS : "ids only: order_ids, seqs"
  JOBS {
    string job_id PK "uuid4 hex"
    string kind "run, mint, withdraw"
    string status "queued, running, succeeded, denied, failed, interrupted"
    string planner_client "openai, scripted, fallback, none"
    string order_ids "json array of ids only"
    string seqs "json array of audit seq"
    string error_code
    string created_at
    string started_at
    string finished_at
  }
  TOPUPS {
    string tx_hash PK
    string amount_wei
    string status "pending, confirmed, failed"
    int block_number
    string created_at
  }
```

The jobs table never holds an order body, a note or a key. A partial unique index (`status IN ('queued','running')`) makes "one active job per owner" atomic.

## Job statuses and error codes (design 10)

| Situation | Status | `error_code` |
| :--- | :--- | :--- |
| Run, mint or withdraw finished; a policy denial inside a run | `succeeded` | none |
| Mint or withdraw order denied by the gateway | `denied` | first rule code |
| Owner stop or global stop (run pre-check, or a record with a stop code) | `denied` | `TIER.EMERGENCY_STOP`, `TIER.GLOBAL_STOP` |
| Agent ETH below the gas floor (2 x base fee x 500,000 gas) | `failed` | `GAS_BELOW_FLOOR` |
| RPC unreadable for the gas check | `failed` | `RPC_UNAVAILABLE` |
| Gateway recorded `failed` | `failed` | `SIMULATION_FAILED`, `TX_REVERTED`, `BALANCE_DELTA_UNEXPECTED`; `SIGNER_UNAVAILABLE` is reported as `RPC_UNAVAILABLE` |
| Gateway lock timeout / unfinished intent | `failed` | `GATEWAY_BUSY` / `RECONCILE_REQUIRED` |
| Process restart | `interrupted` | none |
| Another job is active / hourly limit used | no job (`409 job-in-progress` / `429 rate-limited`) | n/a |

Limits (one place, `JobLimits`): 6 runs or action chats (shared count `chat_action`), 60 chat inquiries per owner per hour (`chat_inquiry`, refunded if zero gateway proposals), 4 mint calls per owner per hour (`AGENT_WALLET_MINT_CALLS_PER_HOUR`), note at most 2,000 characters.

## Planners

`auto` uses `OpenAIClient` when `OPENAI_API_KEY` is set and falls back to `FallbackPlanner` on `LLMError` (nothing is proposed before the LLM answers, so the retry cannot duplicate an order). Without a key the fallback is used directly. `scripted_hostile` is a scripted client that emits a schema-valid supply far above the per-transaction limit, to show the gateway denying it. Records of the audit chain have a closed schema and cannot carry `planner_client`; the label is on the job and on every record summary returned by `GET /jobs/{job_id}`.

## Chat (ticket 115, chat-v3)

```mermaid
flowchart LR
  B[Chat tab] -->|POST /chat| R[api/chat.py]
  R --> J[JobService.start kind=chat]
  J -->|user message, job id| C[(ChatStore: chat_messages)]
  J --> W[worker]
  W --> P[Planner or Fallback<br/>owner_instruction channel]
  P -->|orders, reply| RN[Runner.run_instruction]
  RN -->|one by one, origin=planner| GW[Gateway]
  GW --> AU[(audit chain)]
  W -->|one voice text composed by code| C
  B -->|GET /chat| R
  R -->|steps rebuilt by job id| AU
```

- **Storage.** `chat_messages(owner, id, role, text, job_id, created_at, thread_id)` and `chat_threads(owner, id, title, created_at)` (threads: ticket 123, migration `0005_chat_threads.sql`) (SQLite: `webapp.sqlite`; Postgres: migration `0004_chat.sql`, row level security, no grant to API roles). Ids and text only: the steps are rebuilt from the audit records of `job_id` (the job row's `seqs`), so no order body, key or model transcript is stored.
- **Planner channel.** The message is passed as `owner_instruction` (`{channel: "owner_chat", trust: "untrusted_text", text}`), separate from Reader `market_facts`. It is untrusted for rule purposes: every order goes through the policy engine with `origin="planner"`, the planner schema rejects `mint`, `transfer` and the `owner` recipient, and the chat job has no path to the owner functions.
- **Contract.** The planner may return `orders` (an ordered list; the single `order` form still works) and a short `reply` (plain text, at most 600 characters). At most four orders are accepted; a plan with more is refused whole. The `reply` is shown directly with one voice (lead-in line before code facts for actions, whole text for questions) and is never proof of an outcome.
- **Execution.** `Runner.run_instruction` proposes the orders one at a time and stops at the first that is not `executed` (`denied`, `failed` or `pending_approval`). Each order left over is written to the audit log as a `no_action` record `Skipped <function> (<amount> <token>): <why>`, which the step builder shows as a `skipped` step. A question-only message records one `no_action` (`Chat request: the planner proposed no order.`); the model's words are never written to the audit log.
- **Fallback.** With no `OPENAI_API_KEY`, or on `LLMError`, the labelled `FallbackPlanner` answers and the message says so. It reads the text by keywords: a deposit request (put, supply, deposit, invest, lend, earn, stake, work; amount from "N USDC", capped by idle USDC, default the per-transaction limit) becomes `approve` then `supply`; a question gets the wallet and Aave balance as a reply; withdraw, send, transfer and mint are declined in words (swap is supported by the registry).
- **Limits.** `chat_action` and run jobs share 6 per owner per hour (`JobStore.create(..., count_kinds=("run", "chat_action"))`); `chat_inquiry` has its own limit 60 per hour; chat jobs ending with zero gateway proposals are recorded as `chat_inquiry` and refund the action bucket; the one-active-job rule applies to all.
- **Stops and gas.** An owner or global stop ends the chat job `denied` before the planner is called, with a plain message and one `no_action` record; below the gas floor the job fails with `GAS_BELOW_FLOOR` and a plain message.

## Top-ups (design 8.1)

`POST /topups` checks through the injected RPC callable that the transaction is from the session owner, to the agent address, above zero, on chain 84532, then stores it `pending`. `GET /topups/{tx_hash}` reads the receipt (`0x1` confirmed, `0x0` failed) and settles the row once. A hash another owner registered is unknown to this owner (`404`). The server never sees the owner's key.

## Owner path and ticket C3

Mint and withdraw call `Gateway.propose(order, origin="owner")` through `propose_with_origin`, which passes the keyword only when the gateway accepts it. Until C3 lands the call is a plain `propose` and the engine denies the owner function (`ACCESS.OWNER_ONLY_FUNCTION`): fail closed. The operator global stop is an injected callable: the backend setting (`settings` row on Postgres), `AGENT_WALLET_GLOBAL_STOP=1` or `DATA_DIR/GLOBAL_STOP`; any one of them stops, and an unreadable flag counts as set.

## Owner controls per session (ticket T1b)

A new tenant starts at T0, where a mint ends `denied` with `TIER.READ_ONLY`, so the owner must be able to raise the tier from the page. In multi mode `GET`/`PUT /tier`, `POST /emergency-stop`, `POST /emergency-stop/release`, `GET /decisions`, `/portfolio`, `/ips` and `POST /orders/{id}/approve|reject` read the owner from the session and act on `tenant.stores.tier_read`, `tenant.stores.tier_write` and `tenant.gateway.audit_sink`. Step-up follows design 6.2: only widening actions need a fresh signature (`tier_raise` bound to the new tier, `release_stop`, `approve` bound to the order id); stop, lowering the tier and reject need the session only. The route table, the proofs and the notes on approval queueing are in [`api.md`](api.md#owner-routes-in-multi-mode). The portfolio read is `WalletService.portfolio_state(tenant)` in `api/wallet.py`.

## Wiring in `api/main.py` (`AGENT_WALLET_MODE=multi`)

`build_webapp` reads the environment only and opens no connection (on Postgres the one exception is the schema check, which is the point: see [`storage.md`](storage.md)). Risk inputs for the policy engine are the repository's reference file stamped fresh (`reference_risk_inputs`, the same values `scripts/evidence/live_first_tx.py` uses; Base Sepolia has no indexer for them), because without them every `supply` is denied as `RISK.DATA_STALE`. The keystore secret is optional at start: reads work and wallet creation fails closed with `503 custody-unavailable`.

## Server-side read cache (`api/readcache.py`, ticket G)

To avoid redundant sequential JSON-RPC calls to Base Sepolia and repeated hash chain verifications on tab switches or dual panel queries:
- **Keys and isolation:** In-process entries are keyed by `(tenant.owner, name)` to guarantee strict tenant isolation.
- **TTL and versioning:**
  - `portfolio`: 10 s TTL; version = record count in `tenant.gateway.audit_sink` + tier emergency stop state.
  - `agent`: 10 s TTL; same audit sink record count + stop state versioning.
  - `audit_verify`: 600 s TTL; version = record count in `tenant.gateway.audit_sink`.
- **Single flight:** Concurrent requests for the same key await the active compute rather than issuing duplicate upstream RPC calls or recomputing hash chains. Exceptions fail fast and are never cached.
- **Invalidation:** Successful fund-altering paths (`register_topup`) call `invalidate(tenant.owner)`. New audit records increment the version token and force fresh reads.
- **Bounded memory & safety:** Hard cap of 512 entries with oldest eviction; all cached values are returned as deep copies.

## Proving checks

| Claim | Proving check |
| :--- | :--- |
| Jobs store ids only | `pytest tests/webapp/test_jobs.py::test_job_row_has_no_order_body -q` |
| One active job; rate limits | `test_jobs.py::test_second_job_409`, `test_runs_per_hour`, `test_mint_calls_per_hour_counts_every_outcome` |
| Fallback is labelled | `test_jobs.py::test_llm_unavailable_uses_fallback_labelled` |
| Run under stop ends `denied` | `test_jobs.py::test_run_under_stop_ends_denied_not_failed` |
| Hostile note: denied record, nothing signed | `test_jobs.py::test_hostile_note_yields_denied_record_and_nothing_signed`, `test_routes.py::test_hostile_note_over_http_is_denied_in_plain_words_and_nothing_is_signed` |
| Top-up checks | `tests/webapp/test_wallet.py` (`wrong_sender`, `wrong_recipient`, `zero_value`, `unknown_hash`, `pending_then_confirmed`) |
| Gas floor before mint, run and withdraw | `test_jobs.py::test_gas_below_floor_fails_before_anything_is_proposed` |
| Cross-owner isolation | `tests/webapp/test_routes.py::test_session_a_cannot_read_owner_b_state` and the other isolation tests |
| Read cache: TTL, single flight, copies, isolation, version recompute | `tests/webapp/test_readcache.py`, `tests/webapp/test_wallet.py` (`portfolio_state_cached`, `agent_state_cached`, `audit_verify_cached`) |
| `origin="owner"` only in `api/jobs/` | `test_jobs.py::test_origin_owner_literal_only_in_jobs_module` |
| Keystore, chain and jobs survive a restart on both backends | `tests/webapp/test_storage_backends.py::test_restart_keeps_keystore_and_chain`, `test_running_job_is_interrupted_after_restart` |
| Sign-in creates the tenant and a keystore row | `test_storage_backends.py::test_sign_in_creates_tenant_and_keystore` |
| A second owner cannot read the first owner's rows | `test_storage_backends.py::test_second_owner_cannot_read_first_owners_rows` |
| Works through a transaction-mode pooler (no session state) | `test_storage_backends.py::test_every_statement_works_on_a_fresh_connection` |
| `migrate()` is not called without `DATABASE_MIGRATE_URL`; a schema that is behind fails closed | `test_storage_backends.py::test_app_role_without_migrate_url_only_checks_the_schema`, `test_schema_behind_the_code_fails_closed`, `test_postgres_behind_schema_refuses_to_start` |
| Owner controls act on the session's tenant; raise, release and approve need a bound step-up | `tests/webapp/test_owner_routes.py` |
