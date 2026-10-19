# Presentation deck

Competition deliverable 02: the text of the deck, slide by slide. The deck file itself (`BaKhi-Wallet-deck.pptx`) is in the `02-presentation/` folder of the submission package. Each slide lists its on-slide text, the figures and their source, and the speaker notes with a time budget.

- **Length:** 12 slides plus a four-part appendix. Speaker notes total 518 seconds (8 minutes 38 seconds), inside the 10-minute limit.
- **Section bar on every slide:** Problem · Product · Security · Innovation · Evidence · Next.
- **Scoring criteria:** task completion (slides 4, 5, 10), security and compliance (slides 6, 7, 8, 11), innovation and interaction (slide 9).
- **Figures:** every number on a slide is listed with its source in appendix A4.

---

## Slide 1 - Cover
- Question: What is the product, who built it, and for which topic?
- Kicker: FinTechathon 2026
- Title: BaKhi Wallet
- Subtitle: Autonomous wallet agent operating within owner-defined policy limits
- Primary evidence: Product name with the locked tagline and the testnet label
- Blocks:
  - B1 Tagline: The LLM proposes; code decides.
  - B2 Context: International Track, Topic F Agent Wallet. Base Sepolia testnet only.
  - B3 Team: BaKhi
- Data: none
- Source line: FinTechathon 2026, International Track
- Speaker notes (15 s): Good day to the committee. We present BaKhi Wallet, an autonomous wallet agent that operates within limits defined by the asset owner. The system was built for Topic F, Agent Wallet, and runs on the Base Sepolia test network.

---

## Slide 2 - Problem
- Question: Why does delegating funds to an AI agent require limits that software enforces?
- Kicker: Problem
- Title: An AI agent that controls funds needs limits enforced outside the model
- Subtitle: Key-holding agents expose owners to injection, model error and unverifiable actions
- Primary evidence: Comparison diagram of direct key access and policy-gated access
- Blocks:
  - B1 Exposure of a key-holding agent:
    - Injected text can redirect funds in a single message
    - Model errors can change amounts or destinations
    - Past actions cannot be verified independently
  - B2 Owner requirements:
    - Limits written in advance and enforced by software
    - Authority widens only with the owner's signature
    - A complete record of every decision
- Data: none (qualitative argument; no industry statistic is cited because none was collected)
- Source line: Technical documentation, section 1
- Speaker notes (45 s): Language models now plan financial actions, and the common implementation gives the model an API credential or a signing key. That arrangement leaves the owner exposed in three respects. Text received by the model can redirect funds. The model can alter an amount or a destination through error. And the owner has no independent means of checking afterwards why an action took place. Asset owners therefore require limits written in advance and enforced by software, authority that widens only with their own signature, and a complete record of each decision. The remainder of this presentation describes how BaKhi Wallet meets those requirements.

---

## Slide 3 - Solution
- Question: How does the architecture implement separation of duties?
- Kicker: Architecture
- Title: Duties are separated so the model proposes and deterministic code decides
- Subtitle: Only the gateway can reach the signer, and continuous integration enforces this
- Primary evidence: Architecture diagram (owner policy, planner, gateway, policy engine, simulation, signer, audit log)
- Blocks:
  - B1 Flow: Owner policy → Planner (language model) → Gateway → Policy engine → Simulation → Signer → Audit log
  - B2 Properties:
    - The planner outputs structured orders, never keys
    - The policy engine is a pure function without network access
    - Every outcome is written to the audit log
- Data: none
- Source line: `docs/architecture/overview.md`; boundary test `tests/test_signer_boundary.py`
- Speaker notes (45 s): The design separates three duties. The planner, which is the language model, may only propose a structured order. The gateway receives that order, applies the owner's permission tier, asks the policy engine for a decision, simulates the transaction, and only then requests a signature. The signer is reachable from the gateway alone, and an automated test in continuous integration fails if any other module imports it. Every outcome, whether executed or denied, is appended to a hash-chained audit log.

---

## Slide 4 - Task completion: one request from end to end
- Question: Does a real instruction complete all the way to a confirmed transaction?
- Kicker: End to end
- Title: One chat instruction produced a confirmed swap through seven controlled steps
- Subtitle: Swap of 3 USDC to WBTC on Base Sepolia, executed on 18 October 2026
- Primary evidence: Seven-row table with the record produced at each step
- Blocks:
  - B1 Steps and records:
    - 1 Instruction: swap 3 USDC to WBTC, slippage at most 0.5 percent
    - 2 Order: Uniswap v3, USDC to WBTC, 3 USDC, 50 bps
    - 3 Policy: allow, tier T2, no rule triggered
    - 4 Simulation: passed before signing
    - 5 Execution: approval, then swap
    - 6 Confirmation: blocks 48282056 and 48282062, status 0x1
    - 7 Audit: record 83, hash-chained
