# Deployment Guide

This guide describes end-to-end deployment for `agent-wallet` across local Docker, Docker Compose, and hosted cloud infrastructure (Render and Supabase).

## Architecture & Deployment Model

`agent-wallet` runs as a stateless container using `python:3.11-slim` executed by an unprivileged user (`app`, UID 10001). The application serves both the FastAPI REST backend and the static single-page web application (`/ui/`).

```mermaid
flowchart TD
  subgraph Client ["Client Browser / Evaluator"]
    B[Webapp UI / API caller]
  end

  subgraph Render ["Hosted Container (Render Free Web Service)"]
    API["FastAPI App (api/main.py)"]
    UI["Static UI (/ui/)"]
    API --- UI
    HEALTH["/health & /health/deep"]
  end

  subgraph Supabase ["Supabase Postgres (Free Tier)"]
    POOLER["Session / Transaction Pooler"]
    DB[(PostgreSQL 16 with RLS)]
    POOLER --> DB
  end

  subgraph Blockchain ["Base Sepolia Testnet"]
    RPC["JSON-RPC (Chain ID 84532)"]
  end

  B -->|HTTPS| API
  API -->|Port 6543 (DATABASE_URL)| POOLER
  API -->|JSON-RPC| RPC
```

## Environment Variables

All settings are configured through environment variables (declared in `.env.example`). Values must never be committed to git.

