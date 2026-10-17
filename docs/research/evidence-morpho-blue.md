# Morpho Blue Simulation Evidence (Base Sepolia, 84532)

Evidence for Morpho Blue lending adapter on Base Sepolia.

## Setup
- **Chain:** Base Sepolia (Chain ID 84532)
- **RPC Endpoint:** `https://sepolia.base.org`
- **Method:** `eth_simulateV1` in bundle mode (`blockStateCalls`, `validation: true`, `traceTransfers: true`)
- **Agent Wallet:** `0xc18afDC2dF55772bA3c031Df98305F02a3a36493`
- **State Override:** Overrode slot 0 (`mapping(address => uint256) balances`) of Aave USDC `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` with 100 USDC (`0x5f5e100`), plus 1 ETH native balance for simulation gas validation.
- **Morpho Blue Contract:** `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
- **Loan Token:** Aave USDC `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` (6 decimals)

---

## 1. Market: USDC/WBTC (`usdc-wbtc`)
- **Market ID:** `0xa7cc9d96371f4c4b5be131281cd24a410cf6b76dcfb4319004d5bd66045adb97`
- **Collateral Token:** Aave WBTC `0x54114591963CF60EF3aA63bEfD6eC263D98145a4`
- **Oracle:** `0xb22092390D44EF7271a71a371097aBf44aBb6C27`
- **IRM:** AdaptiveCurveIRM `0x46415998764C29aB2a25CbeA6254146D50D22687`
- **LLTV:** `0.86e18` (`860000000000000000`)

### Simulation Sequence:
1. **Approve 10 USDC (`10,000,000` units) to Morpho Blue (`0x095ea7b3`)**
   - **Target:** `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 46,236

2. **Supply 10 USDC to Morpho Blue (`0xa99aad89`)**
   - **Target:** `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
   - **Input:** `assets = 10,000,000`, `shares = 0`, `onBehalf = 0xc18afDC2dF55772bA3c031Df98305F02a3a36493`, `data = 0x`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 104,757
   - **Return Value:** `assets = 10000000` (10.0 USDC), `shares = 9985999111970`

3. **Position Read (`0x93c52062`)**
   - **Target:** `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
   - **Input:** `marketId = 0xa7cc9d96...`, `user = 0xc18afDC2dF55772bA3c031Df98305F02a3a36493`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 26,989
   - **Decoded Return:** `supplyShares = 9985999111970`, `borrowShares = 0`, `collateral = 0`

4. **Withdraw 9.9999 USDC (`9,999,900` units) from Morpho Blue (`0x5c2bea49`)**
   - **Target:** `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
   - **Input:** `assets = 9,999,900`, `shares = 0`, `onBehalf = receiver = 0xc18afDC2dF55772bA3c031Df98305F02a3a36493`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 62,076
   - **Return Value:** `assets = 9999900` (9.9999 USDC), `shares = 9985899251980`

---

## 2. Market: USDC/cbETH (`usdc-cbeth`)
- **Market ID:** `0x532165f57c1077690d6b2f413e5555eba34611df4e625820ca6aa187f3f89b65`
- **Collateral Token:** Aave cbETH `0xD171b9694f7A2597Ed006D41f7509aaD4B485c4B`
- **Oracle:** `0xF4FbCFDdA39202549ca4f1230E694c32B5A2F80D`
- **IRM:** AdaptiveCurveIRM `0x46415998764C29aB2a25CbeA6254146D50D22687`
- **LLTV:** `0.915e18` (`915000000000000000`)

### Simulation Sequence:
1. **Approve 10 USDC (`10,000,000` units) to Morpho Blue (`0x095ea7b3`)**
   - **Target:** `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 46,236

2. **Supply 10 USDC to Morpho Blue (`0xa99aad89`)**
   - **Target:** `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
   - **Input:** `assets = 10,000,000`, `shares = 0`, `onBehalf = 0xc18afDC2dF55772bA3c031Df98305F02a3a36493`, `data = 0x`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 104,775
   - **Return Value:** `assets = 10000000` (10.0 USDC), `shares = 6925076416933`

3. **Position Read (`0x93c52062`)**
   - **Target:** `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
   - **Input:** `marketId = 0x532165f5...`, `user = 0xc18afDC2dF55772bA3c031Df98305F02a3a36493`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 26,989
   - **Decoded Return:** `supplyShares = 6925076416933`, `borrowShares = 0`, `collateral = 0`

4. **Withdraw 9.9999 USDC (`9,999,900` units) from Morpho Blue (`0x5c2bea49`)**
   - **Target:** `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`
   - **Input:** `assets = 9,999,900`, `shares = 0`, `onBehalf = receiver = 0xc18afDC2dF55772bA3c031Df98305F02a3a36493`
   - **Status:** `0x1` (Success)
   - **Gas Used:** 62,076
   - **Return Value:** `assets = 9999900` (9.9999 USDC), `shares = 6925007166170`

---

## 3. Summary of Gas Metrics
| Operation | Target Contract | Gas Used (`usdc-wbtc`) | Gas Used (`usdc-cbeth`) | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `approve` | Aave USDC | 46,236 | 46,236 | Exact amount approved to Morpho Blue |
| `supply` | Morpho Blue | 104,757 | 104,775 | Assets deposited, shares minted |
| `position` | Morpho Blue | 26,989 | 26,989 | View read of supply shares |
| `withdraw` | Morpho Blue | 62,076 | 62,076 | Assets withdrawn to wallet |

All calls in both bundles completed with status `0x1` (no reverts).