- Data (identifiers, for the footer or notes): chat message "Swap 3 USDC to WBTC on Uniswap, with at most 0.5 percent slippage."; order_id 6cacaffe-f30a-40b0-b2c4-46b1a67111c5; order JSON: protocol uniswap-v3, function exactInputSingle, token USDC, token_out WBTC, amount 3000000 (6 decimals), max_slippage_bps 50, recipient self, proposed_by planner; decision: allow, reason "Order is inside every limit of the IPS.", rules_hit empty, tier T2; approval tx 0xec8d67e2d25cff95a45e0cd454d165e5b4e2e901baeac1769da5f73b8d3897f4, block 48282056, gas 29,136; swap tx 0xe5b0dae4156a7eff167de570c59445a31c62da02d9c3e6a293088095c894eda4, block 48282062, gas 125,085; audit record seq 83, record hash 0f036aaca1ba0fdf5b57639035fc2387f5f32ea5505a401c58adfc2a0ab74a5b, previous hash e52705950855fe296f6cb092566878156c4c3fab6fca4ac8e948534e96a29b19. The audit record stores the decision and the receipts, not the simulation output or the swap's minimum output value. Source: hosted audit chain, record 83; receipts queried 2026-10-19.
- Source line: Audit record 83 (hosted database); BaseScan transactions above
- Speaker notes (70 s): This slide follows one instruction through the whole system. The owner typed an ordinary sentence in the chat: swap three USDC to WBTC on Uniswap with at most half a percent slippage. The planner converted it into a structured order naming the protocol, the function, the two tokens, the amount and the slippage limit. The policy engine evaluated the order against the owner's limits and returned allow, with no rule triggered, under tier T2. The gateway simulated the transaction before signing. Two transactions followed: the token approval in block 48,282,056 and the swap in block 48,282,062, both with status one. The result was recorded as audit record 83, which links to the previous record by hash. The identifiers are listed in the notes of this slide and in the appendix.

---

## Slide 5 - Task completion: deployed product and protocols
- Question: Is the product deployed and how broad is the protocol coverage?
- Kicker: Product
- Title: The application is deployed and supports four DeFi protocols on Base Sepolia
- Subtitle: Owners instruct the agent in chat and review positions, activity and rules
- Primary evidence: Protocol coverage table, with a screenshot of the chat progress card (see `docs/previews/ui3-chat-progress-1280-light.png`)
- Blocks:
  - B1 Protocols:
    - Aave v3: supply and withdraw USDC and USDT
    - Aave yield vaults: deposit and withdraw
    - Morpho Blue: lend and withdraw in two markets
    - Uniswap v3: swap USDC with WETH or WBTC
  - B2 Application:
    - Sign-in with the owner's wallet
    - A separate agent address for each owner
    - Step-by-step progress during execution
- Data: protocol list from `contracts/presets/diversified.json` (aave-v3, aave-stata, morpho-blue, uniswap-v3) and `docs/submission/01-technical-documentation.md`
- Source line: Technical documentation, section 2; application at bakhi-wallet.eddiephung.me
- Speaker notes (40 s): The application is deployed and available at the address shown. Owners sign in with their own wallet, and each owner receives a separate agent address. Instructions are given in the chat, and the interface shows each step as it completes. Four protocols are supported on Base Sepolia: Aave version three for supply and withdrawal, Aave yield vaults, Morpho Blue lending in two markets, and Uniswap version three for swaps between USDC, WETH and WBTC.

---

## Slide 6 - Security: policy engine
- Question: How are orders evaluated before any signature is requested?
- Kicker: Policy engine
- Title: Every order passes a pure policy function before a signature is requested
- Subtitle: 31 rule codes in five families cover access, limits, allocation, risk and tiers
- Primary evidence: Rule family table
- Blocks:
  - B1 Rule families (code count):
    - Access, 8: allow-lists and exact-amount approvals
    - Limits, 3: per order, per day, per hour
    - Allocation, 3: asset weight and stablecoin share
    - Risk, 9: utilization, depeg, data age
    - Tiers, 8: permission tiers and stop
  - B2 Behavior:
    - The same inputs always produce the same decision
    - Missing or stale risk data results in denial
