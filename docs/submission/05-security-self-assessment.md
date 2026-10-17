# Security self-assessment

Scope: the agent-wallet system as built in this repository, testnet only (Base Sepolia, chain 84532). This report covers the permission-tier implementation and the known-risk list that the competition asks for. It states what is enforced and which test proves it, and it lists what is not done yet. Status values are `enforced` (code and test exist), `partial`, `closed`, and `accepted`.

Last updated against `main` following the multi-owner webapp architecture, protocol registry expansions (Aave Stata, Morpho Blue, Uniswap v3), split chat quota buckets, Server-Sent Events streaming, concurrent portfolio RPC reads, and UI v3. Webapp controls are in section 3b and risks K1 to K25 in section 4.

## 1. Principles
1. **The LLM proposes, code decides.** The model never holds a key, never sees credentials, and cannot call the signer.
2. **One path to the signer:** only `core.gateway` imports `core.signer`. Everything else is data in, data out.
3. **Default deny.** A missing rule input (risk data, price, tier) denies; it is never read as zero or fallback values.
4. **Testnet only.** The signer refuses any chain id other than 84532.
5. **Every decision is audited.** Every proposal, whether executed, denied, escalated, or skipped, is written to an append-only, tamper-evident SHA-256 hash chain with its rule codes and evidence.

## 2. Permission tiers and authorization

### Tier definitions and execution semantics

| Tier | Meaning | What the gateway does | Enforced by |
| :--- | :--- | :--- | :--- |
| **T0** | Read only | Denies every proposed order with `TIER.READ_ONLY` before policy evaluation, simulation, or signing. Read-only portfolio and yield queries remain functional. | `tests/gateway/test_gateway.py`, `tests/e2e/test_gateway_e2e.py` |
| **T1** | Propose only | Evaluates orders and records decisions with `TIER.PROPOSE_ONLY`, but halts before simulation and signing. Intended for observation and paper trading. | `tests/gateway/test_gateway.py`, `tests/e2e/test_gateway_e2e.py` |
| **T2** | Execute inside IPS | Evaluates orders against the Investment Policy Statement (IPS). Allowed orders undergo bundle simulation (`eth_simulateV1`), signing on chain 84532, and audit recording. | `tests/policy`, `tests/gateway/test_gateway.py`, `tests/e2e` |
| **T3 (Rule)** | Owner approval required | When an order's USD value exceeds `approval.escalate_above_usd`, the engine queues it as `pending_approval` (`TIER.APPROVAL_REQUIRED`). The agent cannot execute it autonomously; execution requires an explicit owner approval signature. Policy is re-evaluated upon approval so approval cannot bypass a `deny`. | `tests/e2e/test_gateway_e2e.py` (escalate scenarios), `tests/webapp/test_owner_routes.py` |
| **Emergency stop** | Global & per-owner kill switch | Instantly halts all order processing with `TIER.EMERGENCY_STOP` before anything else, overriding existing approvals and active plans. An unreadable tier state also fails closed. | `tests/gateway/test_gateway.py`, `tests/gateway/test_global_stop.py`, `tests/e2e/test_runner_stop.py` |

### How authority and tier changes are authorized

Authority changes are guarded by strict cryptographic and architectural mechanisms depending on the deployment mode:

1. **Local / CLI mode:**
   - Read-only queries use `TierView` (enforced by AST tests to have no mutating methods).
   - State mutations require `OwnerControls`, protected by the environment secret `AGENT_WALLET_OWNER_TOKEN` with constant-time string comparison (`hmac.compare_digest`), failing closed with HTTP 503 if unset.
2. **Webapp / Multi-tenant mode:**
   - Authentication begins with Sign-In with Ethereum (SIWE): a server-generated nonce bound to origin and chain 84532, setting a secure, HttpOnly, SameSite session cookie.
   - **Step-up signatures for widening actions:** Any action that expands the agent's authority—raising tier (e.g., T0 to T2), releasing an emergency stop, requesting an asset withdrawal, approving a pending order, or changing the IPS preset—requires a fresh EIP-191 / SIWE cryptographic signature from the owner's external wallet.
   - **Preset binding:** When switching or activating an Investment Policy Statement preset (e.g., moving from `conservative` to `diversified`), the step-up signature must explicitly bind both the tier and the exact `preset_id` and preset configuration hash. A signature for the tier alone or for a different preset is rejected by the server (`tests/webapp/test_owner_routes.py`).
   - **Single-use & replay protection:** Every step-up challenge contains a random single-use nonce with a strict 5-minute expiration timestamp (`tests/webapp/test_stepup.py`).

