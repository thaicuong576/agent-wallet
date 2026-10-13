# Agent conventions: agent-wallet

Rules for every agent (orchestrator and workers) working in this repo. The plan, scope and milestones are in `PLAN.md`; the project description is in `README.md`.

## Language
- All artifacts are written in English: code, comments, README, docs, specs, tickets, PR titles and descriptions, commit messages. The competition requires English.
- Fleet label names (for example `rủi-ro-thấp`, `chờ-duyệt`) come from agent-ops and stay as they are.

## Review and merge (CI/CD)
The owner has delegated review to the orchestrator. This overrides the fleet default that every merge is the owner's gate.

- **Merge on green.** The orchestrator squash-merges a PR when all hold: CI is green, its own independent check passes, the diff stays inside the ticket's scope, the Docs items of the definition of done (see `PLAN.md`) are present, and the ticket is `rủi-ro-thấp`.
- **Escalate to the owner only for:**
  - anything touching keys, funds, the signer, or wallet permissions (`rủi-ro-cao`);
  - a new or changed dependency, CI or deploy configuration;
  - a change to the IPS schema or to a permission tier;
  - switching the repo to public, or any submission action;
  - a scope or cut-order decision, or a business assumption;
  - the same item failing review twice, or CI that cannot be made green.
- **CI is mandatory from the scaffold ticket on:** lint, unit tests (policy engine, layer A), secret scan, and a check that no module other than the gateway calls the signer.
- After every merge, close the worker session and release its slot.
- Report to the owner in a short digest per sprint: merged, escalated, next.

## Safety
- Testnet only. Never put a mainnet key, a real seed phrase or any secret in the repo, a PR, a log or a test fixture. `.env` stays ignored; `.env.example` holds names only.
- The LLM proposes, code decides. The LLM never holds a key.
- No module other than the gateway may call the signer.
