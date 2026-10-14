# Security self-assessment

Scope: the agent-wallet system as built in this repository, testnet only (Base Sepolia, chain 84532). This report covers the permission-tier implementation and the known-risk list that the competition asks for. It states what is enforced and which test proves it, and it lists what is not done yet. Status values are `enforced` (code and test exist), `partial` and `open`.

Last updated against `main` after the runner tickets T1 to T4 and T5a merged. The remaining runner tickets (T5b, T6, T7) are tracked in `docs/design/runner/design.md`; items that depend on them are marked `partial` or `open`.

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

## 5. How to reproduce the evidence
```sh
python -m pytest -q                                  # unit, e2e, boundary and contract tests
python -m evals.run                                  # 44 scenarios, offline, no key; writes docs/evidence/eval-report.md
python -m core.audit.verify docs/evidence/audit-chain.jsonl   # hash chain of the live run
python scripts/check_no_secrets.py                   # no secret is tracked
```
On-chain evidence: `docs/evidence/tx_hashes.md` (approve and supply of 1 test USDC on Base Sepolia, receipts `0x1`).
Planner-proposed evidence: `docs/evidence/runner-evidence.md` (approve and supply executed, a hostile-note supply of 50 USDC denied with `LIMIT.PER_TX`) and its verified chain `docs/evidence/runner-audit-chain.jsonl`. The planner in that run is scripted (deterministic replay); the rest of the path is real.

## 6. Out of scope
Mainnet operation, custody of third-party funds, smart-contract account abstraction, MEV protection, and any production key management.
