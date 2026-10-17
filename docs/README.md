# Documentation Index

Complete directory of technical documentation, architectural specifications, security designs, and verification evidence for `agent-wallet`.

## 1. Product & Overview
- [`technical-documentation.md`](technical-documentation.md): Deliverable 01 covering system architecture, core algorithms, and security design.
- [`DEPLOYMENT.md`](DEPLOYMENT.md): Step-by-step deployment guide for local Docker, Docker Compose, Render, and Supabase.
- [`TESTING.md`](TESTING.md): Testing strategy, test runner commands, test layer specifications, and 1,376-test catalogue.
- [`competition/problem-description.md`](competition/problem-description.md): Official FinTechathon 2026 Topic F (Agent Wallet) problem description and requirements.

## 2. Architecture
- [`architecture/overview.md`](architecture/overview.md): High-level system architecture, trust boundaries, and execution pipeline.
- [`architecture/gateway.md`](architecture/gateway.md): Gateway trust boundary, simulation-before-signing, and execution gating.
- [`architecture/gateway-wiring.md`](architecture/gateway-wiring.md): Service wiring, tenant registry, and runtime dependency injection.
- [`architecture/policy-engine.md`](architecture/policy-engine.md): Pure deterministic policy engine implementation and rule evaluation order.
- [`architecture/ips-loader.md`](architecture/ips-loader.md): Immutable Investment Policy Statement loader and SHA-256 policy hashing.
- [`architecture/presets.md`](architecture/presets.md): Pre-configured strategy profiles (Conservative vs Diversified).
- [`architecture/protocols.md`](architecture/protocols.md): Central protocol registry mapping calldata builders, spenders, and positions.
- [`architecture/aave-adapter.md`](architecture/aave-adapter.md): Aave v3 supply and withdraw adapter on Base Sepolia.
- [`architecture/aave-stata.md`](architecture/aave-stata.md): Aave Stata ERC-4626 static yield-bearing vault integration.
- [`architecture/morpho-blue.md`](architecture/morpho-blue.md): Morpho Blue isolated lending markets for USDC/cbETH and USDC/WBTC.
- [`architecture/uniswap-v3.md`](architecture/uniswap-v3.md): Uniswap v3 token swap adapter with oracle-bounded slippage protection.
- [`architecture/risk-inputs.md`](architecture/risk-inputs.md): Risk router architecture aggregating live on-chain data and reference fallbacks.
- [`architecture/signer.md`](architecture/signer.md): Isolated local key signer restricted exclusively to Base Sepolia (chain ID 84532).
- [`architecture/custody-tenancy.md`](architecture/custody-tenancy.md): Multi-owner custody model, AES-256-GCM keystore encryption, and tenant isolation.
- [`architecture/storage.md`](architecture/storage.md): Dual storage backend design supporting SQLite and PostgreSQL with Row Level Security.
- [`architecture/tiers.md`](architecture/tiers.md): Permission state machine (T0 read-only, T1 propose-only, T2 autonomous execution).
- [`architecture/global-stop.md`](architecture/global-stop.md): Emergency stop semantics, challenge-response resumption, and fail-closed overrides.
- [`architecture/audit-log.md`](architecture/audit-log.md): Cryptographic SHA-256 hash-chained audit log specification and verifier.
- [`architecture/planner-reader.md`](architecture/planner-reader.md): Reader extraction of facts from untrusted text and Planner LLM order generation.
- [`architecture/runner.md`](architecture/runner.md): Autonomous agent loop cycle, facts extraction, and `no_action` audit recording.
- [`architecture/rpc-latency.md`](architecture/rpc-latency.md): JSON-RPC concurrency controls, keep-alive connections, and latency handling.
- [`architecture/contracts.md`](architecture/contracts.md): Data contract boundaries, JSON Schemas, and append-only rule codes.
- [`architecture/api.md`](architecture/api.md): REST API specification, RFC 7807 problem details, and session endpoints.
- [`architecture/auth.md`](architecture/auth.md): EIP-4361 Sign-In with Ethereum (SIWE), HttpOnly cookies, and step-up authorization.
- [`architecture/chat-sse.md`](architecture/chat-sse.md): Server-Sent Events (SSE) streaming architecture for real-time progress cards.
- [`architecture/chat-quota-voice.md`](architecture/chat-quota-voice.md): Chat inquiry rate limits, prompt quotas, and agent voice guidelines.
- [`architecture/chat-thread-lifecycle.md`](architecture/chat-thread-lifecycle.md): Conversation thread lifecycle, monotonic update tokens, and multi-thread state.
- [`architecture/ui.md`](architecture/ui.md): Static single-page webapp architecture, client views, and route controllers.
- [`architecture/webapp.md`](architecture/webapp.md): Webapp component architecture and state management.
- [`architecture/evals.md`](architecture/evals.md): Evaluation harness architecture, scenario replay driver, and coverage reporting.
- [`architecture/deployment.md`](architecture/deployment.md): Reference container deployment architecture and production topology.

