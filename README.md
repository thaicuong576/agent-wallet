# agent-wallet

An AI wallet agent for FinTechathon 2026 (Topic F, Agent Wallet). The agent holds its own funded wallet and acts only inside an Investment Policy Statement (IPS) set by the owner. A deterministic policy engine decides every order, and an eval suite measures whether the agent complies.

> Status: the safe core is built and tested (policy engine, tiers, gateway, signer, audit chain, risk provider, planner and reader, API, owner UI, eval harness). Two transactions are on Base Sepolia (testnet only). The agent runner that ties the planner to the gateway end to end is in progress; see the known-risk list.

## For judges: verify in five minutes, no keys needed
```sh
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scriptsctivate
pip install -r requirements-dev.txt
python -m pytest -q                              # full test suite, offline
python -m evals.run                              # 46 scenarios, 27/28 rule codes; writes docs/evidence/eval-report.md
python -m core.audit.verify docs/evidence/audit-chain.jsonl   # hash chain of the live run
```
| Evidence | Where |
| :--- | :--- |
| On-chain transactions (approve and supply 1 test USDC, receipts `0x1`) | [`docs/evidence/tx_hashes.md`](docs/evidence/tx_hashes.md) |
| Planner-proposed run: two executed orders, one hostile order denied, verified chain | [`docs/evidence/runner-evidence.md`](docs/evidence/runner-evidence.md) |
| Evaluation report (offline replay) | [`docs/evidence/eval-report.md`](docs/evidence/eval-report.md) |
| Technical documentation | [`docs/technical-documentation.md`](docs/technical-documentation.md) |
| Security self-assessment and known risks | [`docs/security/self-assessment.md`](docs/security/self-assessment.md) |
| Architecture notes | [`docs/architecture/`](docs/architecture/) |

## Quick start
Requires Python 3.11 and, optionally, Docker.

```sh
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
make check                      # lint, tests, secret scan; needs no keys and no network
make run                        # API on http://127.0.0.1:8000 (GET /health)
```

With Docker:
```sh
cp .env.example .env            # optional; the API starts without keys
docker compose up --build       # API on http://localhost:8000
```
The container runs the API and static console at `/ui` as non-root (UID 10001). Runtime state persists in `./data:/app/data` under `AGENT_WALLET_DATA_DIR`. Repository scripts (`scripts/`, `evals/`) are not bundled into the image and run from a local checkout. Architecture note: [`docs/architecture/deployment.md`](docs/architecture/deployment.md).

## Run the UI
The webapp UI (plain HTML, CSS and JS modules, no build step, no external assets) is served by the API at `/ui`. It follows the design in [`docs/design/webapp/system-design.md`](docs/design/webapp/system-design.md) and the look in [`DESIGN.md`](DESIGN.md): onboarding stepper (Connect, Fund, Mint, Policy, Run), dashboard, and a decision feed that explains every outcome in plain language.

```sh
uvicorn api.main:app                                  # then open http://127.0.0.1:8000/ui/
# no backend needed for a look around: http://127.0.0.1:8000/ui/?fixtures=1&demo=1
```
`?fixtures=1` answers every call from `ui/fixtures/*.json` and a stub wallet (a banner chip says so). `&demo=1` starts signed in and funded; `&scenario=hostile` starts with a blocked hostile order in the feed. Without it the page is live: it talks to the session routes through `ui/api.js` (wallet sign-in, HttpOnly cookie, `X-AW-CSRF` header) and needs the API started with `AGENT_WALLET_MODE=multi`, a session secret, a keystore secret and `AGENT_WALLET_PUBLIC_ORIGIN` set to the address in the browser bar (see `docs/architecture/api.md`). Activate agent (raises the tier from T0 with one signature), Fund, Mint, Run, Withdraw, stop and approvals work from the page; the live Dashboard shows only the signed-in owner's own balances. Previews: [`docs/previews/`](docs/previews/). Design note: [`docs/architecture/ui.md`](docs/architecture/ui.md).

## How it works
```mermaid
flowchart LR
  U[Owner sets IPS] --> P[Planner LLM proposes order]
  P --> G[Gateway]
  G --> T{Tier gate}
  T --> E{Policy engine}
  E -- allow --> S[Simulate then sign] --> C[(Base Sepolia)]
  E -- deny or escalate --> L[Audit log with reason]
  S --> L
```
The LLM proposes and code decides; the LLM never holds a key. Only the gateway can reach the signer, and every outcome is written to a hash-chained log. Details: [`docs/technical-documentation.md`](docs/technical-documentation.md).

## Runner
`core.runner.runner.Runner.run_once(untrusted_text, now)` runs one cycle: the Reader extracts facts, the Planner proposes orders, the portfolio reader supplies state and ledger limits, and each order goes through `Gateway.propose`. A planner `no_action` is recorded through `Gateway.record_no_action`. The runner never imports the signer, the owner controls or the intent store. Design note: [`docs/architecture/runner.md`](docs/architecture/runner.md).

## Make targets
| Target | What it does |
| :--- | :--- |
| `make check` | lint, tests and secret scan (what CI runs) |
| `make test` | pytest, including contract-example validation and the signer-boundary test |
| `make lint` / `make format` | ruff |
| `make run` | local API with reload |
| `make docker-build` / `make docker-run` | image build and run |

## Configuration
Environment variable names only (see `.env.example`). Values live in `.env`, which is never committed. Testnet keys only.

| Variable | Purpose |
| :--- | :--- |
| `OPENAI_API_KEY` | Planner and Reader LLM |
| `AGENT_WALLET_PRIVATE_KEY` | Testnet agent wallet key; read only by the signer |
| `AGENT_WALLET_OWNER_TOKEN` | Owner token for the owner endpoints and UI; unset means they answer 503 |
| `BASE_SEPOLIA_RPC_URL` | JSON-RPC endpoint (default `https://sepolia.base.org`) |
| `AGENT_WALLET_DATA_DIR` | Directory for SQLite state files (default `.data`, `/app/data` in container) |
| `IPS_PATH` | The Investment Policy Statement file |

## Repository map
| Path | Purpose |
| :--- | :--- |
| `PLAN.md` | Goal, scope, phases, milestones, risks |
| `AGENTS.md` | Conventions for the agents that build this repo |
| `contracts/` | Data contracts v1: JSON Schemas, OpenAPI, rule codes, examples (see `contracts/README.md`) |
| `core/` | Gateway, signer, policy engine, tiers, audit log, risk provider, planner and reader, protocol adapters |
| `evals/` | Offline replay evaluation harness |
| `api/` | FastAPI app |
| `ui/` | Static webapp UI served at `/ui` |
| `tests/` | pytest suite |
| `scripts/` | Repository checks (secret scan) and the first-live-transaction script (dry run by default) |
| `docs/` | Competition rules, design notes, architecture notes (see `docs/README.md`) |
| `Dockerfile`, `docker-compose.yml` | Container image and one-command run |

## Submission
Deadline: 20 Oct 2026, 23:59 Beijing time. Required items and rules: `docs/competition/`.
