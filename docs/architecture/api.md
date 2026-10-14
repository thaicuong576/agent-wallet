# Architecture: REST API backend

Owner: `api` (`api/app.py`, `api/main.py`). The HTTP interface implementing the single API contract between the minimal UI, the eval harness, and the backend as specified in `contracts/openapi.yaml`.

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

1. **Fail-closed owner authentication**: All owner endpoints (`/emergency-stop*`, `/orders/{id}/approve|reject`, `PUT /tier`) require an owner token via HTTP `Authorization: Bearer <token>` or `X-Owner-Token`. The token is compared against `AGENT_WALLET_OWNER_TOKEN` in constant time (`hmac.compare_digest`). If `AGENT_WALLET_OWNER_TOKEN` is unset in the environment, all owner endpoints immediately return HTTP 503 (`urn:agent-wallet:problem:service-unavailable`).
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
| `/agent/mint`, `/runs` | `POST` | session | `api/jobs.py` |
| `/agent/withdraw` | `POST` | session plus step-up `withdraw` | `api/jobs.py` |
| `/jobs`, `/jobs/{job_id}` | `GET` | session | `api/jobs.py` |

Rules enforced here: the owner address comes only from `require_session` (no route has an owner parameter or body field; extra body fields are `422`); a foreign `job_id` or `tx_hash` is `404`; problems use the 12.1 slugs (`job-in-progress` 409 with the active `job_id`, `rate-limited` 429 with `Retry-After`, `custody-unavailable` 503); `origin="owner"` appears only in `api/jobs.py`.

| Claim | Proving check |
| :--- | :--- |
| App routes and methods equal `contracts/openapi.yaml` | `pytest tests/api/test_routes.py -q` (reads the app's generated schema, since included routers hide their routes from `app.routes`) |
| Session routes reject anonymous callers; withdraw needs a bound step-up | `pytest tests/webapp/test_routes.py -q` |
| A session for A cannot read or act on B | `pytest tests/webapp/test_routes.py -k "session_a or foreign or owner_query or chain_verifies" -q` |