| Action | Who | Mechanism |
| :--- | :--- | :--- |
| Read tier, stop, approval status | Gateway, agent, API | `TierView` (read-only; verified by AST tests to possess no mutating public methods) |
| Set tier, engage/release stop, approve, reject | Owner only | `OwnerControls` locally, or EIP-191 step-up cryptographic signature in webapp mode |
| Select or widen IPS preset | Owner only | `PUT /tier` with step-up signature strictly bound to `preset_id` and configuration hash |
| HTTP owner endpoints | Owner only | SIWE verified session cookie + CSRF header + step-up signature on authority changes |

## 3. Core controls and their evidence

| # | Control | Status | Evidence |
| :--- | :--- | :--- | :--- |
| **C1** | Only `core.gateway` imports `core.signer` | enforced | `tests/test_signer_boundary.py`, CI static boundary check |
| **C2** | Signer signs only chain 84532 and refuses a missing or mismatched chain id | enforced | `tests/signer/test_signer.py` |
| **C3** | Key read from `AGENT_WALLET_PRIVATE_KEY` at construction; never in `repr`, `str`, logs or records; 64-hex strings redacted in error details | enforced | `tests/signer/test_signer.py`, `tests/gateway/test_gateway.py` |
| **C4** | Simulate before sign from the signer's own address; failed or unsupported simulation never signs; dependent calls simulated as one atomic bundle (`eth_simulateV1`) | enforced | `tests/gateway/test_gateway.py`, `tests/e2e/test_gateway_e2e.py` |
| **C5** | Allowances are exact-amount only (`ACCESS.UNLIMITED_APPROVAL` denied) | enforced | `tests/policy/test_access_rules.py`, eval scenario `deny-access-unlimited-approval` |
| **C6** | Universe, per-transaction, per-day and per-hour limits, allocation and risk rules across 28 catalog rule codes (31 total in registry) | enforced | `tests/policy/test_limit_rules.py`, `tests/policy/test_alloc_rules.py`, `tests/policy/test_risk_rules.py`, eval report 27/28 covered |
| **C7** | Policy engine is pure: zero I/O, zero float arithmetic (exact `Decimal`/integers), zero state mutation | enforced | `tests/policy/test_engine_purity.py` |
| **C8** | Audit log is an append-only SHA-256 hash chain; tampering, deletion and reordering are detected and first bad `seq` is reported | enforced | `tests/audit/test_audit_log.py`, eval integrity scenarios, `docs/evidence/audit-chain.jsonl` verified by `python -m core.audit.verify` |
| **C9** | Concurrent writers cannot corrupt or fork the audit chain (`BEGIN IMMEDIATE`, multi-process stress tests) | enforced | `tests/audit/test_sink_concurrency.py` |
| **C10** | Prompt-injection defence: Reader returns structured facts only; Planner output must validate against order schema; out-of-universe actions fail closed in policy engine | enforced | `tests/agent/test_boundaries.py`, `tests/agent/test_injection_corpus.py`, 12 injection eval scenarios |
| **C11** | Secrets hygiene: `.env` ignored in git, environment variable names only in `.env.example`, automated secret scanner in CI | enforced | `scripts/checks/check_no_secrets.py`, CI workflow |
| **C12** | Owner token lives in page memory only; API strings are rendered as text, never HTML (XSS prevention) | enforced | `tests/ui/test_ui.py` |
| **C13** | Emergency stop and tier state are shared by API and runner through persistent storage | enforced | `tests/tiers/test_shared_file.py`, `tests/e2e/test_runner_stop.py` |
| **C14** | Concurrent proposes are serialised by a cross-process lock held from limit read to audit append; timeout denies with nothing signed | enforced | `tests/gateway/test_lock.py` |

## 3b. Webapp controls (multi-owner mode)

