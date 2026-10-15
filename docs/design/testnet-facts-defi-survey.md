# Research: DeFi protocols the agent can use on Base Sepolia (R4, ticket #132)

Chain: Base Sepolia (84532). Method: read-only JSON-RPC against `https://sepolia.base.org` (User-Agent header set), `eth_getCode`, view calls, and `eth_simulateV1` call sequences from the agent wallet `0xc18afDC2dF55772bA3c031Df98305F02a3a36493` (mint, approve, supply, withdraw chained in one simulated block; no transaction was sent, no key used). Morpho market discovery used `eth_getLogs` on `https://base-sepolia.gateway.tenderly.co` because the public RPC rejects ranges above a few hundred blocks. Simulations ran on 2026-10-16 (block about 0x2dee09b); pool and market state drifts, re-check before coding.

**Evidence levels:** *on-chain* = verified by the calls above. *recalled* = from general knowledge, NOT verified in this run (marked as such; confirm before building).

## 1. Summary table (the answer)

| Protocol | Action | Token the wallet uses | Verdict | Effort (h) |
|---|---|---|---|---|
| Aave v3 (existing) | supply / withdraw | Aave USDC (faucet, no captcha, 1,000,000 per call) | ADD (done) | 0 |
| Aave v3 | supply / withdraw USDT | Aave USDT (faucet, 1,000,000 per call) | ADD | 2 |
| Aave v3 | supply / withdraw cbETH, LINK | faucet: 100 cbETH, 10,000 LINK per call | MAYBE (volatile; USD price via Aave oracle) | 3 |
| Aave v3 | WBTC, WETH | WBTC supply cap is full (175/175); WETH not mintable | SKIP | 0 |
| Aave v3 | borrow / repay (loop) | USDC collateral, borrow USDT or WETH | PAPER/MAYBE (needs health-factor policy, new tier) | 8 |
| Uniswap v3 | swap USDC <-> WETH, fee 3000 | Aave USDC | ADD (already scoped) | 0 to 4 |
| Uniswap v3 | swap USDC <-> WBTC, fee 500 | Aave USDC / faucet WBTC | ADD (price anchored to oracle: about 83k USD) | 2 |
| Uniswap v3 | LP mint / increase / decrease / collect | USDC + WETH | MAYBE (works; needs NFT id tracking) | 10 |
| Compound III (cUSDCv3) | supply / withdraw | Circle USDC (captcha faucet; not Aave USDC) | MAYBE: needs a swap route to get the token; yield about 0.03% APR | 6 |
| Morpho Blue | supply / withdraw (lend) | Aave USDC, pick Aave-USDC/cbETH and /WBTC markets | ADD (best new yield: live, funded, no captcha) | 8 |
| Moonwell (Comptroller + mTokens) | mint / redeemUnderlying | USDbC via `allocateTo` (no captcha, unbounded) or WETH wrap | ADD for USDbC; WETH MAYBE | 6 |
| Aave stata token (ERC-4626) | deposit / redeem | Aave USDC | ADD (simplest interface; same yield as Aave) | 3 |
| MetaMorpho vaults (ERC-4626) | deposit / redeem | Aave USDC / Circle USDC | MAYBE (factory live, vault list not enumerated) | 4 |
| Aerodrome / Curve | LP / swap | n/a | SKIP (no Base Sepolia deployment found) | 0 |
| Lido / Rocket Pool | stake | ETH | PAPER (testnets are Holesky/Hoodi/Sepolia, other chain) | 6 |
| EigenLayer / Ether.fi / Symbiotic / Karak | restake | LST | PAPER (not on Base Sepolia, recalled) | 6 each |
| Yearn / Beefy | vault | n/a | SKIP (no testnet deployment, recalled) | 0 |
| Pendle | PT/YT | n/a | PAPER (no verified Base Sepolia market) | 6 |
| Gearbox | leverage | n/a | SKIP/PAPER (not on Base Sepolia, recalled) | 0 |
| GMX / Gains | perps | USDC | PAPER (Arbitrum Sepolia, recalled; needs multi-chain) | 10 |
| Hyperliquid testnet / dYdX | perps | n/a | PAPER (own chains / API; wallet signer differs) | 10 |
| Synthetix v3 | perps | n/a | SKIP (address not found on Base Sepolia in this run) | 0 |
| Ethena / Maker sDAI | stable/RWA yield | USDe / DAI | PAPER (Sepolia, recalled; whitelist/mint limits) | 6 |