- Data: counts from `contracts/rule-codes.json` (ACCESS 8, LIMIT 3, ALLOC 3, RISK 9, TIER 8; total 31). Full list in appendix A2.
- Source line: `contracts/rule-codes.json`; `docs/architecture/core/policy-engine.md`
- Speaker notes (45 s): The policy engine is a pure function. Given an order, the owner's policy, the portfolio state and the risk inputs, it returns a decision and the list of rules that applied. It performs no network calls and does not modify its inputs. The catalogue contains thirty-one rule codes in five families: access, limits, allocation, risk and tiers. Risk inputs are read from the chain, and when data is missing or stale the order is denied. The complete list is in appendix A2.

---

## Slide 7 - Security: owner controls
- Question: How does the owner retain control over what the agent may do?
- Kicker: Owner controls
- Title: Owner controls apply least privilege, maker-checker and an audit trail
- Subtitle: Authority widens only through a signature bound to the exact action
- Primary evidence: Control matrix mapping each principle to its mechanism
- Blocks:
  - B1 Principle and mechanism:
    - Least privilege: allow-listed tokens, protocols and functions; exact-amount approvals
    - Maker-checker: agent proposes; orders above 100 USD await the owner
    - Authority changes: tier, stop release, withdrawal, preset need a step-up signature
    - Audit trail: hash-chained log including denied orders
  - B2 Limits of the diversified preset:
    - 100 USD per order, 300 USD per day
    - 5 transactions per hour
- Data: `contracts/presets/diversified.json`; tiers T0 read-only, T1 propose-only (reserved), T2 autonomous within policy; emergency stop overrides all tiers.
- Source line: Security self-assessment, section on permission tiers; `contracts/presets/diversified.json`
- Speaker notes (50 s): Owner control follows three established principles. Least privilege: the agent may only use the tokens, protocols and functions on the owner's list, and approvals are limited to the exact amount. Maker-checker: the agent proposes, and an order above one hundred US dollars waits for the owner's approval and is evaluated again when approved. Any action that widens authority, such as raising the tier, releasing the emergency stop, withdrawing or choosing a preset, requires a fresh signature that is bound to those exact parameters. The audit trail records every decision, including denials. The limits shown are those of the diversified preset used in the evidence.

---

## Slide 8 - Security: verification results
- Question: What evidence shows that the controls work as described?
- Kicker: Verification
- Title: Evaluation passes 48 of 48 scenarios, including 12 of 12 injection attempts
- Subtitle: Results are reproducible from the repository; the test suite has 1,523 tests
- Primary evidence: Scenario results by category
- Blocks:
  - B1 Scenarios passed by category:
    - Allow 3 of 3, deny 22 of 22, escalate 3 of 3
    - Injection 12 of 12, integrity 2 of 2
    - No action 2 of 2, tiers and stop 4 of 4
  - B2 Coverage:
    - 29 of 31 rule codes replayed
    - The other two are covered by unit tests
    - Command: python -m evals.run
- Data: appendix A3 (category table); test count 1,523 collected on 2026-10-19 (`python -m pytest --collect-only -q`). The catalogue has 31 codes; the evaluation replays 29 of them (see appendix A3).
- Source line: `docs/evidence/eval-report.md`; `docs/guides/testing.md`
- Speaker notes (40 s): The evaluation harness replays forty-eight scenarios offline, without network access or keys. All forty-eight pass. They include twenty-two denial cases, three escalations, and twelve adversarial instructions such as direct overrides, encoded payloads and look-alike characters; none of the twelve produces a transaction. Twenty-nine of the thirty-one rule codes are exercised; the other two are covered by unit tests. The repository also contains one thousand five hundred and twenty-three automated tests. The evaluation is reproduced with a single command.

---

## Slide 9 - Innovation and interaction
- Question: What is new in the design and how does the owner interact with it?
- Kicker: Innovation
- Title: Five implemented mechanisms distinguish the design from unrestricted key access
- Subtitle: Each mechanism has automated tests and is documented in the repository
- Primary evidence: Table of five mechanisms with the file or document that implements each
- Blocks:
  - B1 Mechanisms:
    - Owner policy is hashed and applied to every order
    - Planner chooses from lists, not addresses
    - Signatures are bound to exact action parameters
  - B2 Mechanisms (continued):
    - Audit chain includes denied orders
    - Order amounts are compared with the owner's message
  - B3 Interaction:
    - Decisions explained in plain language
    - Live progress and an emergency stop
