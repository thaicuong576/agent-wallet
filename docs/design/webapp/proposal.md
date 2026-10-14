# Webapp design (ticket #71), Phase A: options and recommendations

Status: proposal, waiting for the orchestrator's `advice-1.md`. Risk label: **`rủi-ro-cao`**. The webapp moves four security boundaries: key custody, the owner's authentication, the order contract (a new `mint` function) and the meaning of "no endpoint writes the IPS". Nothing in this phase changes code.

Scope of this phase: five questions (key custody, multi-tenancy, authentication and sessions, run flow, deployment), at most three options each. The other items (order contract, top-up, API fragment, frontend, threats, tickets, evidence) are answered in Phase B (`design.md`).

## 0. Facts read from the repo that drive the choices

| Fact | Where | Consequence |
| :--- | :--- | :--- |
| `LocalSigner(private_key=...)` already exists; only the no-argument form reads `AGENT_WALLET_PRIVATE_KEY` | `core/signer/__init__.py` | Per-owner keys need no change to the signer interface |
| `core.signer` may only be imported by `core.gateway`; `core/gateway/factory.py` already holds `build_signer` for owner tooling | `tests/test_signer_boundary.py`, `core/gateway/factory.py` | Key generation, keystore read, decrypt and signer construction must live inside `core/gateway/`; `api` and `core.runner` receive an address, never a key |
| `StatePaths`, `TierStore`, `SqliteAuditSink`, `GatewayLock`, `IntentStore`, `AuditLedger` all take file paths | `core/runner/config.py`, runner design | One directory per owner reuses every store unchanged |
| `AaveTxBuilder(rpc, wallet)` and `PortfolioReader(rpc, wallet, ledger)` take the wallet at construction | `core/gateway/txbuilder.py`, `core/runner/portfolio.py` | A gateway is built per owner, not shared |
| A propose can hold the gateway lock for up to about 120 s (estimate 30 s, simulate 30 s, send 30 s) and the lock wait is 15 s | `docs/design/runner/design.md` 5.7 | A run cycle of two orders can exceed typical hosted request timeouts |
| `create_app()` with no argument is an in-memory deny-all `T0` app | `api/app.py` | A real, env-driven factory is needed for the webapp |
| Owner endpoints use one shared `AGENT_WALLET_OWNER_TOKEN`, fail closed with 503 when unset (K8) | `api/app.py`, `docs/security/self-assessment.md` | Per-owner login replaces it for the webapp only |
| `contracts/openapi.yaml` says "No endpoint writes the IPS"; the contracts README classes IPS changes as high risk | `contracts/` | A preset endpoint is a meaning change and goes to the owner |
| `DB_PATH` is set in `Dockerfile` and `docker-compose.yml` and listed in `README.md`, but no Python file reads it (`grep -rn DB_PATH` found nothing in `*.py`) | repo | The container's real state directory is `AGENT_WALLET_DATA_DIR` (default `.data` under cwd), outside the `/app/data` volume |
| The `Dockerfile` copies `core`, `api`, `contracts` but not `ui`; `api/main.py` mounts `StaticFiles(directory=ui)` which checks the directory at startup | `Dockerfile`, `api/main.py` | The image as built cannot start the app with the UI mounted |
| `eth-account 0.14.0` (run in the repo `.venv`): `Account.encrypt(private_key, password, kdf=None, iterations=None)`; default kdf is scrypt with n=262144, r=8, p=1; one encrypt took 0.93 s on the dev laptop; `pbkdf2` with the default 1,000,000 iterations took 0.52 s to encrypt and 0.58 s to decrypt | measured in this session | Decrypt cost is real on a small host and must not run per request |

---

## 1. Key custody

Context. The agent wallet is a server-generated, **custodial, testnet-only** key. The owner never sees it. The server must be able to sign without the owner online, because the runner and the gateway do.

### Options

