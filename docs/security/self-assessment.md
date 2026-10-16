# Security self-assessment

Scope: the agent-wallet system as built in this repository, testnet only (Base Sepolia, chain 84532). This report covers the permission-tier implementation and the known-risk list that the competition asks for. It states what is enforced and which test proves it, and it lists what is not done yet. Status values are `enforced` (code and test exist), `partial` and `open`.

Last updated against `main` after the multi-owner webapp (custody, wallet sign-in, Postgres storage, jobs, global stop) merged. The webapp controls are in section 3b and risks K13 to K18 in section 4.

## 1. Principles
1. The LLM proposes, code decides. The model never holds a key and cannot call the signer.
2. One path to the signer: only the gateway imports it. Everything else is data in, data out.
3. Default deny. A missing rule input (risk data, price, tier) denies; it is never read as zero.
4. Testnet only. The signer refuses any chain id other than 84532.
5. Every decision, including a denial, is written to a tamper-evident log with its reason and rule codes.

## 2. Permission tiers

| Tier | Meaning | What the gateway does | Enforced by |
| :--- | :--- | :--- | :--- |
| T0 | Read only | Denies every order with `TIER.READ_ONLY` before policy, simulation or signer | `tests/gateway`, `tests/e2e` |
| T1 | Propose only | Denies with `TIER.PROPOSE_ONLY`; the proposal is recorded | `tests/gateway`, `tests/e2e` |
| T2 | Execute inside the IPS | Policy engine decides; allowed orders are simulated, signed and recorded | `tests/policy`, `tests/e2e` |
| T3 (rule) | Owner approval required | Orders above `approval.escalate_above_usd` become `pending_approval`; only the owner can approve; policy is re-run on approval, so approval cannot override a `deny` | `tests/e2e` (escalate scenarios) |
| Emergency stop | Overrides every tier | Denies with `TIER.EMERGENCY_STOP` before anything else, including an already approved order; an unreadable tier state also denies | `tests/gateway`, `tests/e2e` |

Who can change what:

| Action | Who | Mechanism |
| :--- | :--- | :--- |
| Read tier, stop, approval status | Gateway, agent, API | `TierView` (read-only; test asserts it has no mutating public method) |
| Set tier, engage or release stop, approve, reject | Owner only | `OwnerControls`; agent-facing packages must not import it (AST boundary test) |
| Owner endpoints over HTTP | Owner only | `AGENT_WALLET_OWNER_TOKEN`, constant-time compare, `503` when unset (fail closed) |

## 3. Controls and their evidence

| # | Control | Status | Evidence |
| :--- | :--- | :--- | :--- |
| C1 | Only `core.gateway` imports `core.signer` | enforced | `tests/test_signer_boundary.py`, CI |
| C2 | Signer signs only chain 84532 and refuses a missing chain id | enforced | `tests/signer` |
| C3 | Key read from `AGENT_WALLET_PRIVATE_KEY` at construction; never in `repr`, `str`, logs or records; 64-hex strings redacted in error details | enforced | `tests/signer`, `tests/gateway` |
| C4 | Simulate before sign, from the signer's own address; a failed or unsupported simulation never signs; dependent calls are simulated as one bundle | enforced | `tests/gateway`, `tests/e2e` (`test_simulation_runs_from_the_signer_address`) |
| C5 | Allowances are exact-amount only (`ACCESS.UNLIMITED_APPROVAL`) | enforced | `tests/policy`, eval `deny-access-unlimited-approval` |
| C6 | Universe, per-transaction, per-day and per-hour limits, allocation and risk rules, 25 rule codes | enforced in the engine; limits input is partial (see K2) | `tests/policy`, eval report 25/25 codes |
| C7 | Policy engine is pure: no I/O, no float, no mutation | enforced | `tests/policy` purity tests |
| C8 | Audit log is an append-only SQLite hash chain; tampering, deletion and reordering are detected and the first bad `seq` is named | enforced | `tests/audit`, eval integrity scenarios, `docs/evidence/audit-chain.jsonl` verified by `python -m core.audit.verify` |
| C9 | Concurrent writers cannot corrupt or fork the chain (`BEGIN IMMEDIATE`, two- and three-process tests) | enforced | `tests/audit/test_sink_concurrency.py` |
| C10 | Prompt-injection defence: Reader returns structured facts only; Planner output must validate against the order schema; an out-of-universe recipient or token is still decided by the engine | enforced | `tests/agent`, 12 injection eval scenarios |
| C11 | Secrets: `.env` ignored, names only in `.env.example`, secret scan in CI | enforced | `scripts/check_no_secrets.py`, CI |
| C12 | The owner token lives in page memory only; API strings are rendered as text, never HTML | enforced | `tests/ui` |
| C14 | Concurrent proposes are serialised by a cross-process lock held from limit read to audit append; a lock timeout denies with nothing signed | enforced | `tests/gateway/test_lock.py` |
| C13 | Emergency stop and tier state are shared by API and runner through one persistent file | partial | `tests/tiers/test_shared_file.py` covers two stores on one file; the end-to-end API-to-runner test lands with the runner (T5) |

