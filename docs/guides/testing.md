# Testing Guide & Verification Catalogue

This document details the test architecture, test layers, test case catalogue with exact counts, evaluation harness, CI mapping, and step-by-step verification commands for BaKhi Wallet.

## Test Philosophy: Defense in Depth

The testing strategy guarantees that **the LLM proposes, code decides, and the LLM never touches keys**. Test suites enforce security boundaries, deterministic rule decisions, protocol simulation, and tamper-evident auditing across 9 distinct layers.

```mermaid
flowchart TD
  subgraph CI ["Automated CI Pipeline (make check)"]
    L1["Layer 1: Static Boundaries & AST Scans (Signer & Intent isolation)"]
    L2["Layer 2: Data Contracts & Schema Validation (JSON Schemas, OpenAPI)"]
    L3["Layer 3: Policy Engine (101 pure deterministic tests)"]
    L4["Layer 4: Gateway Pipeline & Security Trust Boundary"]
    L5["Layer 5: Multi-backend Storage (SQLite & PostgreSQL with RLS)"]
    L6["Layer 6: Webapp Auth & Session Security (SIWE, Step-Up)"]
    L7["Layer 7: UI Fake-DOM & Component Flows (Node.js harness)"]
    L8["Layer 8: Evaluation Harness (48 replay scenarios)"]
  end

  subgraph Manual ["Manual Verification (Testnet keys required)"]
    L9["Layer 9: Live Base Sepolia Execution Scripts"]
  end

  L1 --> L2 --> L3 --> L4 --> L5 --> L6 --> L7 --> L8
```

---

## Quick Verification for Judges

Run the entire offline test suite and evaluation harness in under two minutes with no keys or network required:

```sh
# 1. Run full test suite (1,523 tests)
make test

# 2. Run static linters and repository hygiene checks
make lint
make links
make secrets

# 3. All-in-one CI command (lint, test, links, secrets)
make check

# 4. Run the 48-scenario evaluation harness
make eval

# 5. Verify cryptographic hash chain from live execution evidence
python -m core.audit.verify docs/evidence/audit-chain.jsonl
```

---

## Test Layers & What Each Proves

| Layer | Area | What It Proves | CI Status |
| :--- | :--- | :--- | :--- |
| **Layer 1** | Static Boundaries & AST Scans | Verifies through AST inspection that **no module other than the gateway imports the signer or custody**. Verifies runtime code never imports third-party HTTP clients (`httpx`, `requests`). Verifies rule codes are strictly append-only. | Run in CI |
| **Layer 2** | Contracts & Schemas | Verifies that all orders, portfolio snapshots, decisions, and IPS presets validate strictly against JSON Schemas and `contracts/openapi.yaml`. | Run in CI |
| **Layer 3** | Policy Engine | Verifies pure deterministic rule logic: per-order caps, rolling daily spend, approved protocols, spender allowlists, slippage protection, and utilization caps. Never makes network calls. | Run in CI |
| **Layer 4** | Gateway Pipeline | Verifies the single trust boundary: order submission, tier validation, policy evaluation, pre-execution simulation, signing, and audit logging. Verifies that denied orders are never simulated or signed. | Run in CI |
| **Layer 5** | Storage & Tenancy | Verifies dual-backend persistence (SQLite and PostgreSQL). Proves strict tenant isolation under PostgreSQL Row Level Security (RLS), advisory locking concurrency, and fail-closed schema checks. | Run in CI |
| **Layer 6** | Webapp & Authentication | Verifies EIP-4361 / SIWE wallet authentication, HttpOnly session cookies, CSRF protection, and cryptographic step-up challenge-response signatures for tier elevation and emergency stop release. | Run in CI |
| **Layer 7** | UI Node.js DOM Harness | Verifies frontend rendering, modal dialogs, focus trapping, holdings grouping, and streaming progress cards against a simulated DOM in Node.js. | Run in CI |
| **Layer 8** | Evaluation Harness | Replays 48 adversarial and real-world scenarios across prompt injection, risk feeds, registry checks and tier limits, covering 29 of the 31 rule codes. | Run in CI |
| **Layer 9** | Live On-Chain Scripts | Executes real transactions on Base Sepolia testnet using live RPC and funded agent keys. Proves real protocol interaction with Aave v3, Stata vaults, and Morpho Blue. | Manual only (needs keys) |