| # | Control | Status | Evidence |
| :--- | :--- | :--- | :--- |
| **W1** | Wallet sign-in: server nonce, signed message bound to origin and chain, single use; HttpOnly SameSite cookie, CSRF header and Origin check on every write | enforced | `tests/webapp/test_auth.py`, `docs/architecture/webapp/auth.md` |
| **W2** | Step-up: anything that widens authority (tier raise, stop release, withdraw, approvals) needs a fresh owner signature bound to the action and its parameters, single use | enforced | `tests/webapp/test_stepup.py`, `tests/webapp/test_owner_routes.py`, hosted evidence `activate agent` step |
| **W3** | Per-owner isolation: every route takes the owner from the verified session only; owner-scoped storage; cross-owner reads and actions fail | enforced | `tests/webapp/test_tenant_paths.py`, `tests/storage/contract/test_isolation.py`, contract suite on SQLite and Postgres |
| **W4** | Agent keys are generated server-side, stored encrypted (KDF plus authenticated encryption AES-256-GCM, secret from environment) and never returned by any route or written to logs | enforced (testnet custody) | `tests/webapp/test_custody.py`, `tests/webapp/test_keystore.py`, `docs/architecture/webapp/custody-tenancy.md` |
| **W5** | Origin-aware policy: a planner cannot mint or transfer; owner functions need `origin="owner"`, set by code, never read from the order body | enforced | `tests/gateway/test_origin.py`, `tests/gateway/test_owner_functions_exec.py` |
| **W6** | Global stop (env flag, flag file or settings row) is re-read after simulation and immediately before signing; an unreadable flag stops | enforced | `tests/gateway/test_global_stop.py` |
| **W7** | Run jobs & split quota buckets: jobs are rate-limited to 6 action runs/hour per owner (`chat_action`), while informational questions use a separate 60 inquiries/hour bucket (`chat_inquiry`). Non-action inquiries automatically refund the action quota | enforced | `tests/webapp/test_jobs.py`, `tests/webapp/test_chat.py`, `docs/architecture/webapp/chat-quota-voice.md` |
| **W8** | Database: append-only audit and intents by trigger, row level security enabled on every table, no grants to Supabase data API roles, parameterised SQL only | enforced | migrations 0001 to 0005, `tests/storage` |
| **W9** | Hosted evidence is reproducible: `scripts/evidence/webapp_evidence.py` signs in as a throwaway owner and writes the record | enforced | `docs/evidence/webapp-evidence.md`, `docs/evidence/defi-evidence.md` |
| **W10** | Protocol registry: orders carry enums only; spenders, targets, markets and calldata layout live in code specs; after policy decision the gateway denies unlisted protocols or markets (`ACCESS.PROTOCOL_NOT_ENABLED`, `ACCESS.MARKET_NOT_ALLOWED`) | enforced | `tests/protocols/test_registry.py`, `tests/gateway/test_diversified_preset_e2e.py` |
| **W11** | Preset choice is a widening action: `PUT /tier` with a preset needs a step-up signature bound to the tier and that exact `preset_id`; a signature for the tier alone or for another preset is refused | enforced | `tests/webapp/test_owner_routes.py::test_choosing_a_preset_needs_a_signature_bound_to_that_preset` |
| **W12** | Owner cancel stops further orders of a plan before they reach the gateway, never calls the signer, never undoes an order already submitted; remaining steps are recorded as skipped | enforced | `tests/webapp/test_job_phases.py` |
| **W13** | Chat thread routing & persistence: threads are owner-scoped with row-level security (migration 0005); foreign thread id returns 404; active thread persists across page reloads and remounts; thread titles are set by code, not by the model | enforced | `tests/webapp/test_chat.py`, `tests/storage/contract/test_chat.py`, `tests/storage/test_migration_0005.py` |
| **W14** | Valuation fails closed: every USD value goes through one token registry; a token with no readable price is denied (`RISK.PRICE_UNAVAILABLE`), and swap minimum output is computed from the Aave oracle, never from pool reserves | enforced | `tests/protocols/test_registry.py`, `tests/protocols/test_uniswap_v3.py` |
| **W15** | Server-Sent Events (SSE) streaming: `GET /chat/stream/{job_id}` streams real-time phase updates (`planning`, `simulating`, `executing`) with periodic keep-alive pings; client automatically falls back to HTTP polling on network disconnect | enforced | `tests/webapp/test_sse.py`, `tests/ui/test_sse.py`, `docs/architecture/webapp/chat-sse.md` |
| **W16** | Concurrent portfolio reads & RPC keep-alive: `PortfolioReader.read` queries protocol positions concurrently via `ThreadPoolExecutor` (max 8 workers) with HTTPS connection reuse and retry, reducing latency while preserving fail-closed semantics | enforced | `tests/runner/test_portfolio.py`, `tests/test_http_stdlib.py`, `docs/architecture/protocols/rpc-latency.md` |
| **W17** | UI v3 overview & holdings: comprehensive dashboard featuring asset breakdown, token SVG icons, real-time progress card, balance-validated funds top-up & withdrawal dialogs, and interactive rules explorer | enforced | `tests/ui/test_overview.py`, `tests/ui/test_holdings.py`, `tests/ui/test_dialogs.py`, `tests/ui/test_progress.py`, `tests/ui/test_icons.py`, `tests/ui/test_rules_extras.py` |