| Variable | Default in Container | Description | Required |
| :--- | :--- | :--- | :--- |
| `AGENT_WALLET_MODE` | `single` | `multi` enables multi-owner webapp mode with wallet sign-in; `single` uses token auth | Yes for webapp |
| `AGENT_WALLET_DATA_DIR` | `/app/data` | Local directory for SQLite state files (`.data` locally, `/app/data` in container) | No |
| `BASE_SEPOLIA_RPC_URL` | `https://sepolia.base.org` | JSON-RPC provider for Base Sepolia testnet | Yes for live runs |
| `OPENAI_API_KEY` | (unset) | API key for LLM Planner and Reader (falls back to labeled fallback if unset) | Optional |
| `AGENT_WALLET_PRIVATE_KEY` | (unset) | Testnet private key for the agent wallet (signer only, single mode) | Single mode only |
| `AGENT_WALLET_OWNER_TOKEN` | (unset) | Bearer token for single-owner admin endpoints; unset returns 503 | Single mode only |
| `AGENT_WALLET_KEYSTORE_SECRET` | (unset) | 256-bit secret used to encrypt custodial agent keys at rest | Yes for multi mode |
| `AGENT_WALLET_KEYSTORE_KDF` | `scrypt` | Key derivation function (`scrypt` or `pbkdf2`; use `pbkdf2` on 512MB RAM hosts) | Recommended `pbkdf2` on free tiers |
| `AGENT_WALLET_SESSION_SECRET` | (unset) | Secret used to sign HttpOnly session cookies | Yes for multi mode |
| `AGENT_WALLET_PUBLIC_ORIGIN` | (unset) | Allowed public origin URL (e.g. `https://agent-wallet-6vuy.onrender.com`) | Yes for hosted webapp |
| `AGENT_WALLET_CHAT_INQUIRIES_PER_HOUR` | `60` | Rate limit for chat jobs that propose 0 orders | No |
| `AGENT_WALLET_LOG_LEVEL` | `INFO` | Log level for `agent_wallet` loggers (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | No |
| `IPS_PATH` | `contracts/presets/conservative.json` | Path to Investment Policy Statement JSON | No |
| `AGENT_WALLET_RISK_SOURCE` | `onchain` | Risk provider source: `onchain` (live RPC reads) or `reference` (offline fixtures) | No |
| `DATABASE_URL` | (unset) | Postgres connection URL. If unset, SQLite backend under `AGENT_WALLET_DATA_DIR` is used | Yes for Postgres |
| `AGENT_WALLET_DB_POOL_SIZE` | `12` | PostgreSQL connection pool size (1..50; invalid falls back to 12) | No |
| `DATABASE_MIGRATE_URL` | (unset) | DDL-capable Postgres URL for `python -m core.storage.migrate` | For migrations |
| `POSTGRES_PASSWORD` | (unset) | Password for local `docker compose` database service | Docker compose only |

| `TEST_DATABASE_URL` | (unset) | Dedicated throwaway Postgres URL for contract test suites | Tests only |

---

## 1. Local Container Deployment

### Docker Build & Run
Verify the container builds and runs locally with persistent SQLite storage:

```sh
# 1. Build dev image
docker build -t agent-wallet:dev .

# 2. Run container with data volume mount
docker run --rm -d \
  --name agent-wallet \
  -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  --env-file .env \
  agent-wallet:dev

# 3. Check container logs and health
docker logs agent-wallet
curl http://127.0.0.1:8000/health
```

### Docker Compose (API + PostgreSQL)
For running the full multi-owner stack with PostgreSQL locally:

```sh
# 1. Copy sample environment
cp .env.example .env

# 2. Set POSTGRES_PASSWORD in .env
# Windows PowerShell: Add-Content .env "POSTGRES_PASSWORD=devpassword"
# Linux / macOS: echo "POSTGRES_PASSWORD=devpassword" >> .env

# 3. Start services
docker compose up --build -d

# 4. Apply migrations to the local database
python -m core.storage.migrate

# 5. Check service health
docker compose ps
curl http://127.0.0.1:8000/health
```

---

## 2. Production Deployment on Render

The production deployment runs on the Render Free Tier.

### Web Service Specification
- **Runtime**: Docker
- **Repository**: Connected GitHub repository, branch `main`
- **Instance Type**: Free (0.1 CPU, 512 MB RAM)
- **Auto-Deploy**: Yes (triggers on pushes to `main`)
- **Health Check Path**: `/health` (initial delay: 30s)

### Environment Configuration on Render
Configure these environment variables in the Render Dashboard under **Environment**:
```env
AGENT_WALLET_MODE=multi
AGENT_WALLET_KEYSTORE_KDF=pbkdf2
AGENT_WALLET_KEYSTORE_SECRET=<random-high-entropy-string>
AGENT_WALLET_SESSION_SECRET=<random-high-entropy-string>
AGENT_WALLET_PUBLIC_ORIGIN=https://agent-wallet-6vuy.onrender.com
BASE_SEPOLIA_RPC_URL=https://sepolia.base.org
DATABASE_URL=postgresql://postgres.<project-id>:<password>@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require
OPENAI_API_KEY=<openai-api-key>
```

> [!NOTE]
> Do NOT set `DATABASE_MIGRATE_URL` on the Render web service runtime. The web service runs with least-privilege app credentials (no DDL rights). Migrations are applied out-of-band.

---

## 3. Database Setup: Supabase Postgres & Migrations

The multi-owner backend uses PostgreSQL with Row Level Security (RLS) enabled across all tenant tables.

### Supabase Connection Strings
Supabase exposes two pooler endpoints:
1. **Transaction Mode (`port 6543`)**: Use for `DATABASE_URL`. High throughput, multiplexed client connections, safe behind transaction boundaries.
2. **Session Mode (`port 5432`)**: Use for `DATABASE_MIGRATE_URL`. Direct or session-pooler connection supporting DDL execution and advisory locks.

### Ordered Migrations
Migrations live in `core/storage/migrations/` and must be applied sequentially:
1. `0001_init.sql` — Initial schema: `tenants`, `keystores`, `audit_records`, `jobs`, `topups`, `policy_presets`.
2. `0002_close_data_api.sql` — Revoke public schema API access from unauthorized PostgREST roles.
3. `0003_rls_all_tables.sql` — Enforce PostgreSQL Row Level Security (RLS) policies across all tables.
4. `0004_chat.sql` — Chat message history and streaming job association schema.
5. `0005_chat_threads.sql` — Multi-thread conversation lifecycle and hash route mapping.

### Running and Verifying Migrations
```sh
# Apply pending migrations using session pooler URL
DATABASE_MIGRATE_URL="postgresql://postgres.<id>:<pass>@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require" \
python -m core.storage.migrate

# Verify schema is current (read-only verification, exits 0 if current)
python -m core.storage.migrate --check
```

### What Happens When Database Schema Is Behind (Fail Closed)
In `core/storage/migrate.py` and `api/main.py`:
- `schema_migrations` tracks applied version integers, migration names, and SHA-256 checksums.
- Each migration takes a PostgreSQL transaction advisory lock (`pg_advisory_xact_lock(7240001)`) to eliminate race conditions.
- When `DATABASE_MIGRATE_URL` is set: The server automatically runs `backend.migrate()` at boot.
- When `DATABASE_MIGRATE_URL` is unset: The server executes `backend.verify_schema()`. If pending migrations exist or if a checksum mismatches, `check_schema()` raises `MigrationError`. `build_backend` catches this and raises `RuntimeError("database schema is not ready: ...")`.
- **Fail closed behavior**: The application process exits immediately and refuses to start, preventing corrupted or unindexed operations against an outdated schema.

---

## 4. Secret Rotation Procedures

| Secret | Impact | Rotation Procedure |
| :--- | :--- | :--- |
| `AGENT_WALLET_SESSION_SECRET` | Active user sessions | 1. Generate new 32+ character random string.<br/>2. Update variable in Render dashboard.<br/>3. Redeploy service. Active session cookies become invalid; users simply sign in again via wallet signature (zero data loss). |
| `AGENT_WALLET_KEYSTORE_SECRET` | Custodial private keys at rest | Master key encrypting wallet keystores via AES-256-GCM. To rotate: decrypt stored keystores with old secret and re-encrypt with new secret in a maintenance script before updating the service. |
| `AGENT_WALLET_PRIVATE_KEY` | Single-owner wallet funds | 1. Transfer remaining testnet ETH/USDC to new wallet address.<br/>2. Update `AGENT_WALLET_PRIVATE_KEY` in `.env`.<br/>3. Restart service. |
| `OPENAI_API_KEY` | LLM Planner & Reader | 1. Issue new key in OpenAI console.<br/>2. Update `OPENAI_API_KEY` in environment.<br/>3. Revoke old key. If revoked unexpectedly, the app falls back to labeled rule-based fallback planning without crashing. |
| `DATABASE_URL` | PostgreSQL access | 1. Rotate password in Supabase database settings.<br/>2. Update `DATABASE_URL` in Render environment.<br/>3. Service reconnects on restart. |

---

## 5. Verification: 5 curl Commands

Execute these 5 curl commands against the deployed service URL (`https://agent-wallet-6vuy.onrender.com` or `http://localhost:8000`):

```sh
# 1. Shallow Liveness Check: verifies HTTP server is running
curl -f -s https://agent-wallet-6vuy.onrender.com/health
# Expected: {"status":"ok"}

# 2. Deep Readiness & Database Check: executes read against PostgreSQL settings table
curl -f -s https://agent-wallet-6vuy.onrender.com/health/deep
# Expected: {"status":"ok","db":"ok"}

# 3. Protocol Registry: verifies supported DeFi adapters and spender contracts
curl -f -s https://agent-wallet-6vuy.onrender.com/protocols
# Expected: JSON object listing aave-v3, aave-stata, morpho-blue, uniswap-v3

# 4. Policy Presets Contract: verifies available Investment Policy Statements
curl -f -s https://agent-wallet-6vuy.onrender.com/presets
# Expected: JSON list with "conservative" and "diversified" preset hashes

# 5. Static Webapp Console: verifies single-page UI is served successfully with no-cache header
curl -I -s https://agent-wallet-6vuy.onrender.com/ui/index.html | head -n 5
# Expected: HTTP/1.1 200 OK and Cache-Control: no-cache
```

---

## 6. Latency Visibility, Log Lines & UI Caching

### Logging Configuration & `AGENT_WALLET_LOG_LEVEL`
The application configures structured stdout logging for the `agent_wallet` hierarchy (`agent_wallet.http`, `agent_wallet.rpc`, `agent_wallet.timing`) via `configure_logging()`. The log level is controlled by the `AGENT_WALLET_LOG_LEVEL` environment variable (defaults to `INFO`).

### Reading Latency in Render Logs
On hosted environments like Render, log lines appear in real time under the service logs. Three loggers make time visible:

1. **HTTP Requests (`agent_wallet.http`)**:
   Emits per-request duration, RPC, and DB metrics:
   ```text
   2026-10-18 00:00:00,123 INFO agent_wallet.http method=GET path=/presets status=200 elapsed_ms=4 rpc_calls=0 rpc_ms=0 db_calls=0 db_ms=0
   ```
   - `path`: Route path only, no query string. Excludes `/health`, `/health/deep`, and `/ui/*`.
   - `elapsed_ms`: Wall-clock milliseconds from request arrival until response completes (for SSE `/chat/stream/*`, logged when the stream terminates).
   - `rpc_calls` / `rpc_ms`: Accumulated JSON-RPC call count and latency during that HTTP request.
   - `db_calls` / `db_ms`: Accumulated PostgreSQL round trips and latency during that HTTP request.


2. **JSON-RPC Calls (`agent_wallet.rpc`)**:
   Emits outbound JSON-RPC timing to Base Sepolia testnet without logging parameters, URLs, or secrets:
   ```text
   2026-10-18 00:00:01,456 INFO agent_wallet.rpc method=eth_getBalance elapsed_ms=182 status=ok
   ```

3. **Chat & Runner Milestones (`agent_wallet.timing`)**:
   Emits execution milestones for background jobs:
   ```text
   2026-10-18 00:00:02,100 INFO agent_wallet.timing job=<32-hex-id> phase=planner_first_token elapsed_ms=1250
   2026-10-18 00:00:05,300 INFO agent_wallet.timing job=<32-hex-id> phase=chat_total elapsed_ms=4450
   ```
   - `planner_first_token`: Milliseconds from job start until the first token piece is received from LLM streaming.
   - `chat_total`: Total milliseconds for the entire chat lifecycle.

### UI Cache Rule (`/ui/*`)
Static files served under `/ui/` (`/ui/index.html`, `/ui/app.js`, etc.) return:
```text
Cache-Control: no-cache
```
The browser is allowed to store assets locally but is required to revalidate via HTTP `304 Not Modified` (ETag) on every request. This ensures that new UI code or stylesheet deployments are immediately reflected in user sessions without requiring manual cache clearing.

---

## 7. Common Failures & Fixes

### 1. Render Free Tier Sleep (Cold Starts)
- **Symptom**: First request after 15 minutes of inactivity takes 50–60 seconds to respond.
- **Root Cause**: Render spins down free containers when no HTTP traffic arrives.
- **Fix**: Set up an external cron monitor (e.g. Cron-Job.org, UptimeRobot, or GitHub Actions) to ping `GET /health/deep` every 10 minutes.

### 2. Supabase Free Project Pausing
- **Symptom**: Database queries fail with connection refused or DNS failure after 7 days of inactivity.
- **Root Cause**: Supabase automatically pauses inactive free-tier databases.
- **Fix**: The 10-minute ping to `/health/deep` executes a database read, keeping the Supabase project permanently active.

### 3. Pooler Port Mismatch (`5432` vs `6543`)
- **Symptom**: Migration script errors with `prepared statement does not exist` or `unsupported transaction statement`.
- **Root Cause**: Running DDL migrations through transaction pooler port `6543`.
- **Fix**: Always use session pooler port `5432` for `DATABASE_MIGRATE_URL`.

### 4. Application Startup Failure: "database schema is not ready"
- **Symptom**: Container logs print `RuntimeError: database schema is not ready: Schema is behind the code`.
- **Root Cause**: Database is missing migrations or `DATABASE_MIGRATE_URL` was not supplied to auto-migrate.
- **Fix**: Run `python -m core.storage.migrate` with valid DDL credentials.

### 5. CSRF or Origin Header Rejection
- **Symptom**: API returns `403 Forbidden` on `/auth/verify` or POST routes.
- **Root Cause**: Browser `Origin` does not match `AGENT_WALLET_PUBLIC_ORIGIN`.
- **Fix**: Set `AGENT_WALLET_PUBLIC_ORIGIN=https://agent-wallet-6vuy.onrender.com` (matching exact protocol and host).

### 6. Public RPC Rate Limiting
- **Symptom**: Transaction simulations or balance checks time out.
- **Root Cause**: Public Base Sepolia endpoint (`https://sepolia.base.org`) rate-limiting requests.
- **Fix**: Set `BASE_SEPOLIA_RPC_URL` to a dedicated Alchemy or Infura testnet endpoint.