- Data (source of each mechanism): owner policy and presets `contracts/`, `core/policy`; protocol registry `core/protocols` and planner schema `core/agent/planner`; step-up `api/auth.py`, `tests/webapp/test_stepup.py`; audit chain `core/audit`, `tests/audit`; amount check `core/agent/planner/amount_check.py` (added 2026-10-18); plain-language decisions `api/chat_compose.py`, interface in `ui/`.
- Source line: Repository paths listed above
- Speaker notes (50 s): Five mechanisms distinguish the design. First, the owner's policy is a document that is hashed and applied to every order. Second, the planner selects protocols, functions and tokens from fixed lists; target addresses and calldata come from code specifications. Third, signatures that widen authority are bound to the exact parameters of the action. Fourth, the audit chain records denied orders as well as executed ones. Fifth, the amount in a proposed order is compared with the amount in the owner's message; during testing a request for five thousand USDC produced an order for five, and this check now prevents such a mismatch. For the owner, each decision is explained in plain language, progress is shown step by step, and the emergency stop is always available.

---

## Slide 10 - On-chain evidence
- Question: Which transactions were confirmed, and which requests were stopped?
- Kicker: Evidence
- Title: 25 transactions are confirmed on Base Sepolia and unsafe requests were denied
- Subtitle: Receipts were queried from the public RPC endpoint and returned status 0x1
- Primary evidence: Table of confirmed transactions by protocol
- Blocks:
  - B1 Confirmed transactions by protocol:
    - Aave v3: 12
    - Aave yield vaults: 5
    - Morpho Blue: 5
    - Uniswap v3: 2
    - Native ETH top-up: 1
  - B2 Stopped before signing:
    - Morpho cbETH supply: risk data failed the freshness check (utilization 93.56%, ceiling 90%)
    - Hourly transaction limit reached: supply denied
    - Amount in order differed from message: no order proposed
- Data: counts derived from appendix A1 (10 transactions of 14 to 16 October and 15 of 18 October). Denial records: Morpho cbETH (`RISK.DATA_STALE`, 2026-10-16 and 2026-10-18, `docs/evidence/defi-evidence.md`, `docs/evidence/defi-evidence-loop.md`); `LIMIT.TX_PER_HOUR` denials (hosted audit table, 2026-10-18); amount check (2026-10-19 test). An instruction to send all USDC to a burn address produced no order.
- Source line: `docs/submission/06-execution-evidence.md`; `docs/evidence/defi-evidence-loop.md`; appendix A1
- Speaker notes (45 s): The evidence consists of twenty-five confirmed transactions on Base Sepolia across the four protocols, from Aave supply and withdrawal to Morpho lending and a Uniswap swap. Each receipt was queried from the public endpoint and returned status one. The slide also shows requests that were stopped. A supply to the Morpho cbETH market was denied because its risk data did not pass the freshness check; the same market showed utilization of 93.56 percent against a ninety percent ceiling. Orders were denied when the hourly transaction limit was reached. The complete table with hashes and blocks is in appendix A1.

---

## Slide 11 - Risks and roadmap
- Question: Which risks remain and how will they be reduced?
- Kicker: Risk assessment
- Title: Documented risks have mitigations and a planned path to non-custodial keys
- Subtitle: The self-assessment records 26 risks with severity, status and mitigation
- Primary evidence: Table of four selected risks with severity, status and mitigation
- Blocks:
  - B1 Selected risks (severity, status, mitigation):
    - K13 Server-held agent keys: high on mainnet; accepted for testnet funds only
    - K4 Reference prices: medium; accepted; missing prices fail closed
    - K6 Single RPC endpoint: medium; accepted; multi-provider quorum planned
    - K26 Allowance after denied supply: low; planned; one combined plan
  - B2 Roadmap (planned):
    - Smart-account session keys replace server-held keys
    - Multi-source oracles and multi-provider RPC
