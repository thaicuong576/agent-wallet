# Evidence: Live Per-Protocol Risk Inputs on Base Sepolia

- **Date**: 2026-10-16 14:43:29 UTC
- **Network**: Base Sepolia (Chain ID 84532)
- **RPC Endpoint**: `https://sepolia.base.org`
- **Block Number**: 48196762 (`0x2df6c9a`)
- **Execution Mode**: Read-only JSON-RPC simulation (`eth_call`), zero gas spent, no private keys or signing involved.
- **Provider Module**: `core.risk.router.RiskRouter`

---

## Summary of Whitelisted Markets & Pools

| Protocol | Market / Pool | Token | Pool TVL (USD) | Utilization (bps) | Utilization (%) | Data Age (s) | Source Classification |
|---|---|---|---|---|---|---|---|
| `aave-v3` | Aave V3 Reserve | USDC | $33,531,513.67 | 2083 | 20.83% | 2,562 | TVL & util onchain; secondary reference |
| `aave-v3` | Aave V3 Reserve | USDT | $30,370,159.35 | 1854 | 18.54% | 3,111 | TVL & util onchain; secondary reference |
| `aave-stata` | Stata Vault Underlying | USDC | $33,531,513.67 | 2083 | 20.83% | 2,565 | TVL & util onchain; secondary reference |
| `aave-stata` | Stata Vault Underlying | USDT | $30,370,159.36 | 1854 | 18.54% | 3,114 | TVL & util onchain; secondary reference |
| `morpho-blue` | `usdc-wbtc` market | USDC | $30,042.24 | 7001 | 70.01% | 119 | TVL & util onchain; secondary reference |
| `morpho-blue` | `usdc-cbeth` market | USDC | $75,003.39 | 9356 | 93.56% | 5,611,629 | TVL & util onchain; secondary reference |
| `uniswap-v3` | USDC/WETH (fee 3000) | USDC | $1,598.01 | 0 | 0.00% | 0 | TVL & prices onchain; secondary reference |
| `uniswap-v3` | USDC/WBTC (fee 500) | USDC | $2,095.56 | 0 | 0.00% | 0 | TVL & prices onchain; secondary reference |

---

## Detailed Live Contract Outputs

### 1. Aave V3 USDC Reserve
- Target: `AaveProtocolDataProvider` (`0xBc9f5b7E248451CdD7cA54e717a2BFe1F32b566b`)
- Asset: `AAVE_V3_USDC` (`0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f`)
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-10-16T14:00:54Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "aave-v3",
  "token": "USDC",
  "pool_tvl_usd": "33531513.668055",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 2083,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 2562
}
```

### 2. Aave V3 USDT Reserve
- Target: `AaveProtocolDataProvider` (`0xBc9f5b7E248451CdD7cA54e717a2BFe1F32b566b`)
- Asset: `AAVE_V3_USDT` (`0x0a215D8ba66387DCA84B284D18c3B4ec3de6E54a`)
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-10-16T13:51:46Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "aave-v3",
  "token": "USDT",
  "pool_tvl_usd": "30370159.353394",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 1854,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 3111
}
```

### 3. Aave Stata USDC Vault
- Underlying: `AAVE_V3_USDC` via Aave V3 reserve
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-10-16T14:00:54Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "aave-stata",
  "token": "USDC",
  "pool_tvl_usd": "33531513.672478",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 2083,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 2565
}
```

### 4. Aave Stata USDT Vault
- Underlying: `AAVE_V3_USDT` via Aave V3 reserve
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-10-16T13:51:46Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "aave-stata",
  "token": "USDT",
  "pool_tvl_usd": "30370159.359755",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 1854,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 3114
}
```

### 5. Morpho Blue USDC/WBTC Market
- Target: `MORPHO_BLUE` (`0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`)
- Market ID: `0xa7cc9d96371f4c4b5be131281cd24a410cf6b76dcfb4319004d5bd66045adb97`
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-10-16T14:41:42Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "morpho-blue",
  "token": "USDC",
  "pool_tvl_usd": "30042.238549",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 7001,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 119
}
```

### 6. Morpho Blue USDC/cbETH Market
- Target: `MORPHO_BLUE` (`0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`)
- Market ID: `0x532165f57c1077690d6b2f413e5555eba34611df4e625820ca6aa187f3f89b65`
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-08-12T15:56:34Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "morpho-blue",
  "token": "USDC",
  "pool_tvl_usd": "75003.389394",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 9356,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 5611629
}
```

### 7. Uniswap V3 USDC/WETH Pool (fee 3000)
- Factory: `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24`
- Pool Address: `0x92266210facf124f190fbad79aa230dfb85926ce`
- Token Valuations: USDC valued at $1.00; WETH valued via AaveOracle
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-10-16T14:43:46Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "uniswap-v3",
  "token": "USDC",
  "pool_tvl_usd": "1598.01",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 0,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 0
}
```

### 8. Uniswap V3 USDC/WBTC Pool (fee 500)
- Factory: `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24`
- Pool Address: `0x80e1d0dc2ef7ca7b25548b0458234c3abbd1114d`
- Token Valuations: USDC valued at $1.00; WBTC valued via AaveOracle
- Result:
```json
{
  "schema_version": 1,
  "as_of": "2026-10-16T14:43:49Z",
  "sources": {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain"
  },
  "protocol": "uniswap-v3",
  "token": "USDC",
  "pool_tvl_usd": "2095.56",
  "reward_apy_share_bps": 0,
  "apy_deviation_bps": 0,
  "stablecoin_depeg_bps": 0,
  "utilization_bps": 0,
  "estimated_slippage_bps": null,
  "estimated_cost_usd": "0.40",
  "estimated_benefit_usd": "3.10",
  "data_age_seconds": 0
}
```
