# Execution Evidence Directory Guide

This directory contains on-chain receipts, audit logs, and evaluation reports verifying the operation of BaKhi Wallet on Base Sepolia (chain ID `84532`).

## Current vs Historical Evidence

- **Current evidence:** [`defi-evidence-loop.md`](defi-evidence-loop.md) and section 1b of [`06-execution-evidence.md`](../submission/06-execution-evidence.md) represent the current multi-protocol execution state.
- **Historical run:** [`defi-evidence.md`](defi-evidence.md) records the first multi-protocol run on the hosted application from 2026-10-16. Its preliminary "no order proposed" rows were resolved afterwards:
  - Morpho WBTC supply and protocol withdrawals were executed on 2026-10-18 (see [`defi-evidence-loop.md`](defi-evidence-loop.md)).
  - The Uniswap v3 swap was fixed by the pool-quote minimum-output change and executed on-chain.

## Evidence File Catalogue

- [`defi-evidence-loop.md`](defi-evidence-loop.md): Current multi-protocol chat session on the hosted app (13 transactions executed across Aave v3, Uniswap v3, Aave Stata, and Morpho Blue on 2026-10-18).
- [`defi-evidence.md`](defi-evidence.md): First multi-protocol hosted app run (2026-10-16) covering Aave Stata deposit and Morpho Blue utilization denial.
- [`tx_hashes.md`](tx_hashes.md): Initial Base Sepolia on-chain transactions (Aave v3 approve and supply from 2026-10-14).
- [`runner-evidence.md`](runner-evidence.md): Autonomous agent runner execution trace and hostile prompt injection policy denial.
- [`webapp-evidence.md`](webapp-evidence.md): Hosted webapp verification (Sign-In with Ethereum, native ETH top-up, and planner-proposed transactions).
- [`eval-report.md`](eval-report.md): Offline evaluation harness report (46 scenarios, 100% pass rate, covering rule catalog).
- [`eval-results.json`](eval-results.json): Structured JSON results of all 46 evaluation scenarios.
- [`audit-chain.jsonl`](audit-chain.jsonl): Initial SHA-256 hash-chained audit log records.
- [`audit-chain.verify.txt`](audit-chain.verify.txt): Verification proof output from `python -m core.audit.verify docs/evidence/audit-chain.jsonl`.
- [`runner-audit-chain.jsonl`](runner-audit-chain.jsonl): SHA-256 hash chain for runner session including benign and intercepted actions.