| | A. Keystore file per owner, secret from env | B. Keystore in the database, secret from env, per-key data key (envelope) | C. External KMS or CDP Server Wallet |
| :--- | :--- | :--- | :--- |
| How | `eth_account` Web3 Secret Storage JSON written to `owners/<addr>/keystore.json`, encrypted with a server secret `AGENT_WALLET_KEYSTORE_SECRET` | Same JSON, but stored as a row; a random data key per owner is wrapped by a master secret | Key never leaves a managed service; the gateway asks it to sign |
| New dependency | none | none | yes (SDK, account, network) |
| Offline and self-host (`docker compose up`) | works | works | needs an account and network; breaks replay |
| Attacker with the data volume but not the secret | holds an offline brute-force target; safe only if the secret is high entropy | same, one extra indirection that adds nothing for this threat | gets nothing |
| Rotation of the secret | re-encrypt each file, keep a previous-secret variable during the switch | re-wrap data keys only (cheaper) | provider-side |
| Fit for 6 days | best | more code for no gain here | does not fit; PLAN.md already puts CDP below the cut line |

**Recommendation: A.** One JSON keystore per owner, written by `eth_account` `Account.encrypt`.
- **Where the code lives:** a new `core/gateway/custody.py` (generate with `Account.create`, encrypt, write with `0600`, read, decrypt, build `LocalSigner(private_key=...)`). Only `core.gateway` may import it. The AST boundary test that guards `core.signer` is extended to guard `core.gateway.custody`, and `api` reaches it only through a thin `core/gateway/tenant.py` that returns an address or a ready `Gateway`.
- **Secret:** `AGENT_WALLET_KEYSTORE_SECRET`, a random value of at least 32 bytes (the docs will say "generate with `openssl rand -hex 32`", not a passphrase). Unset or short: wallet creation and every signing call fail closed with 503; reads keep working.
- **KDF cost:** default scrypt (n=262144) costs about 1 s and about 256 MB of memory per call. Recommend scrypt **with the key decrypted once per process and held in the gateway's signer object**, not per request: the cost is paid on first use after a restart and on rotation only. On a 512 MB hosted instance, set `kdf="pbkdf2"` (measured 0.5 to 0.6 s, no large allocation) through one constant in `custody.py`. This is a recorded trade-off, not a silent choice; both are standard keystore KDFs.
- **Held key in memory:** the cached `LocalSigner` keeps the key in process memory for the process lifetime (as `AGENT_WALLET_PRIVATE_KEY` does today, control C3). `repr` and `str` never include it; keystore JSON and secret are redacted in error details by the existing 64-hex redaction plus a new test for keystore fields.
- **Rotation, two layers:**
  1. *Secret rotation*: `AGENT_WALLET_KEYSTORE_SECRET_PREVIOUS` is accepted for decrypt during the transition; a `python -m core.gateway.custody rotate` command re-encrypts every keystore with the new secret and verifies the address is unchanged.
  2. *Agent key rotation*: needs funds moved to a new address. The order contract has no transfer function and no recipient enum names the owner, so **the owner currently has no way to get funds out of an agent wallet**. This is an open question for the owner (Phase B lists it with a recommendation): add an owner-only `withdraw_to_owner` outflow (a contract and policy change) or accept that test funds are abandoned on rotation (testnet).
- **What the database file alone gives an attacker:** addresses and audit history (no secrets), plus keystores they can try to brute-force offline. With a 256-bit random secret that is infeasible; with a human passphrase it is not. The page and README say plainly: **custodial, testnet only, never send real value**.

Boundary moved: signer construction input (key source). `rủi-ro-cao`.

---

## 2. Multi-tenancy

Context. One server, many owners. The existing stores are single-wallet.

### Options

| | A. One directory per owner | B. One database, `owner` column on every table | C. One process per owner |
| :--- | :--- | :--- | :--- |
| Layout | `DATA_DIR/owners/<40-hex lowercase address>/{state,audit,gateway.lock}.sqlite`, `keystore.json`, `ips.json`, `jobs.sqlite` | `owner` added to `audit_records`, `order_intents`, `tier_state`, `approval_queue` | a container or subprocess per owner |
| Code change | new `TenantRegistry`; every store unchanged | hash chain semantics change (chain per owner inside one table), every query, every trigger | orchestration, ports, memory |
| Isolation | filesystem path; the chain is per owner by construction | row filter; one missed `WHERE` leaks across owners | strongest |
| Hash chain | one chain per owner, verifiable by exporting one file | must define chains per owner in one table | one per owner |
| Cost on free hosting | one process, many small files | one file | too heavy |
| Risk | low; reuses audited code | `rủi-ro-cao` across audit, tiers, gateway | not feasible for 6 days |