- Data: severity counts of the 26 risks: Low 12, Medium 9, High 5. Status counts: accepted 15, enforced 5, closed 4, partial 1, planned 1 (`docs/submission/05-security-self-assessment.md`).
- Source line: Security self-assessment, section 4
- Speaker notes (45 s): The security self-assessment records twenty-six risks. The most significant concerns custody: the server holds the agent keys, encrypted at rest, which is acceptable for a testnet with no real value and would be high severity on mainnet. Prices are reference values and missing prices cause denial. A single RPC endpoint is used. After a denied supply, an exact-amount allowance may remain from the earlier approval; the planned mitigation evaluates approval and supply as one plan. The roadmap replaces server-held keys with smart-account session keys and adds redundant data sources.

---

## Slide 12 - Conclusion
- Question: What should the committee take from the submission and where is the material?
- Kicker: Conclusion
- Title: BaKhi Wallet executes on-chain actions only within limits the owner defined
- Subtitle: The submission contains the application, source code, tests and evidence
- Primary evidence: Three-point summary with the resource links
- Blocks:
  - B1 Summary:
    - Duties are separated between model, policy engine and signer
    - 25 transactions confirmed; unsafe requests denied before signing
    - Evaluation and tests are reproducible from the repository
  - B2 Resources:
    - Application: bakhi-wallet.eddiephung.me
    - Source code: github.com/thaicuong576/agent-wallet
  - B3 Closing: Thank you for the committee's time.
- Data: none
- Source line: Submission package
- Speaker notes (28 s): In summary, BaKhi Wallet separates the duties of the model, the policy engine and the signer. It has executed twenty-five confirmed transactions on Base Sepolia and has denied unsafe requests before signing. The evaluation and the tests can be reproduced from the repository. The application and the source code are listed on this slide. Thank you for the committee's time and consideration.

---

# Appendix

