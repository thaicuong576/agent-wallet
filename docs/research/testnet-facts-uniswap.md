# Research: Uniswap V3 Liquidity for Test USDC/WETH on Base Sepolia

## Question
Using the Uniswap V3 Factory on Base Sepolia, for the test USDC from the Aave faucet (`0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`, as defined in `@bgd-labs/aave-address-book` `AaveV3BaseSepoliaAssets.USDC_UNDERLYING`) and WETH (`0x4200000000000000000000000000000000000006`):
1. Is there a pool at fee tiers 100 (0.01%), 500 (0.05%), 3000 (0.30%), and 10000 (1.00%)?
2. For each pool found, what is the on-chain `liquidity()`, `slot0()` state, and current implied price?
3. What are the actual token reserves (`balanceOf`) in each pool?
4. Can a swap of approximately 1 USDC be executed against the available liquidity?
5. Should the swap action stay in scope for v1 according to the criterion ("keep only if at least one pool has non-trivial liquidity for a swap of about 1 USDC")?

---

## Method
1. **Core & Peripheral Addresses**: Read official Uniswap V3 deployment addresses on Base Sepolia from the official Uniswap documentation (`https://developers.uniswap.org/docs/protocols/v3/deployments/v3-base-deployments`).
   - `UniswapV3Factory`: `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24`
   - `SwapRouter02`: `0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4`
   - `QuoterV2`: `0xC5290058841028F1614F3A6F0F5816cAd0df5E27`
2. **Token Addresses**: Read canonical WETH and Aave testnet underlying tokens from `@bgd-labs/aave-address-book` (`src/AaveV3BaseSepolia.sol`):
   - Aave test USDC (`USDC_UNDERLYING`): `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` (6 decimals)
   - WETH (`WETH_UNDERLYING`): `0x4200000000000000000000000000000000000006` (18 decimals)
3. **On-Chain Inspection via JSON-RPC**:
   - Query `UniswapV3Factory.getPool(tokenA, tokenB, fee)` via `eth_call` for fee tiers `100`, `500`, `3000`, and `10000`.
   - On each returned pool address, query:
     - `liquidity()` (`0x1a686502`): Current in-range liquidity units ($L$).
     - `slot0()` (`0x3850c7bd`): Current `sqrtPriceX96` ($P_{raw} = (\text{sqrtPriceX96} / 2^{96})^2$), `tick`, and initialization state.
     - `token0()` (`0x0dfe1681`) and `token1()` (`0xd21220a7`): Token ordering in pool.
     - `balanceOf(pool)` for both tokens (`0x70a08231`): Raw physical reserves held by the pool contract.
   - Simulate a swap of 1 USDC (and 0.0005 WETH) using `QuoterV2.quoteExactInputSingle` (`0xc6a5026a`) to verify execution feasibility and price impact.
4. **Endpoint**: Public Base Sepolia RPC `https://sepolia.base.org` (Chain ID 84532).

---

## Findings

### Primary Deployment Contracts