**Recommendation: A.**
- **Routing:** the owner address comes **only** from the verified session. No route takes an owner address as a parameter, and no path segment is built from request data. The address is normalised to lowercase and checked against `^0x[0-9a-f]{40}$` before it is used as a directory name (no traversal, no case aliasing).
- **Per-owner objects**, built by `TenantRegistry.open(owner) -> Tenant` and cached by address: `TierStore`/`TierView`/`OwnerControls`, `SqliteAuditSink`, `GatewayLock`, `IntentStore`, `AuditLedger`, `PortfolioReader`, `AaveTxBuilder`, the signer and the `Gateway`. `OwnerControls` is created only in the API layer's owner routes, as today.
- **Global store:** the SIWE nonce table and session revocation list live in `DATA_DIR/auth.sqlite`, because they exist before an owner is known.
- **Cross-owner tests** (Phase B names them): a session for A cannot read B's `/portfolio`, `/decisions`, `/tier`, cannot approve B's `order_id`, and an `order_id` from A's chain is `404` in B's.

Boundary moved: the audit boundary gains a directory per owner. `rủi-ro-cao` (audit integrity and tiers are reused unchanged, but the routing layer is new).

---

## 3. Authentication and sessions

Context. Sign in with a browser wallet by signing a message; replace the shared owner token for the webapp.

### Options

| | A. SIWE-style message, HMAC-signed bearer token in JS memory | B. Same message, HttpOnly + SameSite=Strict cookie | C. Per-request signature (no session) |
| :--- | :--- | :--- | :--- |
| Verification | `eth_account` `encode_defunct` + `recover_message`; no new dependency | same | same, on every request |
| Token storage | JS variable; lost on reload (re-sign) | cookie the page cannot read | none |
| XSS reads the token | yes, while the page is open | no (but XSS can still act inside the page) | no token; XSS can still ask the wallet to sign, and the user sees each prompt |
| CSRF | not applicable (no ambient credential) | needs an Origin check plus a custom header | not applicable |
| Fit with control C12 and `tests/ui` | consistent: token kept in page memory only | new rule to write and test | user signs on every click; poor UX; gas-free but 6 prompts per run |
| Reload | re-sign (one prompt) | survives | n/a |

**Recommendation: B for the webapp session, with A kept as the transport for non-browser clients.** Reason: the page renders API strings as text, but a cookie removes the token from the XSS-readable surface, which is the larger standing risk, and the reload cost of A is a daily annoyance. CSRF is handled by a required `X-Requested-With`-style custom header plus an `Origin` allowlist on every state-changing route.
Details common to all options:
- **Message:** plain text containing the configured domain (`AGENT_WALLET_PUBLIC_ORIGIN`), the address, `chain id 84532`, a server nonce, `issued at` and `expiration time` (5 minutes). Verified server-side: recovered address equals the claimed one, nonce exists and is unused, domain matches, not expired. The nonce is deleted on use (single use); replay of the same signature fails.
- **Library:** no `siwe` package. `eth_account.messages.encode_defunct` and `Account.recover_message` are already a dependency.
- **EOAs only in v1.** A smart-contract wallet needs EIP-1271 and fails ecrecover; the page says so.
- **Session token:** `base64url(payload).base64url(HMAC-SHA256)` with payload `{addr, iat, exp, jti}`, key `AGENT_WALLET_SESSION_SECRET`, expiry 8 hours, `jti` in a revocation table for logout. Bound to the address; a token for A is never accepted for B because the address is read from the token and nothing else.
- **Owner-signed policy changes:** a session is not enough to change the IPS, the tier or to release a stop. Each such request carries a fresh wallet signature over a typed text that includes the action and the new `ips_hash` or tier (`personal_sign`, nonce and expiry again). This keeps a stolen session from rewriting the policy.
- **Shared owner token:** stays for the CLI, the eval harness and the replay mode, so `docker compose up` without a wallet still works. When a webapp session exists, it takes precedence on webapp routes; the token never maps to a tenant.
- **Contract meaning change:** `contracts/openapi.yaml` says no endpoint writes the IPS, and the contracts README says IPS changes are high risk. A preset endpoint changes that statement. It goes to the owner as a `rủi-ro-cao` decision.