## A1 - Confirmed transactions
| # | Date (UTC) | Protocol | Step | Transaction hash | Block | Status |
| ---: | :--- | :--- | :--- | :--- | ---: | :--- |
| 1 | 2026-10-14 10:16 | Aave v3 | approve 1 USDC | `0xba75953fca67a96112ec7f11bfeeb03235629e0cc7415033ddb7a6b244630221` | 48102358 | 0x1 |
| 2 | 2026-10-14 10:16 | Aave v3 | supply 1 USDC | `0x7c81b0ad32c8213081a0e142ee50e5de72f750ad23425c9267ecb81a0ea6f233` | 48102361 | 0x1 |
| 3 | 2026-10-14 15:41 | Aave v3 | approve 1 USDC | `0xa3f88092d802c22cd40569e9c488e082a3130221f9521f68ebdb8ddc264b4ac4` | 48112080 | 0x1 |
| 4 | 2026-10-14 15:41 | Aave v3 | supply 1 USDC | `0x05f4089567b5091b7f979214f4cf6115db2364c6839b3d374633a2e72e6a4cb5` | 48112088 | 0x1 |
| 5 | 2026-10-15 05:31 | Native ETH | top-up 0.0001 ETH | `0xf9db01704315836598e0496a7ee9233caf5aedc25ab16eec6002bbd9d3af0e9a` | 48137006 | 0x1 |
| 6 | 2026-10-15 05:31 | Aave v3 | approve 10 USDC | `0xe08022a78165c51f8b4c2b79142906b6e31439634993756582470adf6a4a0749` | 48137327 | 0x1 |
| 7 | 2026-10-15 05:31 | Aave v3 | supply 10 USDC | `0xcc49ca6da6435519fb15291b95887bbcd16f9934ce81076efff93836ed4044ff` | 48138889 | 0x1 |
| 8 | 2026-10-16 17:25 | Aave Stata | approve 5 USDC | `0xd10c778b9b496a0b185a01679a59466a957883f2b01619f683fe8c0b68ca7274` | 48201645 | 0x1 |
| 9 | 2026-10-16 17:25 | Aave Stata | supply 5 USDC | `0xa61e6dcae14132cd74efe19bdd7fb38f16e5d34e1a825625f3745383df960221` | 48201652 | 0x1 |
| 10 | 2026-10-16 17:25 | Morpho Blue | approve 5 USDC | `0x03dec0e246b5d355731020567f94a2aed72a11490bdd6a2060b44fe63c282ecc` | 48201694 | 0x1 |
| 11 | 2026-10-18 14:03 | Aave v3 | approve | `0xf5ae62c9926ebd4c3abca7b97352a30c90b85f8367b1f79f8e8fd7c6c73d053b` | 48281938 | 0x1 |
| 12 | 2026-10-18 14:03 | Aave v3 | supply | `0x4993ce41fe6a6920b512db4eee0c9296c44bec163f649c73bfb629e90b9f88af` | 48281944 | 0x1 |
| 13 | 2026-10-18 14:07 | Uniswap v3 | approve | `0xec8d67e2d25cff95a45e0cd454d165e5b4e2e901baeac1769da5f73b8d3897f4` | 48282056 | 0x1 |
| 14 | 2026-10-18 14:07 | Uniswap v3 | exactInputSingle | `0xe5b0dae4156a7eff167de570c59445a31c62da02d9c3e6a293088095c894eda4` | 48282062 | 0x1 |
| 15 | 2026-10-18 15:05 | Aave yield vault | approve | `0x5383a377de3ba5afa0f640919fe674d83309bad0fb7b1963f7310a1e18b819ad` | 48283800 | 0x1 |
| 16 | 2026-10-18 15:05 | Aave yield vault | supply | `0x532d86e74e21e375e298466636907410f0c1fd5aebc75c88fd393c900e3522cb` | 48283806 | 0x1 |
| 17 | 2026-10-18 15:09 | Morpho Blue | approve | `0xa9fc45898923248c3fc0222404141e7ce851a2c75daa5699145b4ed01a78c869` | 48283918 | 0x1 |
| 18 | 2026-10-18 15:09 | Morpho Blue | supply | `0x20ef510012788bd10dd604a20cb004daf945381a3f14693ff2e3db9b47fc213b` | 48283924 | 0x1 |
| 19 | 2026-10-18 16:07 | Morpho Blue | approve | `0xba8db017f6701277df3196ff4a8d4f7cbfc5bd6ffb64c18b4851d5cac1b42859` | 48285661 | 0x1 |
| 20 | 2026-10-18 16:08 | Aave yield vault | withdraw | `0x99f787c3d43d5628eec02cc68cb9ab7449c90f4104245c24bf9ea69440f41824` | 48285705 | 0x1 |
| 21 | 2026-10-18 16:11 | Morpho Blue | withdraw | `0x3f15f2f249897a8caf14f9816c8e4db92cda0e6128f4d72e55a54ad326c9012f` | 48285778 | 0x1 |
| 22 | 2026-10-18 16:13 | Aave v3 | approve | `0x6441a52257b965c82b4e10ea3543faa208d35fb503150db9f41becc4a22a2228` | 48285851 | 0x1 |
| 23 | 2026-10-18 16:13 | Aave v3 | supply | `0x6b74774cd3e1e5042a4f0f04a6894643711041d7664a404acf583f5509da756f` | 48285856 | 0x1 |
| 24 | 2026-10-18 17:43 | Aave v3 | approve | `0x3c99e2eda7308b9f7c6c21eb3c0c2e0a674aaef44d39c657de956c2c6859fca7` | 48288549 | 0x1 |
| 25 | 2026-10-18 17:43 | Aave v3 | supply | `0x7cb88acefd428d085a0b5a7ba14de97a442752f54deb1637ebef243aa4fd21f8` | 48288554 | 0x1 |


## A2 - Rule codes and policy parameters

Rule codes by family (31):

| Family | Count | Codes |
| :--- | ---: | :--- |
| ACCESS | 8 | `ACCESS.TOKEN_NOT_ALLOWED`, `ACCESS.PROTOCOL_NOT_ALLOWED`, `ACCESS.FUNCTION_NOT_ALLOWED`, `ACCESS.RECIPIENT_NOT_ALLOWED`, `ACCESS.UNLIMITED_APPROVAL`, `ACCESS.OWNER_ONLY_FUNCTION`, `ACCESS.PROTOCOL_NOT_ENABLED`, `ACCESS.MARKET_NOT_ALLOWED` |
| LIMIT | 3 | `LIMIT.PER_TX`, `LIMIT.PER_DAY`, `LIMIT.TX_PER_HOUR` |
| ALLOC | 3 | `ALLOC.MAX_WEIGHT_PER_ASSET`, `ALLOC.MIN_STABLE_WEIGHT`, `ALLOC.WITHIN_REBALANCE_BAND` |
| RISK | 9 | `RISK.POOL_TVL_TOO_LOW`, `RISK.REWARD_APY_SHARE_TOO_HIGH`, `RISK.APY_UNSTABLE`, `RISK.DEPEG_TOO_WIDE`, `RISK.UTILIZATION_TOO_HIGH`, `RISK.SLIPPAGE_TOO_HIGH`, `RISK.COST_EXCEEDS_BENEFIT`, `RISK.DATA_STALE`, `RISK.PRICE_UNAVAILABLE` |
| TIER | 8 | `TIER.READ_ONLY`, `TIER.PROPOSE_ONLY`, `TIER.EMERGENCY_STOP`, `TIER.EMERGENCY_STOP_RELEASED`, `TIER.APPROVAL_REQUIRED`, `TIER.APPROVAL_REJECTED`, `TIER.GLOBAL_STOP`, `TIER.POLICY_CHANGED` |
| Total | 31 | |