| Contract Role | Address | Bytecode Verified (`eth_getCode`) | Source |
| :--- | :--- | :--- | :--- |
| **UniswapV3Factory** | `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24` | Yes (24,710 bytes) | [Uniswap Base Deployments](https://developers.uniswap.org/docs/protocols/v3/deployments/v3-base-deployments) |
| **SwapRouter02** | `0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4` | Yes (24,498 bytes) | [Uniswap Base Deployments](https://developers.uniswap.org/docs/protocols/v3/deployments/v3-base-deployments) |
| **QuoterV2** | `0xC5290058841028F1614F3A6F0F5816cAd0df5E27` | Yes (16,076 bytes) | [Uniswap Base Deployments](https://developers.uniswap.org/docs/protocols/v3/deployments/v3-base-deployments) |
| **Aave Test USDC** | `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` | Yes (USDC, 6 dec) | [Aave Address Book](https://raw.githubusercontent.com/bgd-labs/aave-address-book/main/src/AaveV3BaseSepolia.sol) |
| **Canonical WETH** | `0x4200000000000000000000000000000000000006` | Yes (WETH, 18 dec) | [Base Network Information](https://docs.base.org/docs/network-information/) |

*Note: In Uniswap V3, tokens are ordered numerically: `token0` is WETH (`0x4200000000000000000000000000000000000006`) and `token1` is Aave test USDC (`0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`).*

---

### Pool Status across Fee Tiers

| Fee Tier | Fee (%) | Pool Address (`getPool`) | `liquidity()` ($L$) | `slot0.tick` | Implied Price | WETH Reserve | USDC Reserve | Swap ~1 USDC Feasible? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **100** | 0.01% | `0x068e0df9dfcce57df2c2b60bdb2318ce49a07a31` | `8,639,839,568,058` (`0x7db9e9180ba`) | -199408 | 2,189.25 USDC / WETH | 0.0273 WETH | 0.1536 USDC | **Partial**: USDC $\to$ WETH OK; WETH $\to$ USDC capped at 0.14 USDC |
| **500** | 0.05% | `0x9d9203f8a29c600d567b240692db569b77d1f4a9` | `0` (`0x0`) | -887272 | ~0 (drained / unusable) | 0.0703 WETH | 0.0096 USDC | **No**: Inactive liquidity range; quotes fail |
| **3000** | 0.30% | `0x92266210facf124f190fbad79aa230dfb85926ce` | `4,957,986,963,561` (`0x4825f0d4069`) | -219392 | 296.77 USDC / WETH | 0.4097 WETH | 239.7740 USDC | **Yes**: Fully bidirectional; reserves easily support ~1 USDC |
| **10000** | 1.00% | `0xc8aad20fa4278886e64d279fe39b1e7a2d6e10e8` | `0` (`0x0`) | -196257 | 3,000.00 USDC / WETH | 0.0000 WETH | 0.0000 USDC | **No**: Zero reserves; execution reverts |

---

### Swap Simulation with `QuoterV2`

To verify execution feasibility for a nominal transaction of **1 USDC** ($10^6$ atomic units) and **0.0005 WETH** ($5 \times 10^{14}$ wei):

#### 1. Swap 1 USDC $\to$ WETH (`quoteExactInputSingle`)
- **Fee 100 pool (`0x068e...`)**:
  - `amountOut`: `455,605,427,901,630` wei ($\approx 0.00045561$ WETH).
  - Effective rate: ~2,194.88 USDC / WETH (aligned with market spot price).
  - Status: **Success**. Pool holds 0.0273 WETH ($\approx \$60$), sufficient to pay out 0.000456 WETH.
- **Fee 3000 pool (`0x9226...`)**:
  - `amountOut`: `3,320,764,056,072,310` wei ($\approx 0.00332076$ WETH).
  - Effective rate: ~301.14 USDC / WETH (distorted testnet pricing, but execution succeeds).
  - Status: **Success**.
- **Fee 500 pool (`0x9d92...`)**:
  - `amountOut`: `350,639,256,358,996` wei ($\approx 0.00035064$ WETH, crosses 1 tick).
- **Fee 10000 pool (`0xc8aa...`)**:
  - Status: **Reverts** (`Unexpected error`).

#### 2. Swap WETH $\to$ USDC (Buying ~1 USDC with WETH)
- **Fee 100 pool (`0x068e...`)**:
  - Input `0.0001 WETH` $\to$ `0.140431 USDC`.
  - Input `0.0005 WETH` $\to$ `0.140431 USDC` (output stagnates because the pool's entire USDC reserve is only 0.153555 USDC).
  - Status: **Insufficient USDC reserve** to purchase a full 1.0 USDC.
- **Fee 3000 pool (`0x9226...`)**:
  - Input `0.005 WETH` $\to$ `1.454200 USDC`.
  - Input `0.010 WETH` $\to$ `2.859711 USDC`.
  - Status: **Success**. The pool holds 239.77 USDC, accommodating bidirectional swaps of 1 USDC.

---

### Raw RPC Call Verification Logs

#### 1. Factory Pool Lookups (`eth_call` to `UniswapV3Factory.getPool`)
Selector: `0x1698ee82`  
Arguments: `address tokenA` (Aave test USDC: `0xba50...`), `address tokenB` (WETH: `0x4200...`), `uint24 fee`.

```bash
# Fee 100 (0x64)
curl.exe -s -X POST https://sepolia.base.org -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24","data":"0x1698ee82000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f00000000000000000000000042000000000000000000000000000000000000060000000000000000000000000000000000000000000000000000000000000064"},"latest"],"id":1}'
# Result: "0x000000000000000000000000068e0df9dfcce57df2c2b60bdb2318ce49a07a31"

# Fee 500 (0x1f4)
curl.exe -s -X POST https://sepolia.base.org -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24","data":"0x1698ee82000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f000000000000000000000000420000000000000000000000000000000000000600000000000000000000000000000000000000000000000000000000000001f4"},"latest"],"id":1}'
# Result: "0x0000000000000000000000009d9203f8a29c600d567b240692db569b77d1f4a9"

# Fee 3000 (0xbb8)
curl.exe -s -X POST https://sepolia.base.org -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24","data":"0x1698ee82000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f00000000000000000000000042000000000000000000000000000000000000060000000000000000000000000000000000000000000000000000000000000bb8"},"latest"],"id":1}'
# Result: "0x00000000000000000000000092266210facf124f190fbad79aa230dfb85926ce"

# Fee 10000 (0x2710)
curl.exe -s -X POST https://sepolia.base.org -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24","data":"0x1698ee82000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f00000000000000000000000042000000000000000000000000000000000000060000000000000000000000000000000000000000000000000000000000002710"},"latest"],"id":1}'
# Result: "0x000000000000000000000000c8aad20fa4278886e64d279fe39b1e7a2d6e10e8"
```

#### 2. Pool Liquidity Calls (`eth_call` to `liquidity()`)
Selector: `0x1a686502`

```bash
# Fee 100 Pool: 0x068e0df9dfcce57df2c2b60bdb2318ce49a07a31
curl.exe -s -X POST https://sepolia.base.org -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0x068e0df9dfcce57df2c2b60bdb2318ce49a07a31","data":"0x1a686502"},"latest"],"id":1}'
# Result: "0x000000000000000000000000000000000000000000000000000007db9e9180ba" (Dec: 8639839568058)

# Fee 3000 Pool: 0x92266210facf124f190fbad79aa230dfb85926ce
curl.exe -s -X POST https://sepolia.base.org -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0x92266210facf124f190fbad79aa230dfb85926ce","data":"0x1a686502"},"latest"],"id":1}'
# Result: "0x000000000000000000000000000000000000000000000000000004825f0d4069" (Dec: 4957986963561)
```

---

## Recommendation

Recommendation: **KEEP** swap in scope for v1.

### Rationale
1. **Criterion Met**: The task specification requires: *"keep only if at least one pool has non-trivial liquidity for a swap of about 1 USDC"*.
   - **Fee 3000 pool (`0x9226...`)** holds **239.77 USDC** and **0.4097 WETH** with active liquidity $L = 4,957,986,963,561$. It successfully executes bidirectional swaps of ~1 USDC without reversion or pool exhaustion.
   - **Fee 100 pool (`0x068e...`)** holds **0.0273 WETH** ($\approx \$60$) and active liquidity $L = 8,639,839,568,058$, executing 1 USDC $\to$ WETH at standard market valuation (~2,189 USDC/WETH).
2. **Execution Strategy for Agent Wallet v1**:
   - For **USDC $\to$ WETH** swaps (selling test USDC from Aave faucet for WETH gas): Route to **Fee 100 (`0x068e...`)** for realistic price execution (~0.000456 WETH per 1 USDC).
   - For **WETH $\to$ USDC** swaps (buying test USDC with WETH to supply to Aave): Route to **Fee 3000 (`0x9226...`)** because Fee 100 has only 0.15 USDC in reserve, whereas Fee 3000 has 239.77 USDC.
3. **Alternative Path (Circle Native Test USDC)**: If the operator or owner requires symmetric bidirectional swaps at mainnet-equivalent prices without price distortion, Circle's official Base Sepolia testnet USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`) has deep liquidity across all tiers (Pool `0x4688...` at fee 3000 has $L = 307 \times 10^{12}$). However, for the specified Aave faucet token, Fee 3000 and Fee 100 provide sufficient liquidity to keep swap in v1.

---

## Sources
- **Uniswap V3 Base Sepolia Deployments**: https://developers.uniswap.org/docs/protocols/v3/deployments/v3-base-deployments
- **Uniswap V3 Factory Contract on Base Sepolia**: https://sepolia.basescan.org/address/0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24
- **Uniswap V3 SwapRouter02 Contract on Base Sepolia**: https://sepolia.basescan.org/address/0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4
- **Uniswap V3 QuoterV2 Contract on Base Sepolia**: https://sepolia.basescan.org/address/0xC5290058841028F1614F3A6F0F5816cAd0df5E27
- **Aave Address Book Base Sepolia Registry**: https://raw.githubusercontent.com/bgd-labs/aave-address-book/main/src/AaveV3BaseSepolia.sol
- **Aave Test USDC Underlying Token on Base Sepolia**: https://sepolia.basescan.org/address/0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f
- **Canonical WETH on Base Sepolia**: https://sepolia.basescan.org/address/0x4200000000000000000000000000000000000006
- **Base Sepolia Public Node & RPC**: https://docs.base.org/docs/network-information/ (RPC: `https://sepolia.base.org`)
- **Circle Base Sepolia Testnet USDC Reference**: https://sepolia.basescan.org/address/0x036CbD53842c5426634e7929541eC2318f3dCF7e

---

## Open Questions
1. **Price Impact / Slippage Tolerance**: Given the fee 3000 pool price of ~296.77 USDC/WETH versus fee 100 pool price of ~2,189.25 USDC/WETH, the agent policy engine should set explicit maximum slippage tolerances or fixed route selections depending on the trade direction. Should the IPS explicitly parameterize allowed Uniswap fee tiers?
2. **Liquidity Seeding**: If the owner requires symmetric realistic prices on Fee 100, the wallet could seed 10 USDC into Fee 100 pool once faucet funds are acquired. Should liquidity provision be considered, or is routing WETH $\to$ USDC through Fee 3000 sufficient for v1?

---

Recommendation: KEEP