---

## Test Case Catalogue

Collected directly via `pytest --collect-only -q` per area:

| Area / Directory | Test Count | Key Test Files | What Is Tested |
| :--- | :---: | :--- | :--- |
| `tests/webapp/` | **389** | `test_chat.py`, `test_owner_routes.py`, `test_jobs.py`, `test_routes.py`, `test_stepup.py`, `test_sse.py` | Session routes, wallet sign-in, CSRF, step-up signatures, jobs and quotas, chat threads, SSE streaming |
| `tests/ui/` | **284** | `test_ui_static.py`, `test_ui_chat.py`, `test_ui_wallet.py`, `test_overview.py`, `test_dialogs.py`, `test_progress.py` | Contract route alignment, text-only rendering of API strings, chat and wallet flows, overview, dialogs |
| `tests/storage/` | **182** | `contract/test_chat.py`, `test_pg_stores.py`, `contract/test_tiers.py`, `contract/test_audit.py` | SQLite and Postgres backends against one contract suite, migrations, row level security, owner isolation |
| `tests/agent/` | **113** | `test_planner_chat.py`, `test_client.py`, `test_planner.py`, `test_reader.py` | Reader facts, planner orders, prompt-injection corpus, amount check against the owner's message |
| `tests/policy/` | **101** | `test_risk_rules.py`, `test_owner_functions.py`, `test_access_rules.py`, `test_limit_rules.py` | Pure evaluation of IPS rules, limits, allocation, risk and rule-code assignment |
| `tests/protocols/` | **82** | `test_aave_stata.py`, `test_uniswap_v3.py`, `test_aave.py`, `test_registry.py` | Calldata builders, protocol specs, spenders, exact approvals, pool-quote minimum output and price guard |
| `tests/gateway/` | **73** | `test_gateway.py`, `test_global_stop.py`, `test_txbuilder.py`, `test_owner_functions_exec.py` | Gateway pipeline, simulate before sign, tier gate, global stop, registry re-check, audit recording |
| `tests/risk/` | **46** | `test_provider.py`, `test_router.py` | Risk input routing, on-chain reads, staleness, fail closed |
| `tests/runner/` | **42** | `test_runner.py`, `test_portfolio.py`, `test_config.py`, `test_ledger.py` | Agent loop, portfolio reading, spend ledger from the audit log, `no_action` records |
| `tests/api/` | **31** | `test_owner.py`, `test_state_and_audit.py`, `test_orders.py`, `test_routes.py` | Core API routes, RFC 7807 problem details, OpenAPI conformance |
| `tests/tiers/` | **26** | `test_tiers.py`, `test_shared_file.py` | Tier state, emergency stop, approval queue, state shared between API and runner |
| `tests/audit/` | **22** | `test_audit_log.py`, `test_sink_concurrency.py` | SHA-256 hash chain, tamper detection, concurrent writers |
| `tests/ips/` | **19** | `test_loader.py` | Immutable IPS loading, presets, policy hashing |
| `tests/e2e/` | **18** | `test_gateway_e2e.py`, `test_runner_chain.py`, `test_runner_stop.py` | End-to-end flows with a mocked chain |
| `tests/evidence/` | **16** | `test_webapp_evidence_loop.py` | Evidence script classification and report output |
| `tests/signer/` | **14** | `test_signer.py` | Signing, chain 84532 restriction, no key in repr or logs |
| `tests/evals/` | **9** | `test_evals.py` | Eval harness determinism, coverage and report structure |
| `tests/*.py (root)` | **56** | `test_contracts.py`, `test_container_files.py`, `test_check_links.py`, `test_signer_boundary.py` | Schemas and rule codes, container files, link checker, signer import boundary |
| **Total Test Count** | **1,523** | — | **Full pytest collection across all suites** |

---

## How to Run Specific Test Suites

### 1. Unit & Policy Tests (Offline, Fast)
```sh
pytest tests/policy/ -q
pytest tests/ips/ -q
pytest tests/tiers/ -q
```

### 2. Gateway & Signer Boundary Tests
```sh
pytest tests/gateway/ -q
pytest tests/signer/ -q
pytest tests/test_signer_boundary.py -q
pytest tests/test_intent_boundary.py -q
```