Boundary moved: owner authentication, owner-only mutation of the IPS and of the tier. `rủi-ro-cao`.

---

## 6. Run flow

Context. One button runs `Runner.run_once` with the OpenAI planner (key in server env) or a fixed-schema fallback. The cycle can take well over 30 s with two orders and receipt waits.

### Options

| | A. Synchronous POST, response is the records | B. Job: `202` then poll `GET /runs/{id}` | C. Streamed response (SSE or chunked) |
| :--- | :--- | :--- | :--- |
| Hosting timeouts | request can exceed 30 to 100 s proxy limits; the client loses the result but the work continues | each request is short | proxies often buffer; free hosts may cut idle streams |
| Concurrency | second click starts a second request | explicit single active run per owner (`409`) | same as A |
| Restart | result lost | job row survives; marked `interrupted` | result lost |
| Complexity | lowest | a `runs` table and a worker thread | more moving parts, harder to test |
| UI | spinner only | step list with real progress | live steps |

**Recommendation: B.**
- `POST /runs` returns `202` with `{run_id, status: "queued"}`; the page polls `GET /runs/{run_id}` every 2 s (backoff to 5 s).
- Jobs are rows in `owners/<addr>/runs.sqlite`: `queued → running → succeeded | failed | interrupted`, the planner client used (`openai` or `fallback`), the list of `order_id` and `seq` values the run produced, and a sanitized error code. The runner still writes only through the gateway; the job row is a pointer, not a second record of decisions.
- **One active run per owner**: a second `POST` while one is `queued` or `running` returns `409 run-in-progress` with the active `run_id`. `GatewayLock` serialises orders within an owner anyway; across owners they run in parallel.
- **One worker process**: the app runs a single uvicorn worker and a small bounded thread pool; jobs left `running` after a restart are marked `interrupted` at startup (the audit log is the truth of what executed; `ReconcileRequired` is surfaced as its own status).
- **LLM unavailable:** the planner falls back to a fixed-schema client and the run result and every record the page shows carry `planner_client: "fallback"`. It is never presented as LLM output. A per-owner rate limit (for example 6 runs per hour) bounds LLM cost; Phase B sets the number and the test.
- **Failure modes** (`GatewayBusy`, `ReconcileRequired`, RPC unavailable) map to job statuses, not to HTTP 500s: Phase B tabulates them.

