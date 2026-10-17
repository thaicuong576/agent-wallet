# Data contracts v1: architecture note

Owner: `contracts/` (schemas, OpenAPI, rule codes). Every module builds against it; no module owns a private copy of an object. How to read, validate and change them: [`contracts/README.md`](../../../contracts/README.md).

## Where contracts sit
```mermaid
flowchart LR
  UI[Minimal UI] -->|OpenAPI| API[FastAPI]
  EV[Eval harness] -->|OpenAPI| API
  PL[Planner LLM] -->|Order| API
  API -->|Order| GW[Gateway]
  GW -->|Order, IPS, PortfolioState, RiskInputs| PE[Policy engine]
  PE -->|Decision| GW
  GW -->|allowed Order| SIM[Simulate] --> SG[Signer]
  SG -->|ExecutionResult| GW
  GW -->|DecisionRecord| LOG[(Audit log, hash chain)]
  OWN[Owner] -->|IPS file, TierState, approve, reject| GW
  C[contracts/: schemas, openapi.yaml, rule-codes.json] -. defines .-> API
  C -. defines .-> PE
  C -. defines .-> LOG
```
Only the gateway calls the signer. The agent reads the IPS and has no write path to it, to `TierState` or to approvals.

## Object relations
```mermaid
erDiagram
  IPS ||--o{ DECISION : "evaluated under (ips_hash)"
  IPS ||--o{ DECISION_RECORD : "in force (ips_hash)"
  ORDER ||--o| DECISION : "evaluated by"
  ORDER ||--o{ DECISION_RECORD : "order_id"
  DECISION ||--o{ RULE_HIT : "rules_hit"
  DECISION_RECORD }o--o| DECISION : "decision"
  DECISION_RECORD }o--o| EXECUTION_RESULT : "execution"
  DECISION_RECORD }o--o| APPROVAL : "approval"
  DECISION_RECORD }o--|| TIER_STATE : "tier and stop at write time"
  DECISION_RECORD ||--o| DECISION_RECORD : "prev_hash"
  RULE_HIT }o--|| RULE_CODE : "code"
  ORDER {
    int schema_version
    uuid order_id PK
    string protocol
    string function
    string token
    string amount_value
    int amount_decimals
    string recipient
  }
  DECISION {
    int schema_version
    uuid order_id FK
    string decision "allow, deny, escalate"
    string reason
    sha256 ips_hash FK
  }
  RULE_HIT {
    string code FK
    object evidence "flat key to value"
  }
  RULE_CODE {
    string code PK
    string family
  }
  EXECUTION_RESULT {
    uuid order_id FK
    string status "executed, failed"
    string tx_hash
    int chain_id
    string error_code
  }
  APPROVAL {
    string status "approved, rejected"
    string by "owner"
    timestamp at
  }
  TIER_STATE {
    string tier "T0, T1, T2"
    bool emergency_stop
  }
  IPS {
    int schema_version
    string owner
    object universe
    object allocation
    object risk
    object limits
    object approval
  }
  DECISION_RECORD {
    int schema_version
    int seq PK
    uuid order_id FK "null only for no_action"
    string outcome "no_action, denied, pending_approval, executed, failed"
    sha256 prev_hash
    sha256 hash
  }
```
`PortfolioState` and `RiskInputs` are inputs to the policy engine and are not stored in the record; the record keeps the order, the decision and what happened.

## Which test proves what
| Claim | Proving check |
| :--- | :--- |
| Every schema is valid draft 2020-12 and requires `schema_version` | schema validation check (`check_schema`) |
| Valid examples pass and invalid examples fail | example validation check |
| An order cannot carry an address, a float, or an unlimited amount | `order.invalid.address-recipient`, `.float-amount`, `.unlimited-approval` |
| The hash chain recomputes | chain recomputation check; the audit-log test suite owns the runtime test |
| The IPS has no agent-writable field | `ips.invalid.agent-editable-field` and no write path in `openapi.yaml`; permission tests verify write isolation |
