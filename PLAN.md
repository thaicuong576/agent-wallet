# Plan: agent-wallet (FinTechathon 2026, Topic F)

To-do items live in this repo's GitHub Issues. This file holds only the goal, scope, phases and risks. Change it through a PR; a merge by the owner counts as approval.

Hierarchy: **master plan (this file) → phase (GitHub Milestone) → sprint (Project iteration) → spec (issue labelled `spec`, tickets as sub-issues)**.

## Goal
Submit all 6 required items for Topic F (Agent Wallet) before **20 Oct 2026, 23:59 Beijing time (22:59 Vietnam time)**: an AI wallet agent that acts only inside an Investment Policy Statement (IPS) set by the wallet owner, with real transaction hashes on Base Sepolia and an eval suite that proves the agent complies.

## Scoring map
| Criterion | Weight | What earns it | Specs |
| :--- | :--- | :--- | :--- |
| Task completion | 40% | Asset management, execution and risk control all work end to end, with tx hashes | S1, S4, S5, S6 |
| Security and compliance | 30% | Deterministic policy engine, permission tiers, tamper-evident audit log, injection defence, self-assessment | S2, S3, S5, S8 |
| Innovation and interaction | 30% | User-set policy enforced as code, explained decisions, eval-backed claims | S2, S5, S6, S7 |
| On-chain bonus | 0 to 5 | Rule enforcement at wallet or contract layer | cut first |

## Scope
- In:
  - Pure policy engine (allow, deny, escalate), hashed IPS, tiers T0, T1 (reserved) and T2, an approval rule above a threshold (`TIER.APPROVAL_REQUIRED`, with approve and reject), and an emergency stop that can be released.
  - Append-only audit log with a hash chain.
  - Research and risk scoring computed in code (DefiLlama, contract reads over RPC).
  - Planner LLM (OpenAI) and an isolated Reader LLM; prompt-injection defence.
  - Gateway: propose, policy, simulate, sign. Only the gateway may call the signer.
  - Aave V3 supply and withdraw on Base Sepolia.
  - 3-layer eval harness with JSON and HTML report.
  - FastAPI as the single contract; a minimal UI that only uses it.
  - Submission pack: architecture, security design, self-assessment, tx evidence, deck, video.
- Cut order, first to last, when behind schedule:
  1. On-chain bonus (ERC-7715, Safe session keys).
  2. Polished UI (Claude Design or AI Studio); keep the minimal UI.
  3. User-signed wallet (T1): describe only.
  4. CDP Server Wallet: use a plain wallet, key in `.env`, testnet only.
  5. Reader LLM: replace with a pure allowlist check.
  - Uniswap swap is dropped if the testnet pool has no liquidity.
- Never cut: policy engine tests, eval suite, tx hashes, security self-assessment.

## Definition of done (every ticket)
- Independent check passes (read-only command run by the orchestrator).
- Tests added and passing; CI green.
- `README.md` updated; PR has a Mermaid diagram; a feature has `docs/architecture/<feature>.md`; data flow or schema changes have an ERD; UI changes have a committed preview image.
- No secret in the diff; `.env` stays ignored.

## Phases and sprints
Today is 14 Oct; 6 days remain. One sprint per phase. Up to 3 workers run in parallel (slots are set during `/fleet-setup`).

| Phase | Sprint | Done when | Specs |
| :--- | :--- | :--- | :--- |
| P0 Foundations | 14–15 Oct | Research tickets answered (RPC simulation, Aave address, Uniswap liquidity); scaffold merged; funded testnet wallet; **first tx hash** through the gateway | S1 |
| P1 Safe core | 16–17 Oct | Policy engine and tier tests pass 100%; audit log detects a tampered line; gateway blocks a violating order | S2, S3 |
| P2 Intelligence and proof | 17–18 Oct | Risk scoring, Planner and Reader, eval harness produce a report; all 4 decision branches have a test | S4, S5, S6 |
| P3 Package | 19–20 Oct | Minimal UI, README, deploy guide, architecture and security docs, tx evidence, deck, video, repo public, submitted | S7, S8 |

Critical path: scaffold → gateway → policy engine → eval harness → submission pack. Risk scoring, Planner/Reader and the UI hang off it and can run in parallel once the gateway interface is fixed.

## Specs
| ID | Capability | Phase |
| :--- | :--- | :--- |
| S1 | Gateway and execution (Aave supply and withdraw) | P0 |
| S2 | Policy engine, IPS, permission tiers | P1 |
| S3 | Audit log (append-only, hash chain) | P1 |
| S4 | Research and risk scoring | P2 |
| S5 | Planner, Reader, injection defence | P2 |
| S6 | Eval harness and report | P2 |
| S7 | API contract and minimal UI | P3 |
| S8 | Submission pack | P3 |

Full specs are written for P0 and P1 first; P2 and P3 specs are written after P1 finishes.

## Decisions
| Date | Decision | Why | Revisit |
| :--- | :--- | :--- | :--- |
| 14 Oct | Core stack is Python 3.11 (FastAPI, web3.py, pytest, SQLite) | Matches the approved blueprint; a Cloudflare Workers spike showed warm requests at 3 to 9 ms CPU but cold requests at 30 to 125 ms, too thin a margin on the free plan | Only if the Workers Paid plan is taken |
| 14 Oct | Hosting is **not decided**; candidates are Render or Railway (also Azure from the student pack) | Hosting is optional for judging and does not block the core; the scaffold keeps the host swappable (Dockerfile, env-only config, `/health`, storage behind an interface) | Phase P3, tickets S7 and S8 |

## Risks and open questions
| Risk | Effect | Owner | Check by |
| :--- | :--- | :--- | :--- |
| Team has one member; rules require 2 to 5 and at least 60% non-Chinese nationals | Registration invalid | Owner | 14 Oct |
| Repo is private | Judges cannot open the link | Owner | 19 Oct (switch to public, scan history for secrets) |
| Base Sepolia RPC may not support `eth_simulateV1` | Simulation falls back to `eth_call` or an anvil fork | Research ticket | 15 Oct |
| No liquidity in the Uniswap testnet pool | Swap dropped | Research ticket | 15 Oct |
| Aave V3 Pool address on Base Sepolia not yet verified from primary docs | Wrong contract | Research ticket | 15 Oct |
| Market claims about agent wallets come from secondary sources | Unreliable citations | Research ticket | 18 Oct |
| Full Competition Rules not read yet (fintechathon.g-ican.com) | Missed requirement | Owner | 15 Oct |
