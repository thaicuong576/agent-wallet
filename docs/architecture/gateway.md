# Gateway architecture note

Owner module: `core.gateway`.

The gateway is the single execution pipeline and boundary controller for the agent wallet. It validates incoming proposed orders, queries the policy engine, runs pre-flight EVM simulations, invokes the signer, and commits append-only cryptographic decision records to the audit sink.

Per system security invariants, only `core.gateway` is permitted to import `core.signer`.

## Where the gateway sits

```mermaid
flowchart LR
  API[api: FastAPI / Planner] -->|Order| GW[core.gateway.Gateway]
  GW -->|1. evaluate order| PE[core.policy: Policy]
  PE -->|Decision: allow/deny/escalate| GW
  GW -->|2. if allow: simulate calls| SIM[core.simulate.Simulator]
  SIM -->|SimulationResult| GW
  GW -->|3. if sim ok: sign_and_send| SG[core.signer.Signer]
  SG -->|TxReceiptStub| GW
  GW -->|4. append DecisionRecord| AUDIT[(core.gateway.InMemoryAuditSink)]
```

## Pipeline sequence diagram

```mermaid
sequenceDiagram
  autonumber
  participant Client as Caller / API
  participant Gateway as core.gateway.Gateway
  participant Policy as Policy Engine (core.policy)
  participant Sim as Simulator (core.simulate)
  participant Signer as Signer (core.signer)
  participant Audit as AuditSink (InMemoryAuditSink)

  Client->>Gateway: propose(order)
  Note over Gateway: Step 1: Validate against order.schema.json
  alt Invalid order schema
    Gateway-->>Client: raise InvalidOrder (nothing signed, no record)
  end

  Note over Gateway: Step 2: Idempotency check
  alt Order ID repeat (same body)
    Gateway-->>Client: return stored DecisionRecord
  else Order ID conflict (different body)
    Gateway-->>Client: raise OrderIdConflict
  end

  Gateway->>Policy: evaluate(order)
  Policy-->>Gateway: Decision (allow | deny | escalate)
  Note over Gateway: Validate against decision.schema.json

  alt Decision is deny
    Note over Gateway: outcome = "denied" (signer & simulator never called)
  else Decision is escalate
    Note over Gateway: outcome = "pending_approval" (nothing signed)
  else Decision is allow
    loop For each transaction call (via tx_builder)
      Gateway->>Sim: simulate(call)
      Sim-->>Gateway: SimulationResult(ok, error, gas_used)
    end
    alt Simulation failed
      Note over Gateway: outcome = "failed" (SIMULATION_FAILED, signer never called)
    else Simulation succeeded
      loop For each transaction call
        Gateway->>Signer: sign_and_send(call)
        Signer-->>Gateway: TxReceiptStub(tx_hash)
      end
      alt Signer exception
        Note over Gateway: outcome = "failed" (SIGNER_UNAVAILABLE / TX_REVERTED, key redacted)
      else All signed successfully
        Note over Gateway: outcome = "executed" (ExecutionResult with last tx_hash)
      end
    end
  end

  Gateway->>Audit: append(record)
  Audit-->>Gateway: DecisionRecord (seq, prev_hash, hash assigned)
  Note over Gateway: Validate against decision-record.schema.json
  Gateway-->>Client: DecisionRecord
```

## Data structure and ERD

The returned `DecisionRecord` links the proposed `Order`, the policy `Decision`, and the optional `ExecutionResult` within an immutable cryptographic chain:

```mermaid
erDiagram
  DecisionRecord ||--|| Order : "embeds order"
  DecisionRecord ||--|| Decision : "embeds decision"
  DecisionRecord ||--o| ExecutionResult : "embeds execution (on allow)"
  DecisionRecord ||--o| Approval : "embeds approval (if escalated)"

  DecisionRecord {
    int schema_version
    int seq
    string order_id
    string outcome
    string tier
    boolean emergency_stop
    string ips_hash
    string created_at
    string prev_hash
    string hash
  }

  Order {
    int schema_version
    string order_id
    string proposed_by
    string protocol
    string function
    string token
    PositiveAmount amount
    string recipient
  }

  Decision {
    int schema_version
    string order_id
    string decision
    RuleHit[] rules_hit
    string reason
    string ips_hash
    string decided_at
  }

  ExecutionResult {
    int schema_version
    string order_id
    string status
    string tx_hash
    int chain_id
    int gas_used
    string error_code
    string detail
    string executed_at
  }
```

## Security and design guarantees

1. **Default deny**: If no policy is provided, `DenyAllPolicy` is enforced, denying all proposed orders with `ACCESS.TOKEN_NOT_ALLOWED`. The simulator and signer are never called.
2. **Pre-flight simulate-before-sign**: On allowed orders, all transaction calls are simulated with `Simulator` before any signing occurs. If simulation fails, the transaction is rejected with outcome `failed` (`SIMULATION_FAILED`) and the signer is never called.
3. **Idempotency from the audit log**: Every `order_id` is looked up in the audit log (`find_by_order_id`, latest record) under the gateway lock. A repeat with the exact same body returns the stored `DecisionRecord` without re-evaluating or re-signing, also after a process restart. A different body for a known `order_id` raises `OrderIdConflict`. A `failed` record is terminal for that `order_id`.
4. **Append-only cryptographic hash chain**: Audit records are chained using canonical JSON serialization and SHA-256 (`hash = sha256(canonical(record - {hash}))`). The first record points to `0` * 64, and each subsequent record links to `prev_hash`.
5. **Zero key exposure**: Error messages and execution details are sanitized to redact any private keys or raw exception data.
6. **Signer isolation**: Enforced by `tests/test_signer_boundary.py` and CI: only `core.gateway` may import `core.signer`.
7. **Gateway lock**: `GatewayLock` (a SQLite `BEGIN IMMEDIATE` on `gateway.lock.sqlite` next to the audit file) is acquired at the start of `propose`, before limits are read, and held through the audit append. Two processes cannot both pass a limit. On timeout `GatewayBusy` is raised and nothing is signed or recorded (fail closed).
8. **Order intents**: Before the signer is called, `IntentStore.begin_signing` inserts a row into `order_intents` (insert-only, UPDATE and DELETE are blocked by triggers). If the process dies between signing and the audit append, the next `propose` of that `order_id` finds an intent without an `executed` or `failed` record and raises `ReconcileRequired`; the signer is not called again and no synthetic record is written. Only `core.gateway` may import `core.gateway.intents` (`tests/test_intent_boundary.py`).
9. **`record_no_action`**: lets the runner record a `no_action` decision (no order, no `order_id`) through the gateway, so the audit sink stays written only by the gateway.

```mermaid
sequenceDiagram
    participant R as Runner
    participant G as Gateway
    participant L as GatewayLock
    participant A as Audit log
    participant I as order_intents
    participant S as Signer
    R->>G: propose(order)
    G->>L: held() (timeout -> GatewayBusy)
    G->>A: find_by_order_id
    alt record exists
        G-->>R: stored record (or OrderIdConflict)
    else intent without record
        G-->>R: ReconcileRequired
    else new order
        G->>G: policy (limits from audit log), simulate
        G->>I: begin_signing
        G->>S: sign_and_send
        G->>A: append
        G-->>R: record
    end
```
