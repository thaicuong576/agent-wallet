# Base Sepolia Simulation Method Research

## Question
Which of the following transaction simulation methods work reliably on public Base Sepolia RPC endpoints: `eth_simulateV1`, `eth_call` with state overrides, `eth_estimateGas`, and `debug_traceCall`? What is the recommended method for a pre-sign transaction simulator that must catch reverts and unexpected balance changes, and what is the fallback if public RPC methods fail or become throttled?

## Method
Tests were conducted against two primary public Base Sepolia RPC endpoints (and one secondary endpoint) on Chain ID 84532 (`0x14a34`):
1. **Endpoint A (Official Base Public RPC)**: `https://sepolia.base.org`
2. **Endpoint B (PublicNode / Allnodes)**: `https://base-sepolia-rpc.publicnode.com`
3. **Endpoint C (dRPC Decentralized Public RPC)**: `https://base-sepolia.drpc.org`

For each simulation method, minimal valid JSON-RPC requests were sent via `curl` without private keys:
- Chain ID verification via `eth_chainId`.
- Plain native ETH transfer simulation between arbitrary addresses (`from: 0x1111111111111111111111111111111111111111`, `to: 0x2222222222222222222222222222222222222222`, `value: 0x1`).
- Reverting execution tests against Base Sepolia Canonical WETH9 (`0x4200000000000000000000000000000000000006`, calling `withdraw(1 ether)` without balance) and insufficient gas/funds tests.
- State overrides verification (injecting ephemeral balance on zero-balance addresses).
- Native transfer tracing verification using `traceTransfers: true`.
- Debug tracing tests with `callTracer`.

Raw JSON-RPC responses, status codes, and error payloads were recorded directly from execution.

## Findings

### Summary Table

| Method | `sepolia.base.org` | `base-sepolia-rpc.publicnode.com` | Catches Reverts? | Detects Balance Changes? | Public Production Suitability |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`eth_simulateV1`** | **Supported** (HTTP 200, status `0x1`/`0x0`) | **Supported** (HTTP 200, status `0x1`/`0x0`) | **Yes**: explicitly returns `status: "0x0"` and `error.message: "execution reverted"` | **Yes**: with `traceTransfers: true`, emits synthetic `Transfer` logs (`0xeeee...eeee`) for ETH; standard ERC-20 `Transfer` logs appear in `logs` | **Primary Recommendation** |
| **`eth_call` (with state overrides)** | **Supported** (HTTP 200, state overrides applied) | **Supported** (HTTP 200, state overrides applied) | **Yes**: returns EVM revert error (`code: -32003` or execution revert code) | **Indirectly**: returns raw execution output bytes only; requires state diff inspector contract or post-call balance queries | **Secondary Remote Fallback** |
| **`eth_estimateGas`** | **Supported** (HTTP 200, gas estimation returned) | **Supported** (HTTP 200, gas estimation returned) | **Yes**: returns `code: 3`, `execution reverted` on failure | **No**: returns only gas quantity (`0x5208`) or error; no logs, trace, or return data | **Insufficient alone for balance checks** |
| **`debug_traceCall`** | **Blocked** (`code: -32611`, filter settings) | **Not Available** (`code: -32601`, method does not exist) | N/A (Endpoint blocked) | N/A (Endpoint blocked) | **Unsupported on public nodes** (Available only via local Anvil/fork) |

---

### Raw JSON-RPC Response Excerpts

#### 1. Chain Verification (`eth_chainId`)
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  https://sepolia.base.org
```
Response:
```json
{"jsonrpc":"2.0","id":1,"result":"0x14a34"}
```
*(Decimal: 84532 - Base Sepolia)*

---

#### 2. `eth_simulateV1`

##### Test A: Successful Native Transfer with `traceTransfers: true`
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_simulateV1","params":[{"blockStateCalls":[{"calls":[{"from":"0x1111111111111111111111111111111111111111","to":"0x2222222222222222222222222222222222222222","value":"0x1"}]}],"traceTransfers":true}],"id":1}' \
  https://sepolia.base.org
```
Response excerpt:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {
      "gasUsed": "0x5208",
      "status": "0x1",
      "calls": [
        {
          "returnData": "0x",
          "logs": [
            {
              "address": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
              "topics": [
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                "0x0000000000000000000000001111111111111111111111111111111111111111",
                "0x0000000000000000000000002222222222222222222222222222222222222222"
              ],
              "data": "0x0000000000000000000000000000000000000000000000000000000000000001"
            }
          ],
          "gasUsed": "0x5208",
          "maxUsedGas": "0x5208",
          "status": "0x1"
        }
      ]
    }
  ]
}
```
*Note: PublicNode (`https://base-sepolia-rpc.publicnode.com`) returned identical payload format with matching `status: "0x1"` and `0xeeee...eeee` transfer log.*

