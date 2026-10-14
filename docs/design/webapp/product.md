# Product: agent-wallet webapp

Status: framing for ticket #71 (owner addendum 1). Companion to [`system-design.md`](system-design.md). Testnet only (Base Sepolia, chain 84532). Not financial advice.

## 1. Who it is for
An **individual who holds stablecoins** and wants an agent to manage them: park idle USDC in a lending market, keep inside limits, and never wake up to a surprise. They are comfortable with a browser wallet (MetaMask) but do not want to run scripts, and they do not want to hand a bot their private key.

They are not traders chasing yield, not institutions, and not developers (developers have the CLI, the API and the replay mode).

## 2. Value proposition against "give a bot a private key"

| Question | A bot with your key | agent-wallet |
| :--- | :--- | :--- |
| What can it do? | Anything the key can do | Only what your policy (IPS) lists: tokens, protocols, functions, limits |
| Who decides each action? | The bot, or an LLM | A deterministic policy engine; the LLM only proposes |
| What if the model is tricked by hostile text? | The key signs | The order is denied and the reason is written down in plain language |
| Can you stop it? | Revoke and hope | One click: emergency stop denies every order, including already approved ones |
| Can you prove what happened? | Logs the bot wrote | A hash-chained audit log anyone can verify |
| Can you get your money out? | Move it yourself | Owner-signed withdrawal to your own address (Stage 1) |
| Where is the agent key? | Wherever you pasted it | v1: a separate, server-held testnet key that never holds your own wallet's key. Production path: a non-custodial smart account with a scoped session key |

One honest sentence for the page: *the agent can only do what you allowed, and everything it does can be checked afterwards.* v1 is custodial testnet software; the page says so on every screen. State lives in Postgres when hosted (Render free web service plus Supabase) and in SQLite when run locally or in replay mode.

## 3. The three core journeys

### J1 Onboard (about 3 minutes)
1. **Connect**: connect an injected wallet, sign a login message (free, no gas).
2. **Fund**: the page shows the agent address; send gas ETH from your wallet (your wallet asks you to confirm); press "Get test USDC" and the agent mints it through the gateway.
3. **Set policy**: pick a preset (Conservative, Balanced) described in plain language; sign to confirm.
4. **Run**: press Run once and see the result.

### J2 Delegate
Set the tier (Read only, Propose only, Execute inside policy), choose the approval threshold through the preset, press **Run agent**. See what was proposed, what the policy decided and why, and the transaction on BaseScan. Orders above the threshold wait for you.

### J3 Supervise
Open the page any day: positions and allocation, today's spend against the limit, a feed of every decision in plain language, pending approvals, the emergency stop always visible, and a "verify the audit chain" button. Withdraw funds to your wallet when you are done (Stage 1).

## 4. v1 scope
In (Stage 0 and Stage 1 of the design): wallet sign-in, one agent wallet per owner, gas top-up, test-USDC mint, two or three presets, Run with the LLM planner and a labelled fallback, live portfolio from chain, decision feed, approve and reject, emergency stop, audit verification, owner-signed withdrawal, per-owner isolation, rate limits, replay mode for judges.

## 5. Explicitly out of scope of v1
- Mainnet, real funds, any claim of financial advice, tax or legal reporting.
- Non-custodial smart accounts and session keys (described as Stage 2 roadmap only).
- More than Aave V3 supply and withdraw; swaps (the testnet pool has no liquidity).
- Free-form policy editing: an owner can select a preset, not write an IPS body.
- Our own cap on test-USDC minting: the Aave faucet's on-chain maximum and cooldown apply; we only rate-limit calls per owner per hour.
- Scheduled or autonomous background running: a person presses Run (a scheduler is Stage 2).
- Notifications (email, push), billing, teams, mobile apps, WalletConnect as a requirement (it is optional).
- Smart-contract wallets as owners (EOAs only; EIP-1271 is Stage 2).
- Cross-chain, fiat on-ramps, any third-party custody.

## 6. What "done" looks like for a judge
A five-minute path, either live (hosted demo: the first request after idle can take about a minute on Render's free tier) or from recorded evidence: connect, fund, mint, run with a real transaction link, see the portfolio, paste a hostile note and see it blocked with the rule in words, stop the agent, verify the chain. Anything that is a reference value (prices) or scripted (offline planner) is labelled as such.