Preset "diversified" (`contracts/presets/diversified.json`):

| Parameter | Value |
| :--- | :--- |
| Tokens | USDC, USDT, WBTC, WETH |
| Protocols | aave-v3, aave-stata, morpho-blue, uniswap-v3 |
| Functions | approve, supply, withdraw, exactInputSingle |
| Recipients | self, aave_pool, uniswap_router |
| Maximum weight per asset | 20% |
| Minimum stablecoin weight | 60% |
| Rebalance band | 5% |
| Minimum pool TVL | 1,000 USD |
| Maximum depeg | 50 bps |
| Maximum slippage | 100 bps |
| Maximum reward share of APY | 50% |
| Maximum APY deviation | 300 bps |
| Maximum utilization | 90% |
| Minimum benefit-cost ratio | 2.0 |
| Maximum risk-data age | 7,200 s |
| Per order | 100 USD |
| Per day | 300 USD |
| Transactions per hour | 5 |
| Owner approval above | 100 USD |


## A3 - Evaluation scenarios
| Category | Scenarios | Passed | Focus |
| :--- | ---: | ---: | :--- |
| allow | 3 | 3 | In-policy orders execute |
| deny | 22 | 22 | Limits, allocation, risk, unlisted tokens, unregistered protocols and markets |
| escalate | 3 | 3 | Orders above threshold wait for the owner |
| injection | 12 | 12 | Adversarial instructions produce no transaction |
| integrity | 2 | 2 | Tampered records and replayed orders detected |
| no_action | 2 | 2 | Orders inside the rebalance band skipped |
| tier_and_stop | 4 | 4 | Read-only tier, propose-only tier, stop and release |
| Total | 48 | 48 | |

Environment: offline replay mode, no network, no private keys. Command: `python -m evals.run`. Report: `docs/evidence/eval-report.md`. Rule-code coverage: 29 of 31 codes. Not replayed: `TIER.POLICY_CHANGED` (a status code reserved for preset changes) and `RISK.PRICE_UNAVAILABLE` (raised by the Uniswap v3 builder, covered by `tests/protocols/test_uniswap_v3.py`).


## A4 - Sources and reproduction
| Figure | Value | Source | Date |
| :--- | :--- | :--- | :--- |
| Automated tests | 1,523 collected | `python -m pytest --collect-only -q` | 2026-10-19 |
| Evaluation scenarios | 48 of 48 passed | `docs/evidence/eval-report.md`, `python -m evals.run` | 2026-10-19 |
| Rule codes in catalogue | 31 | `contracts/rule-codes.json` | 2026-10-19 |
| Confirmed transactions | 25 | `docs/submission/06-execution-evidence.md`, `docs/evidence/defi-evidence-loop.md` | 2026-10-18 |
| Receipt status | 0x1 for all 25 | `eth_getTransactionReceipt` on `https://sepolia.base.org` | 2026-10-18 and 2026-10-19 |
| Owner limits | 100 USD per order, 300 USD per day, 5 per hour | `contracts/presets/diversified.json` | 2026-10-19 |
| Morpho cbETH supply | denied with `RISK.DATA_STALE`; utilization 93.56% against a 90% ceiling | `docs/evidence/defi-evidence.md` | 2026-10-16 |
| Known risks | 26 (K1 to K26) | `docs/submission/05-security-self-assessment.md` | 2026-10-19 |
| Audit chain | verified, 104 records, no bad sequence | `GET /audit/verify` on the hosted application (`docs/submission/06-execution-evidence.md`, section 2) | 2026-10-18 |

Incidents recorded in `docs/evidence/defi-evidence-loop.md`: a request for 5,000 USDC produced an order for 5 USDC before the amount check was added (inside the owner's limits; corrected afterwards and re-tested); the swap initially failed simulation because the minimum output used the oracle price (corrected).


---
