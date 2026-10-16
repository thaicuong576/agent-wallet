# Evidence: Uniswap v3 Swap on Base Sepolia (D2, ticket #139)

Chain: Base Sepolia (84532)  
RPC: `https://sepolia.base.org` (User-Agent: `agent-wallet/0.1`)  
Wallet: `0xc18afDC2dF55772bA3c031Df98305F02a3a36493`  
Done marker: `DONE-D2-UNI4M`

---

## 1. Verified Contract Addresses

| Role | Contract Name | Address | Notes |
| :--- | :--- | :--- | :--- |
| Router | `SwapRouter02` | `0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4` | Target spender for approvals; `exactInputSingle` selector `0x04e45aaf` |
| Quoter | `QuoterV2` | `0xC5290058841028F1614F3A6F0F5816cAd0df5E27` | View quotes via `quoteExactInputSingle` selector `0xc6a5026a` |
| Factory | `UniswapV3Factory` | `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24` | Pool deployer |
| Faucet | `AaveTestnetFaucet` | `0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc` | `mint(address,address,uint256)` selector `0xc6c3bbe6` |
| Oracle | `AaveOracle` | `0x943b0de18d4abf4ef02a85912f8fc07684c141df` | `getAssetPrice(address)` selector `0xb3596f07` (8 dec) |
| Token In | Aave USDC | `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` | 6 decimals |
| Token Out | WETH9 | `0x4200000000000000000000000000000000000006` | 18 decimals, fee tier 3000 |
| Token Out | WBTC | `0x54114591963CF60EF3aA63bEfD6eC263D98145a4` | 8 decimals, fee tier 500 |

---

## 2. Live Oracle Prices and QuoterV2 Quotes

Recorded on-chain via read-only `eth_call` on 2026-10-16.

### Aave Oracle Reference Prices (8 decimals)
- `AaveOracle.getAssetPrice(WBTC)`: `8215874629283` -> **$82,158.75 USD**
- `AaveOracle.getAssetPrice(WETH)`: `245285421624` -> **$2,452.85 USD**
- USDC: Pegged at **$1.0000 USD** (within band)

### QuoterV2 Quotes
- **10 USDC -> WBTC (fee tier 500)**:
  - Input: `10_000_000` (10 USDC)
  - Quoted output: `12,023` satoshis (`0.00012023` WBTC)
  - Implied pool price: ~83,174 USD / BTC (~1.2% deviation from oracle $82,158)
  - Gas estimate: `80,967`
- **10 USDC -> WETH (fee tier 3000)**:
  - Input: `10_000_000` (10 USDC)
  - Quoted output: `63,464,951,254,723,602` wei (`0.06346` WETH)
  - Implied pool price: ~157 USD / WETH (extremely mispriced; oracle fair value is `0.00407` WETH, ~2,452 USD / WETH)
  - Gas estimate: `80,243`
- **0.01 WETH -> USDC (fee tier 3000)**:
  - Input: `10_000_000_000_000_000` wei (0.01 WETH)
  - Quoted output: `1,303,964` units (**1.30 USDC**)
  - Fair value: **$24.53 USDC** (pool provides 94.7% worse rate than oracle fair value)

---

## 3. Simulation: Successful USDC -> WBTC Swap Bundle (`eth_simulateV1`)

Executed against Base Sepolia RPC via `eth_simulateV1` with state override (1 ETH for gas):

```json
{
  "method": "eth_simulateV1",
  "params": [
    {
      "blockStateCalls": [
        {
          "stateOverrides": {
            "0xc18afDC2dF55772bA3c031Df98305F02a3a36493": {
              "balance": "0x1000000000000000000"
            }
          },
          "calls": [
            {
              "from": "0xc18afDC2dF55772bA3c031Df98305F02a3a36493",
              "to": "0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc",
              "data": "0xc6c3bbe6000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a364930000000000000000000000000000000000000000000000000000000000989680",
              "value": "0x0"
            },
            {
              "from": "0xc18afDC2dF55772bA3c031Df98305F02a3a36493",
              "to": "0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f",
              "data": "0x095ea7b300000000000000000000000094cc0aac535ccdb3c01d6787d6413c739ae12bc40000000000000000000000000000000000000000000000000000000000989680",
              "value": "0x0"
            },
            {
              "from": "0xc18afDC2dF55772bA3c031Df98305F02a3a36493",
              "to": "0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4",
              "data": "0x04e45aaf000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f00000000000000000000000054114591963cf60ef3aa63befd6ec263d98145a400000000000000000000000000000000000000000000000000000000000001f4000000000000000000000000c18afdc2df55772ba3c031df98305f02a3a3649300000000000000000000000000000000000000000000000000000000009896800000000000000000000000000000000000000000000000000000000000002ee00000000000000000000000000000000000000000000000000000000000000000",
              "value": "0x0"
            }
          ]
        }
      ],
      "traceTransfers": true,
      "validation": false
    }
  ]
}
```

### Result Summary
- **Call 0 (Faucet Mint 10 USDC)**: `status: 0x1`, gas used `53,702`
- **Call 1 (Approve SwapRouter02 for 10 USDC)**: `status: 0x1`, gas used `46,236`
- **Call 2 (SwapRouter02 exactInputSingle)**: `status: 0x1`, gas used `125,081`
  - `amountOut` returned: **`12,023` satoshis** (`0x0000000000000000000000000000000000000000000000000000000000002ef7`)
- **Total Bundle Gas**: `225,019` gas.

---

## 4. Mispriced Pool Protection & Revert Evidence

The testnet pool for USDC/WETH (fee tier 3000) is heavily distorted:
- 1 WETH is traded in the pool at ~130-157 USDC, while the Aave Oracle fair value is **$2,452.85 USD**.

### On-Chain Safety Net Revert
When an order proposes swapping **0.01 WETH -> USDC**:
1. Oracle fair value calculation:
   $$\text{Fair Out} = 0.01 \times 2452.85 = 24.5285\text{ USDC} = 24,528,542\text{ units}$$
   With 100 bps (1%) slippage ceiling, `amountOutMinimum` = **`24,255,000`** units.
2. Pool execution in simulation:
   The pool can only deliver `1,303,964` units ($1.30 USDC).
3. Result:
   SwapRouter02 reverts on-chain:
   - `status: 0x0`
   - `gasUsed: 109,981`
   - `error: {"code": 3, "message": "execution reverted: Too little received", "data": "0x08c379a0...546f6f206c6974746c65207265636569766564"}`

### Pre-Trade Pool Price Guard (`check_pool_price_guard`)
The adapter's pre-trade check queries QuoterV2 before trade submission and triggers:
```json
{
  "code": "RISK.POOL_PRICE_DEVIATION",
  "evidence": {
    "token_in": "WETH",
    "token_out": "USDC",
    "amount_in": 10000000000000000,
    "pool_amount_out": 1303964,
    "fair_amount_out": 24528542,
    "deviation_bps": 9468
  }
}
```
Deviation of 9,468 bps (>94%) triggers the guard ceiling (>300 bps / 3%), ensuring no transaction is signed or broadcast.
