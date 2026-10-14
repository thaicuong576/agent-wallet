# Architecture overview

One request path, one signer, one audit log. The LLM proposes; code decides.

```mermaid
flowchart LR
  UI[Minimal UI] -->|OpenAPI| API[api: FastAPI]
  EV[eval: harness] -->|OpenAPI| API
  API --> GW[core.gateway: Gateway]
  TV[(core.tiers: TierView)] -->|tier, stop, approval_status| GW
  GW -->|request_approval| TS[(core.tiers: approval_queue)]
  OC[OwnerControls] -->|set_tier, stop, approve, reject| TS
  GW -->|order, state, risk_inputs| EP[core.gateway: EnginePolicy]
  EP -->|evaluate| PE[core.policy: pure engine]
  PE -->|decision| EP
  EP -->|decision: allow, deny, escalate| GW
  GW --> SIM[simulate]
  SIM -->|only if allowed| SG[core.signer]
  GW --> LOG[(core.audit: hash chain)]
  GW --> AAVE[core.protocols: Aave V3]
  C[contracts/: schemas, OpenAPI, rule codes] -. defines .-> API
  C -. defines .-> PE
  C -. defines .-> LOG
  C -. defines .-> TS
```

## Boundaries (each has a test)
| Rule | Test |
| :--- | :--- |
| Only `core.gateway` imports `core.signer` | `tests/test_signer_boundary.py` |
| Agent-facing modules never import `core.tiers.owner` | `tests/tiers/test_tiers.py` |
| Every contract example validates against its schema | `tests/test_contracts.py` |
| No secret is tracked | `scripts/check_no_secrets.py` (run by `make check` and CI) |

## Packages
| Package | Responsibility | Status |
| :--- | :--- | :--- |
| `api` | HTTP surface, matches `contracts/openapi.yaml` | `/health` only |
| `core.gateway` | propose, policy, simulate, sign, record | wired to policy & tiers |
| `core.signer` | holds the key, signs | implemented |
| `core.policy` | pure engine (allow, deny), EnginePolicy adapter | implemented |
| `core.tiers` | permission tiers T0-T2, emergency stop, approval queue | implemented |
| `core.audit` | append-only hash chain | implemented |
| `core.protocols` | calldata builders | implemented |
