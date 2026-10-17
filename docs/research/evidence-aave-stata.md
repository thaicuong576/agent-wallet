# Evidence: Aave v3 USDT and Aave Stata (ERC-4626) Vaults (D1, Issue #138)

Chain: Base Sepolia (84532)  
RPC: `https://sepolia.base.org` (User-Agent header set)  
Method: Read-only view calls and `eth_simulateV1` bundle execution  
Wallet: `0xc18afDC2dF55772bA3c031Df98305F02a3a36493`  
Timestamp: 2026-10-16

---

## 1. On-Chain Verified Addresses & Selectors

### Contracts

| Name | Address | Decimals | Role / Notes |
| :--- | :--- | :--- | :--- |
| Aave Pool | `0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27` | - | Aave v3 pool contract |
| Aave Faucet | `0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc` | - | Testnet mock faucet (`mint(address,address,uint256)`) |
| Aave Mock USDC | `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` | 6 | Aave v3 USDC reserve token |
| Aave Mock USDT | `0x0a215D8ba66387DCA84B284D18c3B4ec3de6E54a` | 6 | Aave v3 USDT reserve token |
| Aave aUSDC | `0x10F1A9D11CDf50041f3f8cB7191CBE2f31750ACC` | 6 | Aave v3 aUSDC aToken |
| Aave aUSDT | `0xce3caae5ed17a7aafceebc897de843fa6cc0c018` | 6 | Aave v3 aUSDT aToken |
| Stata USDC Vault | `0xf430cb6E2b85f99222fBFA6dFEa18Ff60FA6B32a` | 6 | ERC-4626 static aToken (`asset()` = Aave USDC) |
| Stata USDT Vault | `0xf63dA51069FAe9448747FA425F8Cb84B0149eC0F` | 6 | ERC-4626 static aToken (`asset()` = Aave USDT) |

### Selectors (Keccak-256)

| Function Signature | Selector | Verified On-Chain |
| :--- | :--- | :--- |
| `deposit(uint256,address)` | `0x6e553f65` | Yes |
| `withdraw(uint256,address,address)` | `0xb460af94` | Yes |
| `redeem(uint256,address,address)` | `0xba087652` | Yes |
| `convertToAssets(uint256)` | `0x07a2d13a` | Yes |
| `asset()` | `0x38d52e0f` | Yes |
| `balanceOf(address)` | `0x70a08231` | Yes |
| `approve(address,uint256)` | `0x095ea7b3` | Yes |
| `supply(address,uint256,address,uint16)` | `0x617ba037` | Yes |
| `withdraw(address,uint256,address)` | `0x69328dec` | Yes |
| `mint(address,address,uint256)` | `0xc6c3bbe6` | Yes |

On-chain view query results:
- `asset()` on Stata USDC (`0xf430cb6...`) returned `0x000000000000000000000000ba50cd2a20f6da35d788639e581bca8d0b5d4d5f` (matches `AAVE_V3_USDC`).
- `asset()` on Stata USDT (`0xf63dA51...`) returned `0x0000000000000000000000000a215d8ba66387dca84b284d18c3b4ec3de6e54a` (matches `AAVE_V3_USDT`).
- `convertToAssets(1_000_000)` on Stata USDC returned `0x12fe5b` (1,244,763 assets per 1,000,000 shares, reflecting accrued yield).
- `convertToAssets(1_000_000)` on Stata USDT returned `0x0f43b9` (1,000,377 assets per 1,000,000 shares).

---

## 2. Simulations: Aave v3 USDT Pool

Simulated via `eth_simulateV1` with `blockStateCalls` chaining actions from wallet `0xc18afDC2dF55772bA3c031Df98305F02a3a36493`.

### Sequence A: Faucet Mint -> Approve -> Supply -> Withdraw

```
Calls:
0: Faucet.mint(USDT, wallet, 100_000_000)
1: USDT.approve(AavePool, 100_000_000)
2: AavePool.supply(USDT, 100_000_000, wallet, 0)
3: AavePool.withdraw(USDT, 50_000_000, wallet)
```

**Results:**
| Step | Action | Status | Gas Used | Error |
| :--- | :--- | :--- | :--- | :--- |
| 0 | `faucet.mint` (100 USDT) | `0x1` | 87,902 | None |
| 1 | `usdt.approve` (100 USDT -> Pool) | `0x1` | 46,236 | None |
| 2 | `pool.supply` (100 USDT) | `0x1` | 184,595 | None |
| 3 | `pool.withdraw` (50 USDT) | `0x1` | 167,132 | None |