Recommendation: add, in this order, **Morpho Blue lending** (highest yield), **Aave stata (ERC-4626)** and **Aave USDT** (cheap), **Moonwell USDbC**. Everything else is PAPER (simulated, clearly labelled) or SKIP.

## 2. Aave v3 reserves (on-chain)

Pool `0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27`, data provider `0xBc9f5b7E248451CdD7cA54e717a2BFe1F32b566b`, faucet `0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc`, oracle `0x943b0de18d4abf4ef02a85912f8fc07684c141df` (8 decimals).

| Symbol | Underlying | Dec | aToken | Supply cap | Active / frozen / paused | Faucet max per call | Oracle USD |
|---|---|---|---|---|---|---|---|
| USDC | `0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f` | 6 | `0x10F1A9D11CDf50041f3f8cB7191CBE2f31750ACC` | none | yes / no / no | 1,000,000 | 0.9999 |
| USDT | `0x0a215D8ba66387DCA84B284D18c3B4ec3de6E54a` | 6 | `0xce3caae5ed17a7aafceebc897de843fa6cc0c018` | none | yes / no / no | 1,000,000 | 0.9987 |
| WBTC | `0x54114591963CF60EF3aA63bEfD6eC263D98145a4` | 8 | `0x47db195baf46898302c06c31bcf46c01c64accf9` | 175 | yes / no / no | at least 1 (not bisected) | 82,204 (cap FULL) |
| WETH | `0x4200000000000000000000000000000000000006` | 18 | `0x73a5bb60b0b0fc35710ddc0ea9c407031e31bdbb` | 2,500 | yes / no / no | not mintable | 2,443.96 |
| cbETH | `0xD171b9694f7A2597Ed006D41f7509aaD4B485c4B` | 18 | `0x9fd6d1dbad7c052e0c43f46df36eec6a68814b63` | none | yes / no / no | 100 | 2,789.71 |
| LINK | `0x810D46F9a9027E28F9B01F75E2bdde839dA61115` | 18 | `0x0ad46de765522399d7b25b438b230a894d72272b` | 200,000 (194,715 used) | yes / no / no | 10,000 | 12.55 |

- There is no DAI, EURS or GHO reserve on this deployment (6 reserves total).
- Faucet `mint(address token,address to,uint256 amount)` selector `0xc6c3bbe6`, `isPermissioned()` false, no captcha. Per-call cap found by `eth_call` probing: USDC and USDT 1,000,000 units, cbETH 100, LINK 10,000. WETH reverts with `Mint limit transaction exceeded` (not faucet-mintable; wrap ETH instead).
- Simulated `mint 1 -> approve -> supply -> withdraw` for USDC, USDT, cbETH, LINK: all succeed (supply about 185k gas, withdraw about 150-167k gas). WBTC supply reverts with `0xf58f733a` (SUPPLY_CAP_EXCEEDED).
- Selectors: `supply(address,uint256,address,uint16)` `0x617ba037`, `withdraw(address,uint256,address)` `0x69328dec`, `borrow(address,uint256,uint256,uint16,address)` `0xa415bcad`, `repay(address,uint256,uint256,address)` `0x573ade81`, `approve` `0x095ea7b3`.
- USD price for non-stable reserves: `AaveOracle.getAssetPrice(address)` (8 decimals), which the policy engine can read as a view call. Testnet prices are admin-set and may not track the market.
- Risks: faucet tokens have no real value; the supply APY on testnet reserves is tiny; WBTC and LINK are at or near their caps so supply can start reverting any time.

## 3. Uniswap v3 swaps (on-chain)

Factory `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24`, SwapRouter02 `0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4`, QuoterV2 `0xC5290058841028F1614F3A6F0F5816cAd0df5E27`, NonfungiblePositionManager `0x27F971cb582BF9E50F397e4d29a5C7A34f11faA2` (its `factory()` and `WETH9()` match).