## 4. Known-risk list

| ID | Risk | Severity | Status | Mitigation or plan |
| :--- | :--- | :---: | :---: | :--- |
| **K1** | Idempotency used to be held in memory (a restart could re-execute an `order_id`) | High | enforced | Idempotency is derived directly from the audit log; the same `order_id` after a restart returns the stored record and never signs again (`tests/gateway/test_idempotency.py`). |
| **K2** | `spent_24h_usd` and `tx_count_1h` could be spoofed if supplied by an untrusted caller | High | enforced | `AuditLedger` computes both metrics directly from verified executed records in the audit log (`tests/runner/test_ledger.py`), feeding verified values into the policy engine. |
| **K3** | A server crash between signing and recording leaves an executed transaction without a record | Medium | partial | An insert-only intent is written to disk/database prior to signing; an unmatched intent on startup raises `ReconcileRequired` and refuses new orders until inspected (`tests/gateway/test_intents.py`). |
| **K4** | Prices are reference values, not live DEX spot prices; USD balances reflect reference valuations | Medium | accepted | Every portfolio state is explicitly labelled `price_source: mainnet_reference`; tokens without a valid price feed fail closed with `RISK.PRICE_UNAVAILABLE`. |
| **K5** | Static or stale risk data could judge all markets by fixed numbers or pass an unsafe order | High | closed | On-chain risk data is retrieved via `RiskRouter` (`core/risk/router.py`) for Aave V3 reserves (USDC/USDT), Aave Stata vaults, Morpho Blue markets (utilization & TVL), and Uniswap V3 pools. Any RPC error, revert, or stale timestamp fails closed with `RISK.DATA_STALE`. Secondary metrics remain labelled reference values. Verified by `tests/risk/test_router.py`. |
| **K6** | Single public RPC endpoint; a compromised or lagging RPC could mislead simulation and balances | Medium | accepted | Testnet only; endpoint is configurable via environment; no real mainnet funds at risk. Mainnet roadmap requires multi-provider quorum. |
| **K7** | Testnet keys stored in local environment `.env` without HSM or KMS | Low (testnet) | accepted | Keys are strictly testnet-only and hold zero monetary value. Rotation accomplished by generating a new key pair. |
| **K8** | Owner token in single-tenant mode is a shared secret | Medium | accepted | Single-tenant mode uses constant-time comparison; multi-tenant webapp replaces this with SIWE session cookies and cryptographic step-up signatures. |
| **K9** | Browser-based audit chain verification in UI is convenience only | Low | accepted | UI explicitly notes that authoritative verification is performed via the Python CLI (`python -m core.audit.verify`). |
| **K10** | Single-process SQLite file locking (`BEGIN IMMEDIATE`) cannot scale horizontally across hosts | Low | accepted | Production multi-tenant mode utilizes PostgreSQL with row-level locks, transactional triggers, and RLS (`tests/storage`). |
| **K11** | LLM provider outage or API rate limits | Low | accepted | Replay mode (`ScriptedClient`, fixtures) runs the entire system offline; `FallbackPlanner` handles basic balance and allocation inquiries locally. |
| **K12** | No third-party professional audit or formal verification | Medium | accepted | Comprehensive self-assessment, 1,279 unit/e2e tests, and a 46-scenario evaluation harness serve as verification evidence. |
| **K13** | Custodial agent keys: server holds encrypted testnet agent keys; server compromise exposes keys | High (mainnet) | accepted for testnet | Testnet only, clearly disclosed on every page; keys encrypted at rest with AES-GCM-256. Roadmap Phase 2 implements ERC-4337 smart accounts and scoped session keys. |
| **K14** | Keystore secret and session secret stored in host environment variables | Medium | accepted | Secrets are managed via platform secrets management (Render/Supabase) outside git. Losing the keystore secret requires generating fresh testnet agent wallets. |
| **K15** | Free hosting cold starts and in-process job queue restarts | Low | accepted | Automated keep-warm via `/health/deep`; `mark_interrupted_on_startup` safely cleans pending jobs; actions are idempotent by `order_id`. |
| **K16** | Hosted LLM outputs malformed JSON or invalid order structures | Low | enforced | LLM output is strictly validated against `order.schema.json`; malformed `order_id` is replaced by code; invalid fields trigger immediate rejection. |
| **K17** | Gas funding dependency: agent cannot self-fund ETH for gas | Low | accepted | System enforces a gas floor check (0.000005 ETH) before executing any job; owner top-up is verified on-chain via receipt checks. |
| **K18** | Signature phishing risks via Web3 wallet popups | Low | accepted | SIWE messages and step-up requests explicitly state domain, chain ID, action parameters, and purpose ("Testnet only. This signature does not move funds"). |
| **K19** | Expanding protocol integrations introduces new contract addresses and spenders | Medium | enforced, owner review | Spenders and target contracts live in immutable code specs (never chosen by LLM); approvals are exact-amount only; unregistered protocols are denied (`ACCESS.PROTOCOL_NOT_ENABLED`). Modifying specs requires review under repo conventions. |
| **K20** | Choosing the diversified preset widens approved tokens (USDT, WBTC, WETH) and protocols | Medium | enforced, owner signature | Preset selection requires an explicit EIP-191 step-up signature bound to the preset ID and hash (`PUT /tier`). Callers default to `conservative` without signature. |
| **K21** | Risk rules for new protocols reading stale or uncalibrated reference data | High | closed | Closed by `RiskRouter` (`core/risk/router.py`), which reads live on-chain metrics (e.g. Morpho utilization at 93.56% triggers `RISK.DATA_STALE`/utilization deny in live evidence). |
| **K22** | Testnet DEX pool mispricing and artificial liquidity quirks | Medium | accepted for testnet | Swaps compute minimum acceptable output using Aave oracle reference prices; mispriced pools fail simulation (`eth_simulateV1`) and are never signed. |
| **K23** | In-flight job cancellation state maintained in process memory | Low | accepted | Valid for single-instance deployment; horizontally scaled production would store cancellation flags in the shared database. |
| **K24** | Chat quota exhaustion from conversational balance or status inquiries | Low | closed | Quota accounting is split into `chat_action` (6/hour) and `chat_inquiry` (60/hour). Inquiries generating zero orders automatically refund the action bucket (`tests/webapp/test_chat.py`). |
| **K25** | SSE streaming disconnects or proxy buffering on mobile connections | Low | closed | Implemented custom SSE client (`ui/sse.js`) with automatic reconnection backoff, keep-alive pings, and transparent fallback to standard HTTP polling (`tests/webapp/test_sse.py`). |