## 3. Security & Risks
- [`security/self-assessment.md`](security/self-assessment.md): Deliverable 05 covering security self-assessment, threat model, and mitigations.

## 4. Design & Research
- [`design/webapp/ux-research.md`](design/webapp/ux-research.md): UX benchmarking against industry products (Safe, Coinbase, Revolut, etc.) [needs owner decision].
- [`design/architecture-draft.vi.md`](design/architecture-draft.vi.md): Initial brainstorming draft in Vietnamese [needs owner decision].
- [`design/contracts-proposal.md`](design/contracts-proposal.md): Early design proposal for data contracts and schemas.
- [`design/webapp/system-design.md`](design/webapp/system-design.md): Frontend system design, typography, spacing tokens, and screen layouts.
- [`design/webapp/product.md`](design/webapp/product.md): Product vision, user journeys, and problem framing for agent wallet.
- [`design/webapp/proposal.md`](design/webapp/proposal.md): Architectural proposal for multi-owner webapp interface.
- [`design/webapp/ui-contract.md`](design/webapp/ui-contract.md): Approved frontend contract specifying screen behaviors and route states.
- [`design/webapp/ui-v3-plan.md`](design/webapp/ui-v3-plan.md): Implementation plan and audit findings for UI v3 overhaul.
- [`design/webapp/v2-contract.md`](design/webapp/v2-contract.md): Webapp v2 route contracts and event specifications.
- [`design/webapp/chat-v3-spec.md`](design/webapp/chat-v3-spec.md): Technical specification for Chat v3 SSE event stream.
- [`design/webapp/icons.md`](design/webapp/icons.md): Asset provenance, licensing (CC0), and color contrast for token/protocol icons.
- [`design/runner/design.md`](design/runner/design.md): Detailed runner component design and state machine.
- [`design/runner/proposal.md`](design/runner/proposal.md): Initial proposal for autonomous background runner execution.
- [`design/evidence-risk-inputs.md`](design/evidence-risk-inputs.md): Analysis of on-chain risk data availability across testnet protocols.
- [`design/evidence-aave-stata.md`](design/evidence-aave-stata.md): Research on Aave Stata static aToken testnet deployment.
- [`design/evidence-morpho-blue.md`](design/evidence-morpho-blue.md): Research on Morpho Blue market availability on Base Sepolia.
- [`design/evidence-uniswap-swap.md`](design/evidence-uniswap-swap.md): Research on Uniswap v3 pool liquidity and swap execution on testnet.
- [`design/testnet-facts-aave.md`](design/testnet-facts-aave.md): Verified testnet contract addresses and parameters for Aave v3.
- [`design/testnet-facts-rpc.md`](design/testnet-facts-rpc.md): RPC simulation support and JSON-RPC latency benchmarks.
- [`design/testnet-facts-uniswap.md`](design/testnet-facts-uniswap.md): Pool liquidity verification and router addresses for Uniswap v3.
- [`design/testnet-facts-defi-survey.md`](design/testnet-facts-defi-survey.md): Survey of candidate DeFi lending and swap protocols on Base Sepolia.

## 5. Evidence & Verification
- [`evidence/tx_hashes.md`](evidence/tx_hashes.md): Real on-chain transaction hashes on Base Sepolia (Aave v3 USDC approve and supply).
- [`evidence/webapp-evidence.md`](evidence/webapp-evidence.md): Hosted webapp end-to-end session evidence (SIWE, top-up, activation, planner execution).
- [`evidence/defi-evidence.md`](evidence/defi-evidence.md): Multi-protocol hosted execution evidence (Aave Stata vaults, Morpho Blue, and hostile denial).
- [`evidence/runner-evidence.md`](evidence/runner-evidence.md): Runner autonomous execution evidence and hash-chained audit verification.
- [`evidence/eval-report.md`](evidence/eval-report.md): Output report from 46 offline evaluation scenarios covering 27/28 rule codes.

## 6. Submission
- [`submission/deck-and-video-outline.md`](submission/deck-and-video-outline.md): Working outline for submission items 02 (presentation deck) and 03 (demo video script).
- [`submission/02-presentation-deck.md`](submission/02-presentation-deck.md): Deliverable 02 presentation deck [managed by ticket S1].
- [`submission/03-demo-video-script.md`](submission/03-demo-video-script.md): Deliverable 03 demo video recording script [managed by ticket S1].
- [`submission/06-execution-evidence.md`](submission/06-execution-evidence.md): Deliverable 06 execution evidence summary [managed by ticket S1].