### Sequence B: State Override -> Approve -> Supply -> Withdraw

State override: USDT balance mapping slot 0 (`_balances[wallet]`) overridden to 1,000 USDT (`1_000_000_000` units).

```
Calls:
0: USDT.approve(AavePool, 100_000_000)
1: AavePool.supply(USDT, 100_000_000, wallet, 0)
2: AavePool.withdraw(USDT, 50_000_000, wallet)
```

**Results:**
| Step | Action | Status | Gas Used | Error |
| :--- | :--- | :--- | :--- | :--- |
| 0 | `usdt.approve` (100 USDT -> Pool) | `0x1` | 46,236 | None |
| 1 | `pool.supply` (100 USDT) | `0x1` | 189,395 | None |
| 2 | `pool.withdraw` (50 USDT) | `0x1` | 150,032 | None |

---

## 3. Simulations: Aave Stata USDC Vault

Simulated via `eth_simulateV1` targeting Stata USDC Vault `0xf430cb6E2b85f99222fBFA6dFEa18Ff60FA6B32a`.

### Sequence A: Faucet Mint -> Approve -> Deposit -> Withdraw

```
Calls:
0: Faucet.mint(USDC, wallet, 100_000_000)
1: USDC.approve(StataVault, 100_000_000)
2: StataVault.deposit(100_000_000, wallet)
3: StataVault.withdraw(50_000_000, wallet, wallet)
```

**Results:**
| Step | Action | Status | Gas Used | Error |
| :--- | :--- | :--- | :--- | :--- |
| 0 | `faucet.mint` (100 USDC) | `0x1` | 53,702 | None |
| 1 | `usdc.approve` (100 USDC -> StataVault) | `0x1` | 46,236 | None |
| 2 | `stataVault.deposit` (100 USDC) | `0x1` | 225,721 | None |
| 3 | `stataVault.withdraw` (50 USDC) | `0x1` | 192,770 | None |

### Sequence B: State Override -> Approve -> Deposit -> Withdraw

State override: USDC balance mapping slot 0 (`_balances[wallet]`) overridden to 1,000 USDC (`1_000_000_000` units).

```
Calls:
0: USDC.approve(StataVault, 100_000_000)
1: StataVault.deposit(100_000_000, wallet)
2: StataVault.withdraw(50_000_000, wallet, wallet)
```

**Results:**
| Step | Action | Status | Gas Used | Error |
| :--- | :--- | :--- | :--- | :--- |
| 0 | `usdc.approve` (100 USDC -> StataVault) | `0x1` | 46,236 | None |
| 1 | `stataVault.deposit` (100 USDC) | `0x1` | 225,721 | None |
| 2 | `stataVault.withdraw` (50 USDC) | `0x1` | 192,770 | None |

---

## 4. Simulations: Aave Stata USDT Vault

Targeting Stata USDT Vault `0xf63dA51069FAe9448747FA425F8Cb84B0149eC0F`.

```
Calls:
0: Faucet.mint(USDT, wallet, 100_000_000)
1: USDT.approve(StataUSDT, 100_000_000)
2: StataUSDT.deposit(100_000_000, wallet)
3: StataUSDT.withdraw(50_000_000, wallet, wallet)
```

**Results:**
| Step | Action | Status | Gas Used | Error |
| :--- | :--- | :--- | :--- | :--- |
| 0 | `faucet.mint` (100 USDT) | `0x1` | 87,902 | None |
| 1 | `usdt.approve` (100 USDT -> StataUSDT) | `0x1` | 46,236 | None |
| 2 | `stataVault.deposit` (100 USDT) | `0x1` | 220,921 | None |
| 3 | `stataVault.withdraw` (50 USDT) | `0x1` | 209,870 | None |

---

## 5. Security & Verification Summary

1. **Spender constraints**: All approval calldata targets only the declared contract spenders (`AAVE_V3_POOL` for Aave v3; `AAVE_STATA_USDC` or `AAVE_STATA_USDT` for Aave Stata), naming the exact order amount.
2. **Selector accuracy**: ERC-4626 standard selectors were confirmed via Keccak-256 and matched on-chain behavior without revert.
3. **No transaction was sent**: All operations were strictly read-only view calls and `eth_simulateV1` against public RPC `https://sepolia.base.org`.
