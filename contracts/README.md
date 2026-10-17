# Data contracts v1

One language-neutral definition of every object that crosses a module boundary in agent-wallet: the policy engine, the gateway, the audit log, the API and the minimal UI all build against these files. Documents only; there is no product code here.

Where they sit and how they relate: [`docs/architecture/core/contracts.md`](../docs/architecture/core/contracts.md).

## Layout
| Path | What |
| :--- | :--- |
| `schemas/<name>.schema.json` | 8 JSON Schemas, draft 2020-12, each self-contained and each with a required integer `schema_version` |
| `openapi.yaml` | OpenAPI 3.1 for every endpoint; `$ref`s the schemas; errors are RFC 7807 `application/problem+json` |
| `rule-codes.json` | Stable policy rule codes shared by the policy engine, eval and UI |
| `examples/` | Valid and invalid examples per schema, plus `decision-record.chain.json` |

## The 8 contracts
| Schema | Owner module (future) | Purpose |
| :--- | :--- | :--- |
| `order` | Planner proposes, gateway receives | A proposed action. Enum values and amounts only: never an address, never free text |
| `portfolio-state` | Gateway | Wallet snapshot: positions, weights, 24h spend, tx count |
| `risk-inputs` | Risk scoring | Measured inputs for the risk rules |
| `ips` | Owner (out of band) | Investment Policy Statement. Read-only for the agent; no endpoint writes it |
| `decision` | Policy engine | `allow`, `deny` or `escalate`, with `rules_hit` and a human-readable `reason` |
| `execution-result` | Gateway | Outcome of an executed order: tx hash or error code |
| `decision-record` | Audit log | One append-only, hash-chained record per state change |
| `tier-state` | Owner | Tier (`T0`, `T1`, `T2`) and the emergency stop flag |

`Decision` (policy result) is separate from `Outcome` (final status: `no_action`, `denied`, `pending_approval`, `executed`, `failed`). A policy `deny` is a normal 200 response with a record, not an HTTP error.

## How to validate
```bash
python - <<'PY'
import glob, json
from jsonschema import Draft202012Validator as V
for p in sorted(glob.glob("contracts/schemas/*.schema.json")):
    V.check_schema(json.load(open(p)))
print("schemas ok")
PY
```
Every `<name>.valid*.json` must validate against `schemas/<name>.schema.json`, every `<name>.invalid.*.json` must fail it, and `rule-codes.json` must be consistent with the examples. JSON Schema cannot express the following, so they are checked in code and by the audit-log tests: `order_id` equals `order.order_id` and `decision.order_id`; `ips_hash` on the record equals the one on the decision; `hash` and `prev_hash` link up (below).

## Conventions
- **Money.** Token amounts are `{ "value": "<integer string>", "decimals": <int> }` in the smallest unit; `USDC` is 6 decimals, while `WETH` and `ETH` are 18, enforced per order. USD values are decimal strings (`"150.00"`). Never a float anywhere.
- **Owner-only functions and recipient owner.** Functions `mint` and `transfer` are owner functions. The planner (agent) origin can never use these functions (proposals with non-owner origin are denied with `ACCESS.OWNER_ONLY_FUNCTION`). Enforcement is handled by the policy engine and gateway, not within JSON Schema. The recipient `owner` is resolved server-side to the owner's session address; orders never contain raw Ethereum addresses.
- **No mint amount cap.** The contract schema and rule codes enforce no mint amount cap (no `LIMIT.MINT_PER_CALL` or `LIMIT.MINT_PER_DAY`). On-chain Aave faucet limits (1,000,000 USDC per call and 1-hour cooldown) and server-side rate limits prevent abuse.
- **Ratios are integer basis points** (10000 = 100%). The draft architecture's sample IPS maps like this:

  | Draft sample (`ips` section 6) | Contract field | Value |
  | :--- | :--- | :--- |
  | `max_weight_per_asset: 0.40` | `allocation.max_weight_per_asset_bps` | `4000` |
  | `min_stable_weight: 0.60` | `allocation.min_stable_weight_bps` | `6000` |
  | `rebalance_band: 0.05` | `allocation.rebalance_band_bps` | `500` |
  | `max_reward_apy_share: 0.5` | `risk.max_reward_apy_share_bps` | `5000` |
  | `min_pool_tvl_usd: 50000000` | `risk.min_pool_tvl_usd` | `"50000000"` |
  | `ips_version: 1` | `schema_version` | `1` |

  Four risk thresholds that draft section 7 names but its sample omits are required fields too, so a rule is never silently skipped: `max_apy_deviation_bps`, `max_utilization_bps`, `min_benefit_cost_ratio_bps`, `max_data_age_seconds`.
