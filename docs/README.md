# Documentation Index

Complete directory of technical documentation, architectural specifications, security designs, and verification evidence for `agent-wallet`.

## Overview and guides
- [`README.md`](README.md): Documentation index grouped by reader.
- [`guides/deployment.md`](guides/deployment.md): Step-by-step deployment guide for local Docker, Docker Compose, Render, and Supabase, including container topology.
- [`guides/testing.md`](guides/testing.md): Testing strategy, test runner commands, test layer specifications, and 1,376-test catalogue.

## Architecture
- [`architecture/overview.md`](architecture/overview.md): High-level system architecture, trust boundaries, execution pipeline, and repository map.

### Architecture: Core
- [`architecture/core/gateway.md`](architecture/core/gateway.md): Gateway trust boundary, simulation-before-signing, and execution gating.
- [`architecture/core/gateway-wiring.md`](architecture/core/gateway-wiring.md): Service wiring, tenant registry, and runtime dependency injection.
- [`architecture/core/policy-engine.md`](architecture/core/policy-engine.md): Pure deterministic policy engine implementation and rule evaluation order.
- [`architecture/core/ips-loader.md`](architecture/core/ips-loader.md): Immutable Investment Policy Statement loader and SHA-256 policy hashing.
- [`architecture/core/presets.md`](architecture/core/presets.md): Pre-configured strategy profiles (Conservative vs Diversified).
- [`architecture/core/signer.md`](architecture/core/signer.md): Isolated local key signer restricted exclusively to Base Sepolia (chain ID 84532).
- [`architecture/core/tiers.md`](architecture/core/tiers.md): Permission state machine (T0 read-only, T1 propose-only, T2 autonomous execution).
- [`architecture/core/global-stop.md`](architecture/core/global-stop.md): Emergency stop semantics, challenge-response resumption, and fail-closed overrides.
- [`architecture/core/audit-log.md`](architecture/core/audit-log.md): Cryptographic SHA-256 hash-chained audit log specification and verifier.
- [`architecture/core/contracts.md`](architecture/core/contracts.md): Data contract boundaries, JSON Schemas, and append-only rule codes.

### Architecture: Agent
- [`architecture/agent/planner-reader.md`](architecture/agent/planner-reader.md): Reader extraction of facts from untrusted text and Planner LLM order generation.
- [`architecture/agent/runner.md`](architecture/agent/runner.md): Autonomous agent loop cycle, facts extraction, and `no_action` audit recording.
- [`architecture/agent/risk-inputs.md`](architecture/agent/risk-inputs.md): Risk router architecture aggregating live on-chain data and reference fallbacks.
- [`architecture/agent/evals.md`](architecture/agent/evals.md): Evaluation harness architecture, scenario replay driver, and coverage reporting.

### Architecture: Protocols
- [`architecture/protocols/protocols.md`](architecture/protocols/protocols.md): Central protocol registry mapping calldata builders, spenders, and positions.
- [`architecture/protocols/aave-adapter.md`](architecture/protocols/aave-adapter.md): Aave v3 supply and withdraw adapter on Base Sepolia.
- [`architecture/protocols/aave-stata.md`](architecture/protocols/aave-stata.md): Aave Stata ERC-4626 static yield-bearing vault integration.
- [`architecture/protocols/morpho-blue.md`](architecture/protocols/morpho-blue.md): Morpho Blue isolated lending markets for USDC/cbETH and USDC/WBTC.
- [`architecture/protocols/uniswap-v3.md`](architecture/protocols/uniswap-v3.md): Uniswap v3 token swap adapter with oracle-bounded slippage protection.
- [`architecture/protocols/rpc-latency.md`](architecture/protocols/rpc-latency.md): JSON-RPC concurrency controls, keep-alive connections, and latency handling.

### Architecture: Webapp
- [`architecture/webapp/api.md`](architecture/webapp/api.md): REST API specification, RFC 7807 problem details, and session endpoints.
- [`architecture/webapp/auth.md`](architecture/webapp/auth.md): EIP-4361 Sign-In with Ethereum (SIWE), HttpOnly cookies, and step-up authorization.
- [`architecture/webapp/custody-tenancy.md`](architecture/webapp/custody-tenancy.md): Multi-owner custody model, AES-256-GCM keystore encryption, and tenant isolation.
- [`architecture/webapp/storage.md`](architecture/webapp/storage.md): Dual storage backend design supporting SQLite and PostgreSQL with Row Level Security.
- [`architecture/webapp/webapp.md`](architecture/webapp/webapp.md): Webapp component architecture and state management.
- [`architecture/webapp/ui.md`](architecture/webapp/ui.md): Static single-page webapp architecture, client views, and route controllers.
- [`architecture/webapp/chat-sse.md`](architecture/webapp/chat-sse.md): Server-Sent Events (SSE) streaming architecture for real-time progress cards.
- [`architecture/webapp/chat-quota-voice.md`](architecture/webapp/chat-quota-voice.md): Chat inquiry rate limits, prompt quotas, and agent voice guidelines.
- [`architecture/webapp/chat-thread-lifecycle.md`](architecture/webapp/chat-thread-lifecycle.md): Conversation thread lifecycle, monotonic update tokens, and multi-thread state.
- [`architecture/webapp/timing-logs.md`](architecture/webapp/timing-logs.md): Latency visibility, structured timing instrumentation, and UI cache control architecture.

