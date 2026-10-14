# Transaction evidence (Base Sepolia, chain 84532)

| Block time (UTC) | Step | Order id | Tx hash | Block | Explorer |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 2026-10-14T10:16:44Z | approve 1 USDC | 5f80e4f3-93f9-4842-986d-70e084ebef65 | `0xba75953fca67a96112ec7f11bfeeb03235629e0cc7415033ddb7a6b244630221` | 48102358 | https://sepolia.basescan.org/tx/0xba75953fca67a96112ec7f11bfeeb03235629e0cc7415033ddb7a6b244630221 |
| 2026-10-14T10:16:50Z | supply 1 USDC | 856f33f7-9005-4890-9af0-824e9204110c | `0x7c81b0ad32c8213081a0e142ee50e5de72f750ad23425c9267ecb81a0ea6f233` | 48102361 | https://sepolia.basescan.org/tx/0x7c81b0ad32c8213081a0e142ee50e5de72f750ad23425c9267ecb81a0ea6f233 |

Audit chain for these two orders: `audit-chain.jsonl`, verified with `python -m core.audit.verify docs/evidence/audit-chain.jsonl` (output in `audit-chain.verify.txt`). Both orders were proposed by the owner (`proposed_by: owner`); planner-proposed evidence follows with the agent runner.