- **Illustrative values.** Every threshold and price in `examples/` is illustrative, not a recommendation. The owner sets real values in the IPS.
- **One version field.** Every object has an integer `schema_version`, including the IPS. The IPS revision is identified by its hash (`ips_hash` = SHA-256 of the IPS's canonical JSON), not by a second counter.
- **Order ids** are client-generated lowercase UUIDs. The same id never executes twice: a replay of the same body returns the stored record, a different body is a 409.
- **Tiers.** The enum is `T0`, `T1`, `T2`. `T1` (propose-only, user signs) is in the enum for forward compatibility; no v1 code path uses it. "Needs approval" (T3 in older notes) is the rule `TIER.APPROVAL_REQUIRED`, driven by `approval.escalate_above_usd`, not a tier value.
- **Pending approval is append-only.** An escalated order gets a record with outcome `pending_approval`. `POST /orders/{id}/approve` or `/reject` appends a new record for the same `order_id` with an `approval` object (`status` approved or rejected, `by` owner, `at`). A rejection is outcome `denied` with rule `TIER.APPROVAL_REJECTED`.
- **No-action records.** Outcome `no_action` has no order: `order_id`, `order`, `decision` are null and `no_action` holds `rules_hit` and `reason`. The emergency stop and its release append such records (`TIER.EMERGENCY_STOP`, `TIER.EMERGENCY_STOP_RELEASED`); neither is a violation.
- **Timestamps** are UTC RFC 3339 with a trailing `Z`.
- **Placeholders.** Addresses, tx hashes and ids in examples are made-up placeholders.

## Rule codes
`rule-codes.json` is an array of `{code, family, description, source}`. A code is a single string `<FAMILY>.<NAME>` matching `[A-Z]+\.[A-Z0-9_]+`; `family` is redundant and must equal the prefix. Families: `ACCESS`, `LIMIT`, `ALLOC`, `RISK`, `TIER`. Codes are append-only: never rename, reuse or delete one. Thresholds are never in this file; they are IPS values.

## Audit record hash
```python
import hashlib, json
def canonical(o):  # sorted keys, compact separators, UTF-8, nulls kept
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
def record_hash(r):  # hash covers prev_hash, excludes the hash field
    return hashlib.sha256(canonical({k: v for k, v in r.items() if k != "hash"})).hexdigest()
```
The first record's `prev_hash` is 64 zeros. `seq` starts at 0 and is assigned by the log, never by the caller. `examples/decision-record.chain.json` holds a denied order, a pending order and the same order executed after approval.

## Embedded copies
Schemas are self-contained so any JSON Schema library validates them with no registry. The objects `Order`, `Decision` and `ExecutionResult` (and the helper `$defs` they use) are therefore copied into `decision-record.schema.json`. The copies must stay byte-identical in canonical JSON; the validation step compares them. Change them in every file together.

## Versioning and changing a contract
- `schema_version` is `1` for all of v1. A change that existing valid data still satisfies (a new optional field, a new rule code, a new enum value that consumers may ignore) keeps `1` and adds examples.
  - **Additive additions (no bump):** Adds enum values to `order.schema.json` and its embedded copy in `decision-record.schema.json`: functions `mint` and `transfer`, recipient `owner`, token `ETH`, protocol `wallet`, along with rule codes `ACCESS.OWNER_ONLY_FUNCTION`, `TIER.GLOBAL_STOP`, and `TIER.POLICY_CHANGED`. Because existing valid orders, stored decision records, and audit chains remain completely valid, `schema_version` remains `1`.
- Any other change (a removed or renamed field, a tighter pattern, a new required field, a meaning change) is a new `schema_version` and a new set of examples. Consumers must reject a `schema_version` they do not know.
- Changes to `ips`, `tier-state` or `decision-record` define permissions and the audit boundary: they are high risk and go to the owner.
- A change comes with: schema, OpenAPI if exposed, examples (one valid, one invalid), this README and `docs/architecture/core/contracts.md` if relations changed.