## 3b. Webapp controls (multi-owner mode)

| # | Control | Status | Evidence |
| :--- | :--- | :--- | :--- |
| W1 | Wallet sign-in: server nonce, signed message bound to origin and chain, single use; HttpOnly SameSite cookie, CSRF header and Origin check on every write | enforced | `tests/webapp/test_auth*.py`, `docs/architecture/auth.md` |
| W2 | Step-up: anything that widens authority (tier raise, stop release, withdraw, approvals) needs a fresh owner signature bound to the action and its parameters, single use | enforced | `tests/webapp`, hosted evidence `activate agent` step |
| W3 | Per-owner isolation: every route takes the owner from the verified session only; owner-scoped storage; cross-owner reads and actions fail | enforced | `tests/webapp` isolation tests, `tests/storage` contract suite on SQLite and Postgres |
| W4 | Agent keys are generated server-side, stored encrypted (KDF plus authenticated encryption, secret from the environment) and never returned by any route or written to logs | enforced (testnet custody) | `tests/gateway/test_custody*.py`, `docs/architecture/custody-tenancy.md` |
| W5 | Origin-aware policy: a planner cannot mint or transfer; owner functions need `origin="owner"`, set by code, never read from the order body | enforced | `tests/gateway/test_origin.py` |
| W6 | Global stop (env flag, flag file or settings row) is re-read after simulation and immediately before signing; an unreadable flag stops | enforced | `tests/gateway/test_global_stop.py` |
| W7 | Run jobs: one active job per owner, 6 runs per owner per hour, mint call-count limit, failed jobs never leave a half-recorded order | enforced | `tests/webapp/test_jobs.py` |
| W8 | Database: append-only audit and intents by trigger, row level security enabled on every table, no grants to the Supabase data API roles, parameterised SQL only | enforced | migrations 0001 to 0003, `tests/storage` |
| W9 | Hosted evidence is reproducible: `scripts/webapp_evidence.py` signs in as a throwaway owner and writes the record | enforced | `docs/evidence/webapp-evidence.md` |

## 4. Known-risk list

