# Gateway wiring: Policy engine, permission tiers, and approval queue

Owner module: `core.gateway` (`core/gateway/gateway.py`, `core/gateway/policy.py`).

The gateway wires together the pure policy engine (`core.policy`), read-only tier and emergency stop controls (`core.tiers.view.TierView`), and the owner approval queue (`core.tiers.store.TierStore`), executing orders against EVM simulation and `core.signer` with cryptographic audit logging (`core.gateway.audit`).

## Sequence diagram of propose

```mermaid
sequenceDiagram
  autonumber
  participant Client as Caller / Agent / API
  participant Gateway as core.gateway.Gateway
  participant TierView as TierView (core.tiers)
  participant Policy as EnginePolicy (core.policy)
  participant Queue as Approval Queue (core.tiers)
  participant Sim as Simulator (core.simulate)
  participant Signer as Signer (core.signer)
  participant Audit as AuditSink (InMemoryAuditSink)

  Client->>Gateway: propose(order)
  Note over Gateway: 1. Validate order schema (order.schema.json)
  alt Invalid schema
    Gateway-->>Client: raise InvalidOrder (nothing signed or recorded)
  end

  Gateway->>TierView: is_stopped(), current()
  alt Error reading TierView or emergency_stop is active
    Gateway->>Audit: append DecisionRecord (outcome=denied, TIER.EMERGENCY_STOP)
    Audit-->>Gateway: stored record
    Gateway-->>Client: DecisionRecord (denied, fail-closed)
  else Tier is T0 (read-only) or T1 (propose-only)
    Gateway->>Audit: append DecisionRecord (outcome=denied, TIER.READ_ONLY / TIER.PROPOSE_ONLY)
    Audit-->>Gateway: stored record
    Gateway-->>Client: DecisionRecord (denied)
  end

  Gateway->>TierView: approval_status(order_id)
  TierView-->>Gateway: status (None | pending | approved | rejected)

  alt Idempotency: order_id already recorded
    alt Outcome was pending_approval AND status is approved
      Note over Gateway: Proceed to execute approved order
    else Outcome was denied due to EMERGENCY_STOP AND stop is now released
      Note over Gateway: Proceed to re-evaluate order
    else Stored outcome complete or still pending
      Gateway-->>Client: return copy of stored DecisionRecord
    end
  end

  alt approval_status == "rejected"
    Gateway->>Audit: append DecisionRecord (outcome=denied, TIER.APPROVAL_REJECTED)
    Audit-->>Gateway: stored record
    Gateway-->>Client: DecisionRecord (denied, rejected)
  end

  Gateway->>Policy: evaluate(order) [via core.policy.engine]
  Policy-->>Gateway: Decision (allow | deny | escalate)

  alt Decision is deny
    Gateway->>Audit: append DecisionRecord (outcome=denied)
    Audit-->>Gateway: stored record
    Gateway-->>Client: DecisionRecord (denied)
  else Decision is escalate
    alt approval_status == "approved"
      Note over Gateway: Execute approved order (simulate & sign)
    else approval_status is None or "pending"
      alt approval_status is None
        Gateway->>Queue: request_approval(order_id)
      end
      Gateway->>Audit: append DecisionRecord (outcome=pending_approval)
      Audit-->>Gateway: stored record
      Gateway-->>Client: DecisionRecord (pending_approval)
    end
  end

  alt Execution path (allow OR approved escalation)
    Gateway->>Sim: simulate(calls) / simulate_bundle(calls)
    alt Simulation failed
      Gateway->>Audit: append DecisionRecord (outcome=failed, SIMULATION_FAILED)
      Audit-->>Gateway: stored record
      Gateway-->>Client: DecisionRecord (failed)
    else Simulation OK
      loop For each call in calls
        Gateway->>Signer: sign_and_send(call)
        Signer-->>Gateway: TxReceiptStub(tx_hash)
      end
      alt Signing failed / reverted
        Gateway->>Audit: append DecisionRecord (outcome=failed, SIGNER_UNAVAILABLE / TX_REVERTED)
        Audit-->>Gateway: stored record
        Gateway-->>Client: DecisionRecord (failed)
      else All signed successfully
        Gateway->>Audit: append DecisionRecord (outcome=executed, last tx_hash)
        Audit-->>Gateway: stored record (hash chained)
        Gateway-->>Client: DecisionRecord (executed)
      end
    end
  end
```

## Architectural guarantees and invariants

1. **EnginePolicy adapter**:
   `EnginePolicy` implements the `Policy` protocol by calling the pure `core.policy.engine.evaluate` with the `LoadedIps`, a portfolio state callable, and an optional risk inputs callable. If the policy engine allows the order, `EnginePolicy` verifies `approval.escalate_above_usd`; if the order USD value exceeds this threshold, it yields an `escalate` decision with rule code `TIER.APPROVAL_REQUIRED`.

2. **Fail-closed tier inspection**:
   `Gateway` reads tier permission (`T0`, `T1`, `T2`) and emergency stop dynamically per `propose` call through a read-only `TierView`. Any failure reading tier state immediately triggers a fail-closed denial with `TIER.EMERGENCY_STOP`. Emergency stop denies all executions, even previously approved orders.

3. **Strict structural isolation**:
   No module in `core/gateway/` imports `core.tiers.owner` or `OwnerControls`. The gateway enqueues approval requests strictly through an injected callable `request_approval(order_id)`. Mutating controls remain exclusive to the owner API surface.

4. **Approval lifecycle and idempotency**:
   - An escalated order produces outcome `pending_approval` and enqueues in SQLite.
   - Re-proposing while pending returns the stored `pending_approval` record without signing or simulating.
   - An owner-approved order re-proposed under Tier T2 executes against the simulator and signer.
   - An owner-rejected order re-proposed is denied with `TIER.APPROVAL_REJECTED` and never executes.
   - After executing, repeat proposals return the stored record; the same `order_id` never executes twice.