##### Test B: Revert Detection (WETH9 `withdraw` without balance)
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_simulateV1","params":[{"blockStateCalls":[{"calls":[{"from":"0x1111111111111111111111111111111111111111","to":"0x4200000000000000000000000000000000000006","data":"0x2e1a7d4d0000000000000000000000000000000000000000000000000de0b6b3a7640000"}]}]}],"id":1}' \
  https://sepolia.base.org
```
Response excerpt:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {
      "calls": [
        {
          "returnData": "0x",
          "logs": [],
          "gasUsed": "0x5c9b",
          "maxUsedGas": "0x5c9b",
          "status": "0x0",
          "error": {
            "code": 3,
            "message": "execution reverted",
            "data": "0x"
          }
        }
      ]
    }
  ]
}
```

##### Test C: State Overrides in `eth_simulateV1`
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_simulateV1","params":[{"blockStateCalls":[{"stateOverrides":{"0x1111111111111111111111111111111111111111":{"balance":"0x1000000000000000000"}},"calls":[{"from":"0x1111111111111111111111111111111111111111","to":"0x2222222222222222222222222222222222222222","value":"0x100000000000000"}]}]}],"id":1}' \
  https://sepolia.base.org
```
Response excerpt:
```json
{"jsonrpc":"2.0","id":1,"result":[{"gasUsed":"0x5208","calls":[{"returnData":"0x","logs":[],"gasUsed":"0x5208","maxUsedGas":"0x5208","status":"0x1"}]}]}
```

---

#### 3. `eth_call` with State Overrides

##### Test A: OutOfFunds without state override
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"from":"0x000000000000000000000000000000000000dEaD","to":"0x2222222222222222222222222222222222222222","value":"0x1000000000000000000"},"latest"],"id":1}' \
  https://sepolia.base.org
```
Response:
```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32003,"message":"EVM error: OutOfFunds"}}
```

##### Test B: Successful call with balance state override
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"from":"0x000000000000000000000000000000000000dEaD","to":"0x2222222222222222222222222222222222222222","value":"0x1000000000000000000"},"latest",{"0x000000000000000000000000000000000000dEaD":{"balance":"0x2000000000000000000"}}],"id":1}' \
  https://sepolia.base.org
```
Response:
```json
{"jsonrpc":"2.0","id":1,"result":"0x"}
```
*(Verified identically on `https://base-sepolia-rpc.publicnode.com`)*

---

#### 4. `eth_estimateGas`

##### Test A: Valid call
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_estimateGas","params":[{"from":"0x1111111111111111111111111111111111111111","to":"0x2222222222222222222222222222222222222222","value":"0x1"}],"id":1}' \
  https://sepolia.base.org
```
Response:
```json
{"jsonrpc":"2.0","id":1,"result":"0x5208"}
```

##### Test B: Reverting call
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_estimateGas","params":[{"from":"0x1111111111111111111111111111111111111111","to":"0x4200000000000000000000000000000000000006","data":"0x2e1a7d4d0000000000000000000000000000000000000000000000000de0b6b3a7640000"}],"id":1}' \
  https://sepolia.base.org
```
Response:
```json
{"jsonrpc":"2.0","id":1,"error":{"code":3,"message":"execution reverted"}}
```

---

#### 5. `debug_traceCall`

##### Test on `https://sepolia.base.org`:
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"debug_traceCall","params":[{"from":"0x1111111111111111111111111111111111111111","to":"0x2222222222222222222222222222222222222222","value":"0x1"},"latest",{"tracer":"callTracer"}],"id":1}' \
  https://sepolia.base.org