| ID | Risk | Severity | Status | Mitigation or plan |
| :--- | :--- | :--- | :--- | :--- |
| K1 | Idempotency used to be held in memory (a restart could re-execute an `order_id`) | High | enforced | Idempotency is now read from the audit log; the same id after a restart returns the stored record and never signs again (`tests/gateway/test_idempotency.py`) |
| K2 | `spent_24h_usd` and `tx_count_1h` are supplied by the caller of the engine, so daily and hourly limits depend on an honest caller | High | partial | `AuditLedger` derives both from executed records (`tests/runner/test_ledger.py`) and the portfolio reader fills them only from the ledger; the end-to-end runner wiring (T5b) closes it |
| K3 | A crash between signing and recording leaves an executed transaction without a record | Medium | partial | An insert-only intent is written before signing; an intent without a record raises `ReconcileRequired` and never re-signs (`tests/gateway/test_intents.py`). The owner `reconcile` tool (T7) that appends the truthful record is not built and is the first cut candidate |
| K4 | Prices are reference values, not testnet market prices; a record that says USD is a reference valuation | Medium | accepted | Every portfolio state is labelled `price_source: mainnet_reference`; unknown tokens deny |
| K5 | A falsified or stale risk input could let a benign-looking order through | Medium | partial | Risk data is read from chain by `OnchainAaveRiskProvider`; stale or missing data denies `RISK.DATA_STALE`; per-transaction caps bound the loss |
| K6 | One public RPC endpoint; an RPC that lies could mislead simulation and balances | Medium | accepted | Testnet only; endpoint is configurable; no mainnet funds |
| K7 | The testnet key sits in a local `.env`, with no HSM or KMS; during testing the owner imported it into a browser wallet to mint test tokens | Low (testnet) | accepted | Key is testnet-only and holds no real value; never reuse it; rotate by generating a new key; mainnet is out of scope |
| K8 | Owner token is a single shared secret | Medium | accepted | Fails closed when unset; constant-time compare; token never stored by the UI; production would use per-user auth |
| K9 | The browser chain check in the UI is convenience only | Low | accepted | The page says so; authoritative verification is the CLI |
| K10 | Single-process file locking: `BEGIN IMMEDIATE` serialises writers on one host, not across machines | Low | accepted | One wallet, one host; documented in the runner design |
| K11 | LLM provider outage or cost | Low | accepted | Replay mode (`ScriptedClient`, fixtures) runs the whole system offline |
| K12 | No third-party audit or formal verification | Medium | accepted | Self-assessment and the eval harness are the evidence offered |
| K13 | Custodial agent keys: the server holds every owner's testnet agent key, so a server compromise exposes them | High (would be critical on mainnet) | accepted for testnet | Testnet only, stated on every page; keys encrypted at rest; next step is non-custodial session keys under the same policy engine |
| K14 | The keystore secret and session secret live in the host environment; losing the keystore secret makes agent keys unrecoverable | Medium | accepted | Secrets are kept outside the repo; rotate by generating new agent wallets (no real funds) |
| K15 | Free hosting: cold starts, a single instance, in-process job queue (a restart marks running jobs interrupted) | Low | accepted | `/health/deep` keep-warm, `mark_interrupted_on_startup`; jobs are idempotent by `order_id` |
| K16 | The hosted LLM can return malformed or unhelpful output | Low | enforced | Output is schema-validated; a malformed order id is replaced by code, any other malformed field rejects; fallback planner is labelled; the policy engine decides regardless |
| K17 | Gas for the agent comes from the owner; the agent cannot self-fund ETH | Low | accepted | Gas floor check before any job; top-up verified on chain (sender, recipient, value) |
| K18 | Wallet sign-in proves control of an address only on a testnet; phishing of a signature request | Low | accepted | The message states origin, chain and purpose; step-up messages name the action |
| K19 | Each new protocol adds spender and target addresses that the agent may approve and call | Medium | enforced, owner review | Spenders and targets live in the registry spec in code (never in an order or from the LLM); approvals are the exact amount; an unregistered protocol or market is denied (`ACCESS.PROTOCOL_NOT_ENABLED`, `ACCESS.MARKET_NOT_ALLOWED`); adding a spender changes `core/protocols/*` and needs owner review (AGENTS.md: keys, funds, signer, permissions) |
| K20 | Choosing the diversified preset widens the agent's approved universe (tokens USDT, WBTC, WETH; protocols aave-stata, morpho-blue, uniswap-v3) | Medium | enforced, owner signature | Choosing a preset widens the owner-approved universe and limits; the selection occurs at activation via PUT /tier where the owner explicitly signs a step-up authorization covering the chosen preset's IPS hash; existing callers default to conservative with no change in universe or hash (`tests/gateway/test_diversified_preset_e2e.py`) |

## 5. How to reproduce the evidence
```sh
python -m pytest -q                                  # unit, e2e, boundary and contract tests
python -m evals.run                                  # 46 scenarios, offline, no key; writes docs/evidence/eval-report.md
python -m core.audit.verify docs/evidence/audit-chain.jsonl   # hash chain of the live run
python scripts/check_no_secrets.py                   # no secret is tracked
```
On-chain evidence: `docs/evidence/tx_hashes.md` (approve and supply of 1 test USDC on Base Sepolia, receipts `0x1`).
Planner-proposed evidence: `docs/evidence/runner-evidence.md` (approve and supply executed, a hostile-note supply of 50 USDC denied with `LIMIT.PER_TX`) and its verified chain `docs/evidence/runner-audit-chain.jsonl`. The planner in that run is scripted (deterministic replay); the rest of the path is real.

## 6. Out of scope
Mainnet operation, custody of third-party funds, smart-contract account abstraction, MEV protection, and any production key management.
