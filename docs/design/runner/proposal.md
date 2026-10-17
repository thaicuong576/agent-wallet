# Runner design: Phase A proposal (ticket #48)

Status: draft for orchestrator advice. Phase B (`design.md`) follows after `advice-1.md`.
Risk label: `rủi-ro-cao` (the work touches the gateway and the path to the signer).

## Question and constraints
Compose Reader -> Planner -> RiskProvider -> Gateway into a runner, and close four gaps that make the safety claims weaker than they look: cosmetic daily and hourly limits, in-memory idempotency, two TierStores that do not share state, and a tx builder that only exists inside `scripts/evidence/live_first_tx.py`.

Constraints taken from the code:
- Only `core.gateway` may import `core.signer`; the runner must not import `core.signer` or `core.tiers.owner` (it gets a `TierView` and the gateway).
- Table `audit_records` is append-only (triggers), has `order_id` and `outcome` columns but no index on `order_id`. `SqliteAuditSink.append()` does `SELECT last` then `INSERT` under the default Python sqlite3 isolation, so the SELECT is outside a transaction: two processes can read the same last `seq` and one fails on the primary key (loud, not silent, but an error path today).
- `portfolio-state.schema.json` accepts `price_source = mainnet_reference` only, tokens `USDC` and `WETH` only, locations `wallet` and `aave-v3`. aUSDC is therefore `USDC` at location `aave-v3`; native ETH has no slot.
- `execution-result.error_code` has no code for "tx build failed", and `Gateway._execute_order` does not catch a `tx_builder` exception, so a builder error today raises with no audit record.
- `tx_builder(order)` is called once per order and returns all calls. `full_tx` reads the `pending` nonce for each call independently, so an order that yields two calls would give both the same nonce. The script avoids it only because approve and supply are two separate orders.
- Only a record with `outcome = executed` moved funds.

## Item 3: `spent_24h_usd` and `tx_count_1h` from the audit log

What counts (same for all options): only records with `outcome = executed`. `pending_approval`, `denied` and `failed` did not move funds and never count.
Spend is the USDC value of executed `supply`, `withdraw` and swap orders; `approve` is excluded (it moves nothing and would double count with the supply that follows it). `tx_count_1h` counts executed records, so an approve counts there. The window is rolling, `created_at > now - 24h` and `> now - 1h`, using the injected `now`, not the wall clock, so replay is deterministic. USD value is recomputed from the stored order amount with the same reference price table the portfolio reader labels (`mainnet_reference`); no schema change.

| Option | How | Trade-off |
| :--- | :--- | :--- |
| A. Query `audit_records` (new read-only `AuditLedger.spent_since / count_since` over `outcome='executed'` and `created_at`, parse `payload`) | One source of truth, the same rows the verifier checks, no new table | Parses JSON per row (fine at this volume); wants an index on `created_at` and `order_id`; race-free only if propose is serialised (below) |
| B. Separate `ledger` table written on execute | Fast, simple SQL | A second source of truth that can disagree with the chain; mutable, not covered by the hash chain |
| C. Caller passes state (today) | None | The cosmetic limits we are fixing |

Recommendation: **A**. `PortfolioReader` receives an `AuditLedger` and fills the two fields; the caller can no longer set them.

Race between two concurrent proposes: both read `spent_24h = 0`, both are allowed, together they exceed the limit. It is real because the API (`POST /orders`) and the runner are separate processes.

| Option | Trade-off |
| :--- | :--- |
| 1. In-process lock | Does not cover the API process |
| 2. Serialise the whole propose (state read, policy, simulate, sign, append) under one cross-process lock | Proposes become serial; acceptable for one wallet and seconds per tx |
| 3. Optimistic re-check after append | Useless: funds already moved |

Recommendation: **2**, inside the gateway, as a `BEGIN IMMEDIATE` held on a dedicated connection to the audit database file, so every caller (API, runner, scripts) is covered with stdlib `sqlite3`. `SqliteAuditSink.append` also moves to explicit `BEGIN IMMEDIATE` so it is correct on its own.

## Item 4: idempotency backed by the audit log

Today `Gateway._orders` is a dict, so after a restart the same `order_id` executes again. `_in_flight` (signed but not recorded) is memory only too: a crash between `sign_and_send` and `append` loses it and a retry would sign a second time.

| Option | How | Trade-off |
| :--- | :--- | :--- |
| A. Audit lookup only: `find_by_order_id` (latest record for that id) replaces `_orders` | Smallest change, one store | Does not cover the crash window between sign and append |
| B. A + an `order_intents` table in the audit DB file (written before signing: `order_id`, `body_hash`, `state`; closed by the matching record). On startup an intent with no `executed` or `failed` record means "reconcile": propose returns a recorded `failed` record and never re-signs | Closes the crash window; the table sits outside the chain so append-only triggers stay intact | One more table; reconcile needs an operator or a receipt check |
| C. Nonce as idempotency key | Chain-native | Still needs a store; awkward for approve plus supply |

Behaviour in every option: lookup by `order_id`; same body returns the stored record; different body raises `OrderIdConflict`; a `failed` record is terminal (a retry needs a new `order_id`, because a failed order may have partially signed); `pending_approval` and stop-denied records keep today's re-evaluation rules. Recommendation: **B**, the only option where "the same `order_id` never executes twice" holds across a crash, which is the acceptance claim.

## Item 5: one TierStore file shared by API and runner

| Option | How | Trade-off |
| :--- | :--- | :--- |
| A. Shared SQLite file, path from `AGENT_WALLET_DATA_DIR` through one helper; `TierStore` sets WAL and `busy_timeout`; the gateway already reads tier and stop fresh on every propose | Works across processes with stdlib; a stop is visible on the next propose | `TierStore` has no WAL or busy timeout today, so it needs both (small change in `core.tiers`, flagged as touching a permission-tier module) |
| B. Runner is an HTTP client of the API | Single writer | The runner still needs a gateway for offline demo; adds a hop and an outage mode |
| C. Runner runs inside the API process | One connection | Scripts and offline replay cannot run without the server; coupled lifecycles |

Recommendation: **A**. Acceptance e2e test: two `TierStore` instances on one temp file; `OwnerControls` behind the API test client engages stop; a runner built on a `TierView` of the other instance gets `denied` with `TIER.EMERGENCY_STOP` on its next `run_once`, with no signer call.

## Preview of the rest (Phase B)
- Layout `core/runner/`: `portfolio.py`, `ledger.py`, `runner.py`, `config.py`. The tx builder goes to `core/gateway/txbuilder.py` (gateway side). `TierView` gets a public `get_approval(order_id)` and the gateway drops the `_store` fallback.
- Tx builder: nonces allocated sequentially for a multi-call order; `pending` nonce checked against `latest` and failed closed if `pending < latest` or the head block is too old; no gas fallback in live mode; builder errors caught in the gateway and recorded as `failed`.
- Prices: fixed `mainnet_reference` table (USDC 1.00, WETH a labelled constant) because testnet prices are not meaningful and it is the only `price_source` the schema allows.

## Open questions (recommended answer each)
1. Count on-chain-reverted `failed` orders toward `tx_count_1h`? Recommend no, they are not `executed`.
2. New `error_code` for tx build failure? Recommend reuse `SIMULATION_FAILED` with `detail` prefixed `tx build:`, no contract change.
3. Show native ETH in portfolio state? Recommend no: report it outside `PortfolioState` and use it only for the builder's gas check, because the schema has no ETH token.

DONE-48-Q4HT