## 5. How to verify yourself (5 commands for judges)

A judge or auditor can independently verify the entire security and functional posture of this submission using these 5 commands:

```sh
# 1. Run the comprehensive test suite (1,279 unit, e2e, boundary, and contract tests pass)
python -m pytest -q

# 2. Run the offline evaluation harness (replays 46 scenarios, verifies 27/28 rule codes, injection defense, zero keys)
python -m evals.run

# 3. Verify the cryptographic SHA-256 audit log hash chain of real on-chain actions
python -m core.audit.verify docs/evidence/audit-chain.jsonl

# 4. Verify that no private keys, seed phrases, or secrets are tracked in the repository
python scripts/checks/check_no_secrets.py

# 5. Build and verify the complete 6-folder submission package structure
python scripts/submission/build_submission_package.py
```

### On-chain evidence files
- `docs/evidence/tx_hashes.md`: Initial approve and supply on Aave v3 on Base Sepolia.
- `docs/evidence/runner-evidence.md`: Agent runner execution and hostile-note policy denial.
- `docs/evidence/webapp-evidence.md`: Webapp sign-in, top-up, and planner-proposed transactions.
- `docs/evidence/defi-evidence.md`: Multi-protocol execution (Aave Stata vault deposit, Morpho Blue approval, and utilization denial).
- `docs/submission/06-execution-evidence.md`: Master index of all 10 verified on-chain transactions with BaseScan links and receipt status `0x1`.

## 6. Out of scope
Mainnet key management, custody of third-party user funds, smart-contract account abstraction deployment (ERC-4337), MEV protection, and high-frequency automated arbitrage.
