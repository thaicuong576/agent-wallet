# Policy engine: access, limit, allocation, risk-input, and owner-function rules

Owner: `core.policy` (`core/policy/engine.py`). A pure evaluation function `evaluate(ips, state, order=None, risk_inputs=None, now=None, origin="planner", tier="T2", emergency_stop=False, global_stop=False) -> dict` that inspects proposed orders against the Investment Policy Statement (IPS) and current portfolio state, producing a strictly validated `Decision`.

## Where the policy engine sits

```mermaid
flowchart TD
  IN([Order In]) --> GS{Global Stop active?}
  GS -->|Yes| D_GS[Deny: TIER.GLOBAL_STOP]
  GS -->|No| OF{Owner Function: mint/transfer?}
  
  OF -->|Yes| ORG{origin == owner?}
  ORG -->|No| D_OWN[Deny: ACCESS.OWNER_ONLY_FUNCTION]
  ORG -->|Yes| TIER_CHECK{Stop or Tier restricted?}
  TIER_CHECK -->|transfer: allowed under stop/T0/T1| A_OWN[Allow: Bypass limits & escalate]
  TIER_CHECK -->|mint under stop: TIER.EMERGENCY_STOP| D_STOP[Deny: TIER.EMERGENCY_STOP]
  TIER_CHECK -->|mint under T0: TIER.READ_ONLY| D_T0[Deny: TIER.READ_ONLY]
  TIER_CHECK -->|mint under T1: TIER.PROPOSE_ONLY| D_T1[Deny: TIER.PROPOSE_ONLY]
  TIER_CHECK -->|mint normal: T2| A_OWN
  
  OF -->|No: planner order| UNIF{In Universe tokens/protocols/funcs/recips?}
  UNIF -->|No| D_UNIF[Deny: ACCESS.*]
  UNIF -->|Yes| LIM{Spend Limits / Limits per hour?}
  LIM -->|Exceeded| D_LIM[Deny: LIMIT.*]
  LIM -->|OK| ALLOC{Allocation & Risk Checks}
  ALLOC -->|Violated| D_RISK[Deny: ALLOC.* / RISK.*]
  ALLOC -->|Passed| A_PLAN[Allow / Escalate / No Action]
```

The policy engine is completely stateless and deterministic. It performs no network requests, executes no filesystem operations, invokes no LLM, and never mutates its input parameters.

## Interface

| Component | Signature / Return | Description |
| :--- | :--- | :--- |
| `evaluate` | `(ips: LoadedIps \| Mapping[str, Any], state: Mapping[str, Any], order: Mapping[str, Any] \| None = None, risk_inputs: Mapping[str, Any] \| None = None, now: datetime \| None = None, origin: str = "planner", tier: str = "T2", emergency_stop: bool = False, global_stop: bool = False) -> dict[str, Any]` | Evaluates access, limit, allocation, risk-input, and owner-function rules and returns a `Decision` dictionary validating against `contracts/schemas/decision.schema.json` (or no-action record payload). |

## Implemented rules