### 3. Storage Tests (With & Without PostgreSQL)
By default, running `pytest tests/storage/` runs SQLite storage tests, connection pool sizing/warmup unit tests (`test_postgres_pool.py`), and skips tests requiring live PostgreSQL:
```sh
# SQLite and pool unit tests (offline, default)
pytest tests/storage/ -q
pytest tests/storage/test_postgres_pool.py -q
```
To run full PostgreSQL tests (including RLS and migration concurrency), supply `TEST_DATABASE_URL`:
```sh
# PostgreSQL tests (requires running PostgreSQL instance)
TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/agent_wallet_test" pytest tests/storage/ -q
```


### 4. Webapp Authentication & Session Tests
```sh
pytest tests/webapp/ -q
```

### 5. UI Tests (Static Scans & Node.js Fake-DOM Harness)
The UI test suite combines Python static analysis with direct Node.js headless flows:
```sh
# Python test runner (automatically discovers Node.js)
pytest tests/ui/ -q

# Direct Node.js execution of UI flow verification scripts
node tests/ui/flows.mjs
node tests/ui/chat_flows.mjs
node tests/ui/harness.mjs
```

### 6. Evaluation Harness (48 Replay Scenarios)
The evaluation suite runs 48 deterministic offline scenarios testing edge cases, adversarial prompts, risk thresholds, and tier elevation:
```sh
python -m evals.run
```
Outputs are written to:
- Markdown report: [`docs/evidence/eval-report.md`](../evidence/eval-report.md)
- JSON results: `docs/evidence/eval-results.json`

Rule-code coverage: **29 of 31 codes**. `TIER.POLICY_CHANGED` is a reserved status code that no code path writes yet; `RISK.PRICE_UNAVAILABLE` is raised by the Uniswap v3 builder and covered by `tests/protocols/test_uniswap_v3.py`.

### 7. Live Scripts (Require Keys & RPC — NOT Run in CI)
These scripts communicate with Base Sepolia and are intended for manual testing and generating submission evidence:
```sh
# Execute first live transaction on Base Sepolia
python scripts/evidence/live_first_tx.py --execute

# Execute hosted webapp live multi-protocol flow
python scripts/evidence/webapp_evidence_defi.py --execute

# Execute agent runner autonomous planning flow
python scripts/evidence/runner_evidence.py --execute
```

---

## CI Mapping

Our GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push and pull request against `main`:

```yaml
jobs:
  check:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        ports: [5432:5432]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements-dev.txt
      - run: make check

  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t agent-wallet:ci .
      - run: scripts/checks/container_smoke.sh agent-wallet:ci
```

1. **`make check`** triggers:
   - `make lint` (`ruff check .`)
   - `make test` (`pytest -q` executing all 1,523 tests against SQLite and PostgreSQL service container)
   - `make links` (`python scripts/checks/check_links.py` checking all relative links and markdown anchors)
   - `make secrets` (`python scripts/checks/check_no_secrets.py` ensuring no private keys, seed phrases, or credentials exist in code or fixtures)
2. **`docker`** builds `Dockerfile` and executes `scripts/checks/container_smoke.sh` to verify non-root UID 10001 execution, `/app/data` volume permissions, and `/health` response.

---

## Makefile Target Reference

All primary developer tasks are wrapped in thin Makefile targets:

| Target | Command | Purpose |
| :--- | :--- | :--- |
| `make test` | `$(PY) -m pytest -q` | Run the complete 1,523 pytest test suite |
| `make lint` | `$(PY) -m ruff check .` | Check linting and code style rules |
| `make format` | `$(PY) -m ruff format .` | Format code with Ruff |
| `make check` | `lint test links secrets` | Comprehensive CI check (tests, style, links, secrets) |
| `make eval` | `$(PY) -m evals.run` | Run the 48 evaluation scenarios and generate report |
| `make demo` | `$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000` | Start local API server for zero-key browser demo |
| `make links` | `$(PY) scripts/checks/check_links.py` | Verify all markdown relative links and anchors |
| `make secrets` | `$(PY) scripts/checks/check_no_secrets.py` | Scan repository for accidental secret leaks |
| `make docker-build` | `docker build -t agent-wallet:dev .` | Build local development Docker container |
| `make docker-run` | `docker run --rm -p 8000:8000 --env-file .env agent-wallet:dev` | Run container locally |
