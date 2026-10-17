# Webapp evidence (Base Sepolia, chain 84532)

Generated 2026-10-15T05:31:23Z by `scripts/evidence/webapp_evidence.py --execute` against `https://agent-wallet-6vuy.onrender.com`.

The owner is a throwaway testnet wallet held by the script; the server created and holds a separate custodial agent key for it. Prices are mainnet reference values, not market prices.

Owner: `0x9D1Ae26890c683a52e73A504091b090f7689373a`  Agent: `0xc18afDC2dF55772bA3c031Df98305F02a3a36493`

| Step | Result |
| :--- | :--- |
| health | HTTP 200 |
| sign-in | owner 0x9D1Ae26890c683a52e73A504091b090f7689373a signed the server message; session set (HttpOnly cookie) |
| agent wallet | 0xc18afDC2dF55772bA3c031Df98305F02a3a36493 created by the server for this owner (custodial, testnet) |
| agent balances | {"eth_wei": "64472587999899", "gas_floor_wei": "5000000000000"} |
| top-up | [0xf9db01704315836598e0496a7ee9233caf5aedc25ab16eec6002bbd9d3af0e9a](https://sepolia.basescan.org/tx/0xf9db01704315836598e0496a7ee9233caf5aedc25ab16eec6002bbd9d3af0e9a) status 0x1, registered HTTP 202 |
| activate agent | PUT /tier T2 with step-up signature: HTTP 200 |
| mint job | {"note": "agent already holds 100000000 USDC units (faucet: 1 mint/hour)", "status": "skipped"} |
| run job | {"error_code": null, "kind": "run", "order_ids": [], "planner_client": "openai", "status": "succeeded"} |
| hostile run | {"error_code": null, "kind": "run", "order_ids": ["38f8e8c0-a5da-47c4-9848-ab46185e5b78"], "planner_client": "scripted", "status": "succeeded"} |
| audit verify | {"first_bad_seq": null, "ok": true, "records": 13} |

## Transactions sent by the agent


Session message signed at sign-in:

```
agent-wallet wants you to sign in with your Ethereum account.
Domain: agent-wallet-6vuy.onrender.com
Address: 0x9d1ae26890c683a52e73a504091b090f7689373a
Statement: Testnet only. This signature does not move funds.
Chain ID: 84532
Nonce: 193e0c7c1aff91291a58c01fa49293d86ad0652e
Issued At: 2026-10-15T05:31:26Z
Expiration Time: 2026-10-15T05:36:26Z
```

/me: `{"address": "0x9d1ae26890c683a52e73a504091b090f7689373a", "agent_address": "0xc18afDC2dF55772bA3c031Df98305F02a3a36493", "chain_id": 84532, "emergency_stop": false, "mode": "multi", "tier": "T2"}`

## Planner-proposed transactions on the hosted app (2026-10-15)

Agent `0xc18afDC2dF55772bA3c031Df98305F02a3a36493`, planner client `openai` (job records, audit seq 15 and 17, chain verified server-side). The model proposed each order from an owner note; the policy engine allowed it ("inside every limit of the IPS"), the gateway simulated, signed and recorded it.

| Step | Transaction |
| :--- | :--- |
| approve 10 USDC for the Aave pool (note: allowance not set, approve first) | [0xe08022a78165c51f8b4c2b79142906b6e31439634993756582470adf6a4a0749](https://sepolia.basescan.org/tx/0xe08022a78165c51f8b4c2b79142906b6e31439634993756582470adf6a4a0749) |
| supply 10 USDC to Aave (note: propose a supply of exactly 10 USDC) | [0xcc49ca6da6435519fb15291b95887bbcd16f9934ce81076efff93836ed4044ff](https://sepolia.basescan.org/tx/0xcc49ca6da6435519fb15291b95887bbcd16f9934ce81076efff93836ed4044ff) |

Honest notes from the same session: a first supply attempt without an allowance failed simulation (`SIMULATION_FAILED`, nothing signed); one run answered `no_action`; an earlier run was rejected because the model returned a malformed `order_id` (fixed in #113). The model's output varies between runs.