```
Response:
```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32611,"message":"rejected due to request filter settings"}}
```

##### Test on `https://base-sepolia-rpc.publicnode.com`:
Request:
```bash
curl.exe -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"debug_traceCall","params":[{"from":"0x1111111111111111111111111111111111111111","to":"0x2222222222222222222222222222222222222222","value":"0x1"},"latest",{"tracer":"callTracer"}],"id":1}' \
  https://base-sepolia-rpc.publicnode.com
```
Response:
```json
{"jsonrpc":"2.0","error":{"code":-32601,"message":"the method debug_traceCall does not exist/is not available"},"id":1}
```

---

## Recommendation

### 1. Primary Recommendation: `eth_simulateV1`
For the pre-sign transaction simulator on Base Sepolia, the recommended primary method is **`eth_simulateV1`** with `traceTransfers: true` and `validation: true`.

#### Reasons:
1. **Public RPC Support**: Both official `sepolia.base.org` and alternative `base-sepolia-rpc.publicnode.com` natively support `eth_simulateV1` without authentication keys.
2. **Deterministic Revert Catching**: Reverting transactions do not fail the outer RPC call; instead, `calls[i].status` evaluates to `"0x0"` and the exact revert reason (`error.message`, `error.data`) is returned.
3. **Comprehensive Balance Change Detection**:
   - **Native ETH**: Setting `"traceTransfers": true` generates synthesized ERC-20 `Transfer` event logs from address `0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee`, making native balance shifts inspectable without tracing opcodes.
   - **ERC-20 Tokens**: Standard `Transfer` event logs are emitted in `calls[i].logs` and can be parsed to verify balance deltas against policy limits.
4. **State Overrides & Bundling**: Supports account overrides (balance, nonce, storage, bytecode) and allows simulating multiple sequential transactions (e.g., token approve + swap) in a single simulated block.

---

### 2. Fallbacks

#### Fallback A (Remote Node Fallback): `eth_call` with State Overrides
If an RPC provider disables `eth_simulateV1` or throttles it, fall back to `eth_call` with state overrides:
- **Reverts**: Catches reverts directly via RPC error codes (`-32003` / `-32000` / `3`).
- **Balance Tracking**: Because `eth_call` returns only raw return bytes, balance changes must be captured by injecting an inspector contract bytecode via `stateOverrides[ephemeralAddr].code`. The inspector executes the target call, measures `balanceOf` / `balance` before and after, and reverts with the delta or returns encoded balance diffs.

#### Fallback B (Local Environment Fallback): Local Fork via Anvil
If public RPC endpoints fail, throttle requests, or reject complex bundled transactions:
- **Mechanism**: Launch an Anvil local fork:
  ```bash
  anvil --fork-url https://sepolia.base.org --fork-chain-id 84532
  ```
- **Capability**: Provides full, unrestricted access to `debug_traceCall`, state dumps, instant EVM snapshots/reverts, and zero external rate limits.

---

## Sources
- **Base Network Information**: https://docs.base.org/docs/network-information/
- **Base Node Providers**: https://docs.base.org/docs/tools/node-providers/
- **Ethereum Execution APIs - `eth_simulateV1` Specification**: https://github.com/ethereum/execution-apis/pull/484
- **Ethereum JSON-RPC Specification**: https://ethereum.org/en/developers/docs/apis/json-rpc/
- **Geth `eth_call` State Overrides Reference**: https://geth.ethereum.org/docs/interacting-with-geth/rpc/ns-eth#eth-call
- **Foundry / Anvil Documentation**: https://book.getfoundry.sh/anvil/

---

## Open Questions
1. **Public Rate Limits**: Public endpoints (`sepolia.base.org`, `publicnode.com`) enforce unspecified rate limits per IP. Under heavy load during agent execution cycles, will a dedicated node provider (e.g. Alchemy/QuickNode) or an Anvil sidecar be required?
2. **Untraced Balance Reductions**: `traceTransfers: true` captures direct ETH value transfers and `calls[i].logs` captures ERC-20 `Transfer` events. However, balance decreases via contract destruction (`SELFDESTRUCT`) or rebasing tokens without log emissions cannot be detected without opcode tracing (available only on Anvil/debug nodes). Is standard transfer/log tracing sufficient for the initial IPS scope?
