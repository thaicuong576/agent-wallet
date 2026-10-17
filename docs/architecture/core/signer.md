# Signer architecture note

Owner modules: `core.signer` and `core.simulate`.

The signer subsystem provides key management, transaction signing, and dry-run simulation for the agent wallet. Per system invariants, only `core.gateway` may import `core.signer`.

## Where the signer sits

```mermaid
flowchart LR
  GW[core.gateway] -->|sign_and_send| GS[GuardedSigner]
  GS -->|1. simulate| SIM[RpcSimulator<br/>core.simulate]
  SIM -->|eth_simulateV1 / eth_call| RPC[(Base Sepolia RPC)]
  GS -->|2. if ok: sign_and_send| LS[LocalSigner<br/>core.signer]
  LS -->|eth_sendRawTransaction| RPC
  LS -. reads env .-> KEY([AGENT_WALLET_PRIVATE_KEY])
```

## Sequence and boundary flow

```mermaid
sequenceDiagram
  autonumber
  participant Gateway as core.gateway
  participant Guarded as GuardedSigner
  participant Simulator as RpcSimulator (core.simulate)
  participant RPC as Base Sepolia RPC
  participant Signer as LocalSigner (core.signer)

  Gateway->>Guarded: sign_and_send(tx)
  Guarded->>Simulator: simulate(tx)
  Simulator->>RPC: eth_simulateV1 (blockStateCalls, validation=true)
  alt Method not supported (-32601)
    Simulator->>RPC: eth_call (fallback)
  end
  RPC-->>Simulator: simulation output
  Simulator-->>Guarded: SimulationResult(ok, error, gas_used)

  alt Simulation failed (ok=false)
    Guarded-->>Gateway: raise SimulationFailed (never calls signer)
  else Simulation succeeded (ok=true)
    Guarded->>Signer: sign_and_send(tx)
    Signer->>RPC: send raw transaction
    RPC-->>Signer: tx_hash
    Signer-->>Guarded: TxReceiptStub(tx_hash)
    Guarded-->>Gateway: TxReceiptStub(tx_hash)
  end
```

## Security and design guarantees

1. **Simulate-before-sign**: `GuardedSigner` wraps any `Signer` with a `Simulator`. If simulation fails (e.g. EVM revert or out of funds), `SimulationFailed` is raised immediately, and the inner signer is never invoked.
2. **Zero key exposure**: `LocalSigner` reads `AGENT_WALLET_PRIVATE_KEY` only at construction using `eth_account`. Its `__repr__`, `__str__`, exception messages, and logs never contain the private key or any sensitive key material. Missing or malformed keys raise `SignerKeyError` without echoing the invalid value.
3. **Simulation isolation**: `core.simulate` is an independent package that never imports `core.signer` or handles private keys. Tests use an injectable transport to execute deterministically without network access.
4. **RPC fallback strategy**: Follows the findings in `docs/research/testnet-facts-rpc.md`: uses `eth_simulateV1` with `blockStateCalls` and `validation: true` as the primary method, falling back to `eth_call` if `eth_simulateV1` returns method-not-found (`-32601`).
5. **Architectural boundary**: Enforced by `tests/test_signer_boundary.py` and CI: only `core.gateway` may import `core.signer`.
