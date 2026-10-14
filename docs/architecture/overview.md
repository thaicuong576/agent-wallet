# Architecture overview

One request path, one signer, one audit log. The LLM proposes; code decides.

```mermaid
flowchart LR
  UI[Minimal UI] -->|OpenAPI| API[api: FastAPI]
  EV[eval: harness] -->|OpenAPI| API
  API --> GW[core.gateway]
  GW -->|order, IPS, state| PE[core.policy: pure engine]
  PE -->|decision| GW
  GW --> SIM[simulate]
  SIM -->|only if allowed| SG[core.signer]
  GW --> LOG[(core.audit: hash chain)]
  GW --> AAVE[core.protocols: Aave V3]
  C[contracts/: schemas, OpenAPI, rule codes] -. defines .-> API
  C -. defines .-> PE
  C -. defines .-> LOG
```

## Boundaries (each has a test)
| Rule | Test |
| :--- | :--- |
| Only `core.gateway` imports `core.signer` | `tests/test_signer_boundary.py` |
| Every contract example validates against its schema | `tests/test_contracts.py` |
| No secret is tracked | `scripts/check_no_secrets.py` (run by `make check` and CI) |

## Packages
| Package | Responsibility | Status |
| :--- | :--- | :--- |
| `api` | HTTP surface, matches `contracts/openapi.yaml` | `/health` only |
| `core.gateway` | propose, policy, simulate, sign, record | placeholder |
| `core.signer` | holds the key, signs | placeholder |
| `core.policy` | allow, deny, escalate | placeholder |
| `core.audit` | append-only hash chain | placeholder |
| `core.protocols` | calldata builders | placeholder |