| Rule code | Family | Condition / Threshold | Evidence keys |
| :--- | :--- | :--- | :--- |
| `ACCESS.OWNER_ONLY_FUNCTION` | `ACCESS` | Order `function` is an owner-only function (`mint`, `transfer`) but `origin != "owner"`. | `function`, `origin` |
| `ACCESS.TOKEN_NOT_ALLOWED` | `ACCESS` | Order `token` or `token_out` is not in `ips.universe.tokens` (or token not allowed for owner function). | `token` or `token_out` |
| `ACCESS.PROTOCOL_NOT_ALLOWED` | `ACCESS` | Order `protocol` is not in `ips.universe.protocols`. | `protocol` |
| `ACCESS.FUNCTION_NOT_ALLOWED` | `ACCESS` | Order `function` is not in `ips.universe.functions`. | `function` |
| `ACCESS.RECIPIENT_NOT_ALLOWED` | `ACCESS` | Order `recipient` is not in `ips.universe.recipients` (or recipient not allowed for owner function). | `recipient` |
| `ACCESS.UNLIMITED_APPROVAL` | `ACCESS` | Approve order with `amount >= 2**256 - 1` or exceeding order's own supply/withdraw amount. | `amount`, `max_uint256`, `supply_amount`, `withdraw_amount` |
| `LIMIT.PER_TX` | `LIMIT` | Order USD value exceeds `limits.per_tx_usd`, or token price is missing in portfolio positions. | `order_usd`, `per_tx_usd` (or `price_missing`, `token`) |
| `LIMIT.PER_DAY` | `LIMIT` | `state.spent_24h_usd` plus order USD value exceeds `limits.per_day_usd`. | `order_usd`, `spent_24h_usd`, `per_day_usd`, `projected_24h_usd` |
| `LIMIT.TX_PER_HOUR` | `LIMIT` | `state.tx_count_1h` is already at or above `limits.max_tx_per_hour`. | `tx_count_1h`, `max_tx_per_hour` |
| `ALLOC.MAX_WEIGHT_PER_ASSET` | `ALLOC` | After the order, a non-stable asset's portfolio weight exceeds `allocation.max_weight_per_asset_bps`. | `token`, `weight_bps`, `max_weight_per_asset_bps` |
| `ALLOC.MIN_STABLE_WEIGHT` | `ALLOC` | After the order, aggregate stablecoin weight falls below `allocation.min_stable_weight_bps`. | `stable_weight_bps`, `min_stable_weight_bps` |
| `ALLOC.WITHIN_REBALANCE_BAND` | `ALLOC` | Current allocation deviation $\Delta \le \text{rebalance\_band\_bps}$. Yields `no_action` rather than a violation denial. | `deviation_bps`, `rebalance_band_bps` |
| `RISK.POOL_TVL_TOO_LOW` | `RISK` | Pool TVL is below `risk.min_pool_tvl_usd`. | `pool_tvl_usd`, `min_pool_tvl_usd` |
| `RISK.REWARD_APY_SHARE_TOO_HIGH` | `RISK` | Share of total APY coming from reward tokens exceeds `risk.max_reward_apy_share_bps`. | `reward_share_bps`, `max_reward_share_bps` |
| `RISK.APY_UNSTABLE` | `RISK` | APY 7d/30d deviation exceeds `risk.max_apy_deviation_bps`. | `apy_deviation_bps`, `max_apy_deviation_bps` |
| `RISK.DEPEG_TOO_WIDE` | `RISK` | Stablecoin price depeg from 1.00 USD exceeds `risk.max_depeg_bps`. | `depeg_bps`, `max_depeg_bps` |
| `RISK.UTILIZATION_TOO_HIGH` | `RISK` | Lending pool utilization exceeds `risk.max_utilization_bps`. | `utilization_bps`, `max_utilization_bps` |
| `RISK.SLIPPAGE_TOO_HIGH` | `RISK` | Estimated or specified slippage exceeds `risk.max_slippage_bps`. | `estimated_slippage_bps` / `order_slippage_bps`, `max_slippage_bps` |
| `RISK.COST_EXCEEDS_BENEFIT` | `RISK` | Estimated benefit/cost multiple is below `risk.min_benefit_cost_ratio_bps`. | `actual_ratio_bps`, `estimated_benefit_usd`, `estimated_cost_usd`, `min_benefit_cost_ratio_bps` |
| `RISK.DATA_STALE` | `RISK` | Risk inputs are missing (`risk_inputs=None`) or data age exceeds `risk.max_data_age_seconds`. | `data_age_seconds`, `max_data_age_seconds` (or `risk_inputs: "missing"`) |
| `TIER.GLOBAL_STOP` | `TIER` | Global stop is active (`global_stop=True` or `state.global_stop=True`). Denies all operations. | `global_stop` |
| `TIER.READ_ONLY` | `TIER` | Tier T0 active; non-rescue operations like `mint` are denied. | `tier` |
| `TIER.PROPOSE_ONLY` | `TIER` | Tier T1 active; operations like `mint` are denied. | `tier` |
| `TIER.EMERGENCY_STOP` | `TIER` | Emergency stop active; operations like `mint` are denied. | `emergency_stop` |

## Weight calculation and rebalance formulas

All allocation calculations use exact `decimal.Decimal` and integer basis points (10000 bps = 100%).

1. **Post-order asset values**:
   - For swaps (`exactInputSingle`):
     $$V_{\text{post}}(t_{\text{in}}) = V_{\text{current}}(t_{\text{in}}) - O_{\text{usd}}$$
     $$V_{\text{post}}(t_{\text{out}}) = V_{\text{current}}(t_{\text{out}}) + O_{\text{usd}}$$
     Total portfolio USD value $V_{\text{total}}$ remains unchanged.
   - For supplies (`supply`):
     $$V_{\text{post}}(t) = V_{\text{current}}(t) + O_{\text{usd}}$$
     $$V_{\text{total, post}} = V_{\text{total}} + O_{\text{usd}}$$
   - For withdrawals (`withdraw`):
     $$V_{\text{post}}(t) = \max(0, V_{\text{current}}(t) - O_{\text{usd}})$$
     $$V_{\text{total, post}} = \max(0, V_{\text{total}} - O_{\text{usd}})$$