## Design
- [`design/contracts-proposal.md`](design/contracts-proposal.md): Early design proposal for data contracts and schemas.
- [`design/runner/design.md`](design/runner/design.md): Detailed runner component design and state machine.
- [`design/runner/proposal.md`](design/runner/proposal.md): Initial proposal for autonomous background runner execution.
- [`design/webapp/chat-v3-spec.md`](design/webapp/chat-v3-spec.md): Technical specification for Chat v3 SSE event stream.
- [`design/webapp/icons.md`](design/webapp/icons.md): Asset provenance, licensing (CC0), and color contrast for token/protocol icons.
- [`design/webapp/product.md`](design/webapp/product.md): Product vision, user journeys, and problem framing for agent wallet.
- [`design/webapp/proposal.md`](design/webapp/proposal.md): Architectural proposal for multi-owner webapp interface.
- [`design/webapp/system-design.md`](design/webapp/system-design.md): Frontend system design, typography, spacing tokens, and screen layouts.
- [`design/webapp/ui-contract.md`](design/webapp/ui-contract.md): Approved frontend contract specifying screen behaviors and route states.
- [`design/webapp/ui-v3-plan.md`](design/webapp/ui-v3-plan.md): Implementation plan and audit findings for UI v3 overhaul.
- [`design/webapp/v2-contract.md`](design/webapp/v2-contract.md): Webapp v2 route contracts and event specifications.

## Research
- [`research/evidence-aave-stata.md`](research/evidence-aave-stata.md): Research on Aave Stata static aToken testnet deployment.
- [`research/evidence-morpho-blue.md`](research/evidence-morpho-blue.md): Research on Morpho Blue market availability on Base Sepolia.
- [`research/evidence-risk-inputs.md`](research/evidence-risk-inputs.md): Analysis of on-chain risk data availability across testnet protocols.
- [`research/evidence-uniswap-swap.md`](research/evidence-uniswap-swap.md): Research on Uniswap v3 pool liquidity and swap execution on testnet.
- [`research/testnet-facts-aave.md`](research/testnet-facts-aave.md): Verified testnet contract addresses and parameters for Aave v3.
- [`research/testnet-facts-defi-survey.md`](research/testnet-facts-defi-survey.md): Survey of candidate DeFi lending and swap protocols on Base Sepolia.
- [`research/testnet-facts-rpc.md`](research/testnet-facts-rpc.md): RPC simulation support and JSON-RPC latency benchmarks.
- [`research/testnet-facts-uniswap.md`](research/testnet-facts-uniswap.md): Pool liquidity verification and router addresses for Uniswap v3.

## Evidence
- [`evidence/defi-evidence.md`](evidence/defi-evidence.md): Multi-protocol hosted execution evidence (Aave Stata vaults, Morpho Blue, and hostile denial).
- [`evidence/eval-report.md`](evidence/eval-report.md): Output report from 46 offline evaluation scenarios covering 27/28 rule codes.
- [`evidence/runner-evidence.md`](evidence/runner-evidence.md): Runner autonomous execution evidence and hash-chained audit verification.
- [`evidence/tx_hashes.md`](evidence/tx_hashes.md): Real on-chain transaction hashes on Base Sepolia (Aave v3 USDC approve and supply).
- [`evidence/webapp-evidence.md`](evidence/webapp-evidence.md): Hosted webapp end-to-end session evidence (SIWE, top-up, activation, planner execution).

## Submission
- [`submission/01-technical-documentation.md`](submission/01-technical-documentation.md): Deliverable 01 covering system architecture, core algorithms, and security design.
- [`submission/02-presentation-deck.md`](submission/02-presentation-deck.md): Deliverable 02 presentation deck with slide-by-slide visuals and speaker scripts.
- [`submission/03-demo-video-script.md`](submission/03-demo-video-script.md): Deliverable 03 demo video recording script and walkthrough plan.
- [`submission/05-security-self-assessment.md`](submission/05-security-self-assessment.md): Deliverable 05 covering security self-assessment, threat model, and mitigations.
- [`submission/06-execution-evidence.md`](submission/06-execution-evidence.md): Deliverable 06 execution evidence summary with verified on-chain transactions.

## Competition
- [`competition/problem-description.md`](competition/problem-description.md): Official FinTechathon 2026 Topic F (Agent Wallet) problem description and requirements.