Pools with liquidity (Aave USDC pairs; Circle USDC shown as cUSDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e`):

| Pair | Fee | Pool | State |
|---|---|---|---|
| USDC/WETH | 3000 | `0x92266210facf124f190fbad79aa230dfb85926ce` | liq 4.96e12, reserves 168.2 USDC / 1.935 WETH |
| USDC/WETH | 100 | `0x068e0df9dfcce57df2c2b60bdb2318ce49a07a31` | liq 8.6e12, reserve 0.014 USDC (dry on the USDC side) |
| USDC/WETH | 500, 10000 | exist | liquidity 0, unusable |
| USDC/WBTC | 500 | `0x80e1d0dc2ef7ca7b25548b0458234c3abbd1114d` | liq 6.3e8, 1,134.5 USDC / 0.01169 WBTC |
| USDC/cUSDC | 500 | `0x4deb53d22e5e927d62c14023c5490ef1335fb4f0` | 1,014 USDC / 6.2 cUSDC |
| WETH/cUSDC | 3000 | `0x46880b404cd35c165eddeff7421019f8dd25f4ad` | 6.48 WETH / 25,677 cUSDC |
| USDC/USDT | 500 | exists | liquidity 0 (quote reverts `LOK`) |

QuoterV2 `quoteExactInputSingle((tokenIn,tokenOut,amountIn,fee,sqrtPriceLimitX96))` selector `0xc6a5026a`:

| Swap | 1 unit | 10 units |
|---|---|---|
| USDC -> WETH, fee 3000 | 0.12299 WETH | 0.76221 WETH |
| USDC -> WETH, fee 100 | 0.000456 WETH | 0.004467 WETH |
| USDC -> WBTC, fee 500 | 0.00001202 WBTC (83.2k USD/BTC) | 0.00012023 WBTC |
| WBTC -> USDC, fee 500 | 0.0001 WBTC -> 8.30 USDC | 0.001 WBTC -> 82.66 USDC |
| USDC -> cUSDC, fee 500 | 0.006136 cUSDC | 0.060819 cUSDC |

- **Important quirk:** the USDC/WETH 3000 pool is badly mispriced right now (1 USDC returns 0.123 WETH, about 8 USDC per WETH versus the oracle 2,444). The earlier note recorded about 297 USDC per WETH, so the pool was traded to an extreme since. A policy that checks slippage against the pool quote will happily accept a price far from market; compare against `AaveOracle` before approving. The USDC/WBTC 500 pool is anchored near the oracle and is the better swap venue.
- `exactInputSingle((address tokenIn,address tokenOut,uint24 fee,address recipient,uint256 amountIn,uint256 amountOutMinimum,uint160 sqrtPriceLimitX96))` selector `0x04e45aaf` on SwapRouter02 (no deadline field). Approve the router for `amountIn` first. `exactInput((bytes path,address recipient,uint256 amountIn,uint256 amountOutMinimum))` selector `0xb858183f`.
- LP via the position manager: `mint(...)` `0x88316456`, `increaseLiquidity` `0x219f5d17`, `decreaseLiquidity` `0x0c49ccbe`, `collect` `0xfc6f7865`. Simulated full-range USDC/WETH 3000 mint (1 USDC + 0.01 WETH after wrapping) succeeds, 390k gas, NFT minted (`balanceOf` 1). Needs approvals for both tokens to the position manager, token id tracking, and an impermanent-loss notion in the IPS. Verdict MAYBE.
- Multi-hop route USDC -> WETH -> cUSDC (both fee 3000) quotes 557 cUSDC for 1 USDC and 3,176 for 10 USDC. This is a testnet artifact caused by the mispriced pool, it will vanish once arbitraged, and it is the only way to get Circle USDC without the captcha faucet. A simulated `exactInput` of 10 USDC succeeded (352k gas).

## 4. Compound III (Comet) (on-chain)

- Comet cUSDCv3 `0x571621Ce60Cebb0c1D442B5afb38B1663C6Bf017` (1,878 bytes, proxy). `name()` "Compound USDC", `baseToken()` = Circle USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (6 dec, 1,798 bytes). Not Aave USDC, so Aave faucet tokens cannot be supplied directly.
- State: totalSupply 140,421 USDC, borrow 849 USDC, utilization 0.6%, supply rate about 0.029% per year. Collateral assets: `0x774ed9edb0c5202df9a86183804b5d9e99dc6ca3` (cap 800) and WETH (cap 1,000). Supply and withdraw not paused.
- Interface: `supply(address asset,uint256 amount)` `0xf2b9fdb8`, `withdraw(address asset,uint256 amount)` `0xf3fef3a3` (supplying the base token earns interest; withdrawing more than your balance borrows). Approve the Comet for the amount first. `balanceOf(address)` returns the supplied base balance.
- Simulation (state override of the wallet's Circle USDC balance, 1 USDC): approve, supply, balanceOf (999,999), withdraw all succeed (supply about 100k gas).
- Obtaining the token: Circle faucet (`faucet.circle.com`) needs a captcha/login (10 USDC per 2h, recalled). Programmatic path only via the mispriced Uniswap route above.
- Verdict MAYBE: interface is trivial, yield negligible, and the input token has no clean mint path.

## 5. Morpho Blue (on-chain)

- Morpho Blue `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb` (15,623 bytes, same CREATE2 address as other chains, deployed block 9,304,957). `owner()` `0x937ce2d6c488b361825d2db5e8a70e26d48afed5`. AdaptiveCurveIRM `0x46415998764C29aB2a25CbeA6254146D50D22687`. MetaMorpho factory v1.0 `0xA9c3D3a366466Fa809d1Ae982Fb2c46E5fC41101` has code (23,911 bytes).
- Market creation is permissionless: 529 `CreateMarket` events, nearly all dust. Markets with the Aave USDC loan token and real size:

| Market id | Collateral | LLTV | Supplied | Borrowed | Util | Supply APR now | Oracle |
|---|---|---|---|---|---|---|---|
| `0x532165f57c1077690d6b2f413e5555eba34611df4e625820ca6aa187f3f89b65` | cbETH (Aave) | 0.915 | 75,003 USDC | 70,180 | 94% | about 192% (utilization spike, not sustainable) | `0xF4FbCFDdA39202549ca4f1230E694c32B5A2F80D`, constant 2,200 |
| `0xa7cc9d96371f4c4b5be131281cd24a410cf6b76dcfb4319004d5bd66045adb97` | WBTC (Aave) | 0.86 | 30,038 | 21,033 | 70% | about 0.87% | `0xb22092390D44EF7271a71a371097aBf44aBb6C27`, constant (about 100,000 USD per BTC) |
| `0x6af42641dd1ddc4fd0c3648e45497a29b78eb50d21fd0f6eac7b8eae2192dd47` | wETH `0x1ddebA64...` (not canonical WETH) | 0.77 | 248 | 241 | 97% | about 462% | `0x97EBCdb0F784CDc9F91490bEBC9C8756491814a3` |

  Circle USDC loan-token markets also exist (for example `0xe36464b73c0c39836918f7b2b9a6f1a8b70d7bb9901b38f29544d9b96119862e`, 3,460 supplied, collateral WETH, LLTV 0.915).
- The wallet mints Aave USDC from the Aave faucet (no captcha) and can supply it here; this is the only no-captcha path to a yield with real utilization.
- Interface (`MarketParams` = `(address loanToken,address collateralToken,address oracle,address irm,uint256 lltv)`):
  - `supply(MarketParams,uint256 assets,uint256 shares,address onBehalf,bytes data)` selector `0xa99aad89`. Pass `assets` > 0 and `shares` 0.
  - `withdraw(MarketParams,uint256 assets,uint256 shares,address onBehalf,address receiver)` selector `0x5c2bea49`.
  - Approve Morpho for the loan token. `position(bytes32 id,address user)` reads supplyShares; `market(bytes32)` reads totals.
- Simulation, 1 USDC, each of the three markets above: mint -> approve -> supply -> position -> withdraw (n-100) all succeed (supply about 105k gas, withdraw about 62k gas). The `position` call returned non-zero supply shares.
- Risks: the markets are unaudited user-created deployments with constant (stub) oracles, so a borrower can post collateral valued far from reality and leave bad debt that socializes to suppliers; withdrawal is bounded by market liquidity (the cbETH market has about 4.8k USDC available); the `LLTV` and oracle are immutable per market, so the IPS must pin the exact market id (whitelist), never accept an id from the LLM freely.
- Verdict ADD for the USDC/WBTC and USDC/cbETH markets, whitelisted by id. Effort: adapter and encoder 3h, policy and market whitelist 2h, tests and docs 3h.

## 6. Moonwell (on-chain)

- Unitroller/Comptroller `0xC72ba45f2Ae134F21DAEb2b7B5fd5De0064Ee9bB` (impl `0xE193BE0A14D86BA554b11075b4235ab2Bd4F7D16`), oracle `0x1abea09b07276669de5bc3a11f820fdc4ba529d1`. Markets (source: `moonwell-fi/moonwell-contracts-v2` `chains/84532.json`):
  - mUSDbC `0x876852425331a113d8E432eFFB3aC5BEf38f033a`, underlying USDbC `0x4616975f7E6045FD9737e95bC527e9e14c727BD2` (6 dec), price 1.00 USD, supply cap 40,000,000.
  - mWETH `0x2F39a349A79492a70E152760ce7123A1933eCf28`, underlying test WETH `0x663A6F02d547dFd639BF689FED298712f2FB69Fb` (18 dec, WETH9 style, `deposit()` wraps ETH), price 2,000 USD.
  - mcbETH `0x5302EbD8BC32435C823c2e22B04Cd6c45f593e89`, underlying `0xdbc885569E711262926803184eC9Ff73816fc953`.
  - No mUSDC market (there is only a Circle USDC address in the repo file, no mToken).
- Token mint path: USDbC is a Compound-style `FaucetToken`: `allocateTo(address,uint256)` selector `0x08bca566` is callable by anyone, no captcha. Simulated for 1, 1,000, 1,000,000 and 1e9 USDbC: succeeds each time; 1e9 USDbC then trips the market cap (`market supply cap reached`), i.e. effectively unbounded minting with the supply cap as the only limit.
- Interface (Compound v2 style): approve the mToken, `mint(uint256)` `0xa0712d68` (returns 0 on success, error codes otherwise: always check the return), `redeemUnderlying(uint256)` `0x852a12e3`, `redeem(uint256)` `0xdb006a75`.
- Simulation: allocateTo 1 USDbC -> approve -> mint -> balanceOf -> redeemUnderlying all succeed (mint about 266k gas, redeem about 180k gas). mWETH: wrap 0.1 ETH (state override), approve, mint, redeemUnderlying succeed.
- Rates: mUSDbC supply rate is about 0 (utilization nil); mWETH about 0.075% APR. Yield is negligible, so value is diversification and demonstrating a second lending protocol.
- Risks: the faucet token is unrestricted and the market admin is a testnet deployer; the mint returns error codes instead of reverting (the adapter must decode the return value).
- Verdict ADD for USDbC (2 calls, trivial). Effort 6h including the return-code handling.

## 7. ERC-4626 and staking (on-chain)

- **Aave stata tokens** (static aToken, ERC-4626) from `bgd-labs/aave-address-book` `AaveV3BaseSepolia.sol`: factory `0x4Afb5ADe7Bd7a670B61f303ab0C740eE8350918f`; USDC `0xf430cb6E2b85f99222fBFA6dFEa18Ff60FA6B32a` (name "Wrapped Aave Base Sepolia USDC", `asset()` = Aave USDC), USDT `0xf63dA51069FAe9448747FA425F8Cb84B0149eC0F`, WBTC `0x5d7538579560F5fdD54F9D264DBe0e697b5BF668`, WETH `0xde7820fFb73059608928cb9e29F6EB1369Ad1342`, cbETH `0x9F56ff6c3441bb89eBFf1c0bc75D4096C33230d1`, LINK `0xFb3AB5ABd3dE4703C0F296B1fe6f5E570e1631Ff`. `deposit(uint256,address)` `0x6e553f65`, `redeem(uint256 shares,address,address)` `0xba087652`. Simulated 1 USDC deposit: mints 803,368 shares (about 0.80 share per USDC), approval to the stata token is needed. My simulated redeem with shares equal to the asset amount reverted because it exceeded the share balance, so redeem must use the share balance, not the asset amount. Gas about 226k deposit.
  Verdict ADD: single ERC-4626 interface that can be reused for MetaMorpho vaults.
- **MetaMorpho factory v1.0** live; vaults were not enumerated (the log query for `CreateMetaMorpho` returned `invalid params` on the gateway). MAYBE; any vault found must be whitelisted by address and checked for asset, curator and totalAssets.
- No generic staking contract for a faucet token was found beyond the above.

## 8. Not found or off-chain (recalled, NOT verified in this run)

- **Aerodrome, Curve:** no Base Sepolia deployment found (the mainnet router address has no code on 84532). SKIP.
- **Synthetix v3:** the address I recalled for Base Sepolia has no code; I did not locate an official deployment, so SKIP rather than guess.
- **Compound on other testnets, Lido, Rocket Pool, EigenLayer, Ether.fi, Symbiotic, Karak, Ethena, Maker sDAI, GMX, Gains, Hyperliquid, dYdX, Pendle, Gearbox, Yearn, Beefy:** none of these were checked on-chain here. By general knowledge these live on Ethereum Sepolia, Holesky/Hoodi, Arbitrum Sepolia or their own chains, and several need allowlists, KYC-gated mints or captcha faucets. They are marked PAPER (simulate and label as simulated) or SKIP in the table, with effort estimates being rough guesses. Verify chain, address and faucet before any build.

## 9. Category coverage (scope update)

| Category | Notable protocol | Chain on testnet | Tokens without captcha? | Verdict |
|---|---|---|---|---|
| DEX swap | Uniswap v3 | Base Sepolia (on-chain) | yes (Aave faucet) | ADD |
| Lending / borrowing | Aave v3, Morpho Blue, Moonwell, Compound III | Base Sepolia (on-chain) | Aave, Morpho, Moonwell yes; Compound no | ADD (supply side); borrow PAPER |
| Liquidity provision | Uniswap v3 NFT positions | Base Sepolia (on-chain) | yes | MAYBE |
| Liquid staking | Lido, Rocket Pool | Sepolia / Holesky / Hoodi (recalled) | ETH faucets only | PAPER |
| Restaking | EigenLayer, Ether.fi, Symbiotic, Karak | Sepolia / Holesky / Hoodi (recalled) | LST needed first | PAPER |
| Yield vaults | ERC-4626 (Aave stata, MetaMorpho) | Base Sepolia (on-chain) | yes | ADD / MAYBE |
| Leverage / looping | Aave borrow loop, Morpho | Base Sepolia (on-chain) | yes | PAPER first |
| Perps / derivatives | GMX, Gains, Hyperliquid, dYdX, Synthetix | Arbitrum Sepolia / own chains (recalled) | varies | PAPER or SKIP |
| Stablecoin / RWA | Ethena, Maker sDAI | Sepolia (recalled) | whitelisted mints | PAPER |

## 10. Cross-cutting notes for the wallet agent

- Do not assume testnet prices are market prices. Both the Uniswap USDC/WETH pool and the Morpho oracles are stubs; use `AaveOracle` as the reference and cap exposure per protocol in the IPS.
- Every new protocol means a new `SUPPORTED_PROTOCOLS` entry, an address constant, a selector table and an adapter; new spender approvals are a permission-tier change and need owner review per AGENTS.md.
- Dust and cap risks: Aave WBTC is at its cap; Morpho markets can drain; the faucet cooldown on the Aave faucet was reported as 3,600 s in the earlier note, so repeated mints in tests may fail.

## Sources

- Aave address book `AaveV3BaseSepolia.sol`: https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol
- Moonwell contracts: https://github.com/moonwell-fi/moonwell-contracts-v2 (`chains/84532.json`)
- Uniswap Base deployments: https://developers.uniswap.org/docs/protocols/v3/deployments/v3-base-deployments
- Morpho addresses: https://docs.morpho.org/get-started/resources/addresses/ (Base Sepolia section not retrieved; addresses verified on-chain instead)