2. **Post-order asset weight in basis points**:
   $$W(t) = \lfloor \frac{V_{\text{post}}(t) \times 10000}{V_{\text{total, post}}} \rfloor$$

3. **Aggregate stablecoin weight in basis points**:
   $$W_{\text{stable}} = \lfloor \frac{\sum_{t \in \text{STABLE}} V_{\text{post}}(t) \times 10000}{V_{\text{total, post}}} \rfloor$$
   where $\text{STABLE} = \{\text{USDC}, \text{USDT}, \text{DAI}\}$.

4. **Rebalance deviation**:
   Current allocation deviation $\Delta$ is computed across stable and non-stable assets:
   $$\Delta_{\text{stable}} = \max(0, \text{min\_stable\_weight\_bps} - W_{\text{stable, current}})$$
   $$\Delta(t) = \max(0, W_{\text{current}}(t) - \text{max\_weight\_per\_asset\_bps}) \quad \forall t \notin \text{STABLE}$$
   $$\Delta = \max\left(\Delta_{\text{stable}}, \max_{t \notin \text{STABLE}} \Delta(t)\right)$$

   If $\Delta \le \text{rebalance\_band\_bps}$, the state is within the rebalance band and `ALLOC.WITHIN_REBALANCE_BAND` triggers a `no_action` outcome.

## Owner functions and emergency bypass

Per design section 7.3, the owner can propose two direct utility and rescue functions:
1. `mint`: Testnet faucet utility pinning token to `USDC`, recipient to `self`, and protocol to `aave-v3`. Has no amount cap. Denied under emergency stop (`TIER.EMERGENCY_STOP`), T0 (`TIER.READ_ONLY`), T1 (`TIER.PROPOSE_ONLY`), and global stop (`TIER.GLOBAL_STOP`).
2. `transfer`: Owner fund recovery pinning recipient to `owner` (resolved server-side), protocol to `wallet`, and token to `USDC` or `ETH`. Serves as the owner's emergency escape hatch and is allowed under emergency stop, T0, and T1, but denied under global stop (`TIER.GLOBAL_STOP`).

Both owner functions:
- Require `origin == "owner"`; any other origin denies with `ACCESS.OWNER_ONLY_FUNCTION`.
- Bypass spend limits (`per_tx_usd`, `per_day_usd`), `escalate_above_usd`, hourly tx limits, and all allocation/risk-input checks.
- Are excluded from 24h spend calculations in the audit ledger, while counting towards executed tx limits.

## Invariants and guarantees

1. **Purity and determinism**: For identical inputs and timestamp, `evaluate` always returns identical decision dictionaries. Network sockets and file open calls raise without affecting execution.
2. **Zero mutation**: Neither `ips`, `state`, `order`, nor `risk_inputs` is modified in any way.
3. **No floats**: All monetary math and ratios use `decimal.Decimal` and `int`, ensuring exact precision and canonical JSON serialization compatibility.
4. **Comprehensive evaluation**: `evaluate` evaluates all rules across families without early exits, populating all violations in `rules_hit`.
5. **Fail closed**: Orders requiring risk inputs (supply, withdraw, swap) deny with `RISK.DATA_STALE` when `risk_inputs=None`.
6. **Contract compliance**: Every decision output satisfies Draft 2020-12 schema validation against `contracts/schemas/decision.schema.json`.

## Proving checks

| Claim | Proving check |
| :--- | :--- |
| Every ACCESS and LIMIT rule has passing and blocking test cases | `tests/policy/test_access_rules.py`, `tests/policy/test_limit_rules.py` |
| Every ALLOC rule has passing and blocking test cases | `tests/policy/test_alloc_rules.py` |
| Every RISK rule has passing and blocking test cases | `tests/policy/test_risk_rules.py` |
| Owner functions mint and transfer satisfy all tier, origin, and bypass rules | `tests/policy/test_owner_functions.py` |
| Multiple simultaneous violations all appear in `rules_hit` | `tests/policy/test_multiple_violations.py` |
| Execution is pure, offline, and does not mutate inputs | `tests/policy/test_engine_purity.py` |
| Outputs validate against `decision.schema.json` | `tests/policy/test_schema_compliance.py` |
| Independent check matches policy suite | `pytest tests/policy -q` (100 passed) |