Boundary moved: none (the runner's imports are unchanged). `rủi-ro-thấp` for the job table; the planner client switch touches no boundary.

---

## 9. Deployment

Context. Judges may self-host with `docker compose up`; a hosted demo is optional.

### Defects in the current container setup (found by reading, to fix in the deployment ticket)
1. `Dockerfile` does not `COPY ui`, but `api/main.py` mounts it: the app fails at startup in the image.
2. `DB_PATH` is set but not read by any Python file; state goes to `AGENT_WALLET_DATA_DIR` (default `.data` under the working directory, `/app/.data`), which is **outside** the `./data:/app/data` volume. State would be lost on `docker compose down`.
3. `create_app()` with no argument is a deny-all in-memory app; `api/main.py` uses it. A real factory that builds the tenant registry from env is needed.
4. `scripts/` is not copied; the replay and evidence scripts are not in the image (acceptable, but `README` must say so).

### Options

| | A. Self-host only: `docker compose up`, named volume | B. Render | C. Railway |
| :--- | :--- | :--- | :--- |
| Persistence of SQLite and keystores | volume `./data` (or named volume) at `/app/data` with `AGENT_WALLET_DATA_DIR=/app/data` | **Free web services have an ephemeral filesystem**: local SQLite is lost on redeploy, restart and spin-down. Persistent disks are paid-only and only the mount path persists ([Render, Persistent Disks](https://render.com/docs/disks); [Render, Deploy for Free](https://render.com/docs/free)) | Volumes exist on the Trial and Free plans at 0.5 GB each, 5 GB on Hobby ($5/month) ([Railway volumes reference](https://docs.railway.com/volumes/reference); [Railway plans](https://docs.railway.com/pricing/plans)) |
| Spin-down | none | free services spin down after 15 minutes without inbound traffic and take about a minute to wake ([Render, Deploy for Free](https://render.com/docs/free)) | not stated in the sources I found; to confirm before relying on it |
| Cost | none | free (ephemeral) or paid with disk | trial credits (the source I found says $5, unconfirmed) or Hobby |
| Consequence for this app | none | **On a free instance the custodial keystores vanish with the filesystem, so every agent wallet and its test funds become unrecoverable after any spin-down.** Judges must not be sent to it as the only copy | works if a volume is attached and `AGENT_WALLET_DATA_DIR` points at it |

**Recommendation: A as the supported path, C as the hosted demo only with a volume, and do not use Render free for anything that holds a keystore.**
- Fix the four defects above; `AGENT_WALLET_DATA_DIR=/app/data`, run as the non-root user, `HEALTHCHECK` on `/health` (already present), one uvicorn worker.
- Compose adds only names (no values) to `.env.example`: `AGENT_WALLET_KEYSTORE_SECRET`, `AGENT_WALLET_SESSION_SECRET`, `AGENT_WALLET_PUBLIC_ORIGIN`, `AGENT_WALLET_DATA_DIR`, `OPENAI_API_KEY`, `BASE_SEPOLIA_RPC_URL`, `AGENT_WALLET_MODE` (`live` or `replay`). With no secret set the app starts in replay mode: no wallet creation, fixtures only.
- A banner on every page: "Testnet only (Base Sepolia). Custodial: the server holds this agent key. Do not send real funds." Test: `tests/ui` asserts the banner text is present and not removable by API data.
- A hosted demo that survives a restart must keep a **backup of the keystore directory**; the evidence plan (Phase B) records transactions in `docs/evidence/` so judges do not depend on the live instance.
- Free-tier limits and prices above come from the cited pages as found on 14 Oct 2026 and must be re-checked before the demo is deployed. I did not verify Railway's idle behaviour.

Boundary moved: deployment configuration and new secrets. Dependency and CI/deploy config changes go to the owner. `rủi-ro-cao`.

---

## Owner addendum 1 (2026-10-14): effect on the options

The owner asked for a production-grade, commercialisable design with staged delivery (Stage 0 demo path by 17 Oct, Stage 1 hardening by 19 Oct, Stage 2 roadmap only). Phase B applies it fully (`product.md`, production concerns, UI tokens, staged tickets). What it changes in the options above:

| Question | Change | Why |
| :--- | :--- | :--- |
| 1. Custody | Recommendation A stays for v1 but is framed as the **v1 custodial testnet model**. Phase B adds the production path: a non-custodial smart account (owner is the root, the agent holds a scoped session key limited by token, function, amount and expiry) with the **same** policy engine deciding off-chain and the account enforcing the same bounds on-chain. What changes: signer input becomes a session-key signer, `custody.py` is replaced by a session-key registry, the owner signs a grant instead of the server generating the key, and the fund-exit gap (open question 1) disappears because the owner keeps the root. Keystore rotation and `..._PREVIOUS` stay as designed. | Commercial path without rewriting the gateway. |
| 2. Tenancy | Option A (directory per owner) is mandatory from Stage 0, even with a single owner, so the layout never changes. Stage 0 may run one tenant; the cross-owner tests and a second-owner session land in Stage 1. | Avoids a data migration under a deadline. |
| 3. Auth | No change. Per-owner and global **kill switches** are added to the design: owner stop (existing tier flag, per owner) and an operator stop (`AGENT_WALLET_GLOBAL_STOP` or a file flag read by the gateway before any signing) that denies all owners. The global stop is a new boundary and is `rủi-ro-cao`. | Incident response. |
| 6. Run flow | Rate limits become two named controls: runs per owner per hour (LLM cost) and mints per owner per day (faucet). Both are enforced server-side and recorded; values are set in Phase B. | Abuse controls. |
| 9. Deployment | Adds environments (local, CI, hosted demo), CI/CD with the existing checks plus a webapp smoke test, secrets only from the host's secret store, backup and restore of `DATA_DIR/owners/` (nightly copy plus a restore test), retention (audit logs kept, nonces and expired sessions purged after 24 h), structured JSON logs with a request id and secret redaction, and an audit-chain verification endpoint per owner. Hosted option stays C with a volume; Render free remains unsafe for keystores. | Production concerns. |
| Cut order | If a Stage 1 item threatens Stage 0, Stage 0 wins. | Deadline is fixed. |

Honesty rules from the addendum are adopted: reference prices and replayed or fallback planner output are labelled in the UI and in records; no fake data on the live path.

---

## Owner addendum 2 (2026-10-14 night): decisions that supersede parts of this proposal

The owner decided the hosting and storage questions that were open here. `design.md` (see its "Amendments" table) is the source of truth; this section keeps the proposal consistent.

| Proposal section | Was | Now (owner decision) |
| :--- | :--- | :--- |
| 1. Key custody | keystore file per owner | same encrypted JSON, but stored through a `KeystoreStore`: a file on the SQLite backend or an **encrypted row in Postgres**; never plaintext; the secret still comes from `AGENT_WALLET_KEYSTORE_SECRET` |
| 2. Multi-tenancy | directory per owner (option A) | **two backends behind Protocols**: SQLite (directory per owner, local, replay, tests) and **Postgres** (one set of tables with an `owner` column, composite keys and row-level security). Both pass one parametrised contract suite |
| 9. Deployment | self-host recommended; Railway with volume as hosted option; Render free unsafe | **Render free web service (Docker) plus Supabase Postgres via `DATABASE_URL`**. With Postgres holding all state, Render's ephemeral filesystem no longer loses keystores. Render's own free Postgres expires after 30 days ([Render](https://render.com/docs/free)), so Supabase is used; its free plan pauses an idle project after a week (pricing page) |
| Mint (heads-up in Phase A) | caps of 1,000 per call and 3,000 per day | **no amount cap in our contract**; the faucet's on-chain maximum and cooldown apply; rate limit is a call count per owner per hour |
| New dependency | none | `psycopg[binary]` 3.x, an owner approval item |
| Open question 1 (funds exit) | open | answered by advice 1: owner-signed `transfer` to the owner (Stage 1) |
| Open question 3 (hosted demo) | owner choice | Render plus Supabase, with the unverified items listed in `design.md` 15.4 |

---

## Summary of recommendations

| # | Question | Recommendation | Risk |
| :--- | :--- | :--- | :--- |
| 1 | Key custody | A: `eth_account` keystore per owner in `core/gateway/custody.py`, secret from env, decrypt once and cache; pbkdf2 on small hosts; custodial and testnet stated on the page | `rủi-ro-cao` |
| 2 | Multi-tenancy | A: one directory per owner, address only from the session, `^0x[0-9a-f]{40}$` | `rủi-ro-cao` |
| 3 | Auth | B: SIWE-style message with `eth_account`, HMAC session in an HttpOnly cookie, per-action owner signatures for IPS and tier; no new dependency; EOAs only | `rủi-ro-cao` |
| 6 | Run flow | B: job with polling, one active run per owner, single worker, planner client labelled | `rủi-ro-thấp` |
| 9 | Deployment | Superseded by addendum 2: Render free web service plus Supabase Postgres (`DATABASE_URL`), `docker compose up` for self-host; fix four container defects | `rủi-ro-cao` (config and secrets) |

## Questions for the owner (answers recommended in Phase B)
1. How does an owner get funds out of an agent wallet, or is abandonment acceptable on testnet?
2. May the webapp write the IPS (preset endpoint), given that `openapi.yaml` currently says no endpoint does?
3. Is a hosted demo required, or is `docker compose up` plus recorded evidence enough?
