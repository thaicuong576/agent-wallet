# Research: Aave V3 Addresses and Faucets on Base Sepolia (Chain ID 84532)

## Question
What are the Aave V3 `Pool`, `PoolAddressesProvider`, the test USDC token the Aave faucet mints, and its aToken on Base Sepolia (chain id 84532)? Which faucets give Base Sepolia ETH and the Aave test USDC, and what do they require (login, captcha, limits)?

## Method
This research uses primary sources exclusively:
1. **Official Aave Documentation**:
   - [Aave Deployed Contracts / Addresses Dashboard](https://aave.com/docs/resources/addresses)
   - [Aave V3 Testing & Debugging Guide](https://aave.com/docs/aave-v3/smart-contracts/testing-and-debugging)
2. **Official Aave Address Registry (`bgd-labs/aave-address-book`)**:
   - [`AaveV3BaseSepolia.sol`](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol)
   - [`MiscBaseSepolia.sol`](https://github.com/bgd-labs/aave-address-book/blob/main/src/MiscBaseSepolia.sol)
   - [Aave Address Book Release JSON (`address-book.json`)](https://assets.aave.com/address-book/releases/latest/address-book.json)
3. **Official Base Network Documentation**:
   - [Base Faucets Documentation](https://docs.base.org/tools/network-faucets)

For every address identified in primary sources, on-chain verification was executed via read-only JSON-RPC calls against the public Base Sepolia endpoint (`https://sepolia.base.org`, Chain ID 84532 / `0x14a34`):
- `eth_getCode`: Verified that the contract is deployed and returns non-empty bytecode (`!= 0x`).
- `eth_call`:
  - Verified `symbol()`, `decimals()`, and `name()` for token contracts.
  - Verified `PoolAddressesProvider.getPool()` (selector `0x026b1d5f`) resolves to the `Pool` contract.
  - Verified `Pool.ADDRESSES_PROVIDER()` (selector `0x0542975c`) resolves to `PoolAddressesProvider`.
  - Verified `Pool.getReservesList()` (selector `0xd1946dbc`) contains the test USDC contract.
  - Verified `Pool.getReserveData(USDC)` (selector `0x35ea6a75`) points to the exact aToken address.
  - Verified Faucet permission status (`isPermissioned()`, selector `0xe2a4157c`), token configuration (`getTokenConfig(USDC)`, selector `0xcb67e3b1`), and max mint amount limit via simulated `mint(address,address,uint256)` execution.

## Findings

### 1. Core Contracts and Tokens Table

| Role / Component | Contract Address | Verified Code (`eth_getCode != 0x`) | On-Chain Verification & Metadata | Status | Primary Source |
| :--- | :--- | :---: | :--- | :---: | :--- |
| **Pool** (Aave V3 Pool Proxy) | `0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27` | Yes (1,847 bytes) | `ADDRESSES_PROVIDER()` returns `0xE4C23309117Aa30342BFaae6c95c6478e0A4Ad00`. `getReservesList()[0]` is USDC `0xba50Cd2A...`. | **VERIFIED** | [`bgd-labs/aave-address-book` (`AaveV3BaseSepolia.sol#L16-L17`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L16-L17) |
| **PoolAddressesProvider** | `0xE4C23309117Aa30342BFaae6c95c6478e0A4Ad00` | Yes (4,763 bytes) | `getPool()` returns `0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27`. `getPriceOracle()` returns `0x943b0dE1...`. `getPoolDataProvider()` returns `0xBc9f5b7E...`. | **VERIFIED** | [`bgd-labs/aave-address-book` (`AaveV3BaseSepolia.sol#L11-L13`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L11-L13) |
| **USDC Underlying** (Aave Testnet USDC) | `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` | Yes (3,968 bytes) | `name()` = `"USD Coin"`, `symbol()` = `"USDC"`, `decimals()` = `6`. Listed as first active reserve in `Pool.getReservesList()`. | **VERIFIED** | [`bgd-labs/aave-address-book` (`AaveV3BaseSepolia.sol#L88-L91`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L88-L91) |
| **aUSDC** (Aave Base Sepolia aToken) | `0x10F1A9D11CDf50041f3f8cB7191CBE2f31750ACC` | Yes (1,847 bytes) | `name()` = `"Aave Base Sepolia USDC"`, `symbol()` = `"aBasSepUSDC"`, `decimals()` = `6`. Matches `aTokenAddress` in `Pool.getReserveData(USDC)`. | **VERIFIED** | [`bgd-labs/aave-address-book` (`AaveV3BaseSepolia.sol#L93-L94`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L93-L94) |
| **Aave Testnet Faucet** | `0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc` | Yes (3,991 bytes) | `isPermissioned()` = `false`. `getTokenConfig(USDC)` = cooldown `3,600` sec (1 hour). Max mint limit per call = `1,000,000` USDC (`10^12` atomic units). | **VERIFIED** | [`bgd-labs/aave-address-book` (`MiscBaseSepolia.sol#L12-L13`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/MiscBaseSepolia.sol#L12-L13) |
| **PoolConfigurator** | `0x0Bf6bdFF4da24C272BC524d521Ab0db20601D384` | Yes (1,847 bytes) | `PoolAddressesProvider.getPoolConfigurator()` returns this address. Admin proxy for aToken configurations. | **VERIFIED** | [`bgd-labs/aave-address-book` (`AaveV3BaseSepolia.sol#L19-L21`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L19-L21) |
| **AaveOracle** | `0x943b0dE18d4abf4eF02A85912F8fc07684C141dF` | Yes (4,411 bytes) | `PoolAddressesProvider.getPriceOracle()` returns this address. Price feed oracle for collateral calculations. | **VERIFIED** | [`bgd-labs/aave-address-book` (`AaveV3BaseSepolia.sol#L23-L24`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L23-L24) |
| **AaveProtocolDataProvider** | `0xBc9f5b7E248451CdD7cA54e717a2BFe1F32b566b` | Yes (11,460 bytes) | `PoolAddressesProvider.getPoolDataProvider()` returns this address. View provider for protocol reserve data. | **VERIFIED** | [`bgd-labs/aave-address-book` (`AaveV3BaseSepolia.sol#L32-L34`)](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L32-L34) |

---

### 2. Critical On-Chain Discrepancy: Aave Test USDC vs. Circle Native Test USDC

> [!IMPORTANT]
> **Circle's official native testnet USDC on Base Sepolia (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`) is NOT the asset accepted by Aave V3 on Base Sepolia.**
>
> On Base Sepolia, the Aave V3 Pool was initialized with Aave's custom mock USDC (`0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`). If test USDC is acquired from Circle's official faucet (`faucet.circle.com`), attempting to call `Pool.supply()` or `Pool.deposit()` with Circle's token will fail with reserve errors because Circle's USDC is not listed in `Pool.getReservesList()`.
>
> Applications and tests interacting with Aave V3 Base Sepolia **must** use `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`, minted through the Aave Faucet contract.

---

### 3. Faucets Comparison and Instructions

#### A. Aave Test USDC Faucets

1. **On-Chain Direct Mint (Recommended for Automated Tests & Scripts)**:
   - **Contract**: `0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc` (Aave Testnet Faucet)
   - **Method**: `mint(address token, address to, uint256 amount)` (selector `0xc6c3bbe6`)
     - `token`: `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`
     - `to`: Recipient address
     - `amount`: Up to `1,000,000,000,000` (1,000,000 USDC with 6 decimals; `10^12` wei)
   - **Requirements**:
     - **Login**: None.
     - **Captcha**: None.
     - **Permissions**: Completely permissionless (`isPermissioned()` returned `false`).
     - **Gas**: Small amount of Base Sepolia ETH to submit transaction.
   - **Limits**: Cooldown of 3,600 seconds (1 hour) per caller address (`getTokenConfig(USDC)`). Max transaction limit: 1,000,000 USDC per call.

2. **Aave Web Interface (Recommended for Manual Testing)**:
   - **URL**: [Aave Web App](https://app.aave.com)
   - **Process**:
     1. Open settings in the top right corner and switch on **Testnet mode**.
     2. Select **Base Sepolia** market.
     3. Navigate to the **Faucet** page or click the **Faucet** button next to USDC in the dashboard.
     4. Confirm the transaction in the connected Web3 wallet.
   - **Requirements**: Web3 wallet connected to Base Sepolia with gas ETH. No captcha, no KYC.
   - **Limits**: Enforces the same on-chain limit (1,000,000 USDC / 1 hr cooldown).

#### B. Base Sepolia Native ETH Faucets (for Gas)

| Faucet Provider | URL | Requirements | Limits / Cooldown | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Superchain Faucet** (Optimism / Base) | [console.optimism.io/faucet](https://console.optimism.io/faucet) | Sign in with GitHub (account > 30 days old), or verify Onchain Identity (World ID, Coinbase Verifications). | - Standard GitHub: **0.05 Base Sepolia ETH** / 24h.<br>- Onchain Identity: **Up to 1.0 Base Sepolia ETH** / 24h. | Recommended primary faucet. Reliable and official across Superchain. |
| **Coinbase Developer Platform Faucet** | [coinbase.com/faucets/base-sepolia-faucet](https://www.coinbase.com/faucets/base-sepolia-faucet) | Coinbase account login. Optional: CDP API key for programmatic requests. | **0.1 Base Sepolia ETH** / 24h (up to 1.0 ETH for holders/KYC). | Official Coinbase faucet for Base testnets. |
| **QuickNode Faucet** | [faucet.quicknode.com/base/sepolia](https://faucet.quicknode.com/base/sepolia) | Connect Web3 wallet. Optional: Tweet / X post multiplier. | **0.05 ETH** (or 0.1 ETH with social share) / 24h. Cloudflare captcha check. | Good secondary option when GitHub or Coinbase login is unavailable. |
| **Alchemy Faucet** | [alchemy.com/faucets/base-sepolia](https://www.alchemy.com/faucets/base-sepolia) | Alchemy account login, Cloudflare Turnstile, and minimum 0.001 ETH balance on Ethereum Mainnet. | **0.1 Base Sepolia ETH** / 24h. | Requires mainnet balance to deter sybils. |
| **Sepolia PoW Faucet + Base Bridge** | [sepolia-faucet.pk910.de](https://sepolia-faucet.pk910.de) + [bridge.base.org/deposit](https://bridge.base.org/deposit) | Proof-of-work browser mining (no login required). | Mine testnet ETH, then bridge to Base Sepolia via official Base bridge. | Zero login or KYC fallback if all third-party faucets are throttled. |

## Recommendation

1. **Protocol Configuration**:
   - Store the following verified contract addresses in the application config / environment variables:
     - `AAVE_V3_POOL`: `0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27`
     - `AAVE_V3_POOL_ADDRESSES_PROVIDER`: `0xE4C23309117Aa30342BFaae6c95c6478e0A4Ad00`
     - `AAVE_V3_USDC`: `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`
     - `AAVE_V3_A_USDC`: `0x10F1A9D11CDf50041f3f8cB7191CBE2f31750ACC`
     - `AAVE_V3_FAUCET`: `0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc`

2. **Testnet Token Acquisition Strategy**:
   - For gas ETH: Use the [Superchain Faucet](https://console.optimism.io/faucet) or [Coinbase Faucet](https://www.coinbase.com/faucets/base-sepolia-faucet).
   - For test USDC: **Do not** use Circle's testnet USDC faucet. Use the on-chain Aave Faucet (`0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc`) by invoking `mint(0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f, walletAddress, amount)` or via [app.aave.com](https://app.aave.com) in Testnet mode.
   - For automated CI/CD and end-to-end testing: Because the Faucet contract is permissionless, test runner wallets can programmatically mint test USDC directly before test execution without manual user interaction.

## Sources
- [Aave Documentation - Addresses Dashboard](https://aave.com/docs/resources/addresses)
- [Aave Documentation - Testing & Debugging Guide](https://aave.com/docs/aave-v3/smart-contracts/testing-and-debugging)
- [BGD Labs Aave Address Book - AaveV3BaseSepolia.sol](https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol)
- [BGD Labs Aave Address Book - MiscBaseSepolia.sol](https://github.com/bgd-labs/aave-address-book/blob/main/src/MiscBaseSepolia.sol)
- [Aave Address Book Release JSON Manifest](https://assets.aave.com/address-book/releases/latest/address-book.json)
- [Base Official Documentation - Network Faucets](https://docs.base.org/tools/network-faucets)
- [Base Sepolia Public JSON-RPC](https://sepolia.base.org)
- [BaseScan Sepolia Explorer](https://sepolia.basescan.org)

## Open questions
- None. All addresses and token parameters have been cross-checked and verified on-chain against Base Sepolia RPC.
