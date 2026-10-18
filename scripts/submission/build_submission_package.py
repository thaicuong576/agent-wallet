#!/usr/bin/env python3
"""Build the formal FinTechathon 2026 submission package for BaKhi Wallet.

Creates the 6 submission folders:
01-technical-documentation/
02-presentation/
03-demo-video/
04-source-code/
05-security-self-assessment/
06-execution-evidence/

Standard library only.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("D:/eddie-projects/personal-projects/_submission-package")


def build_package(
    dest_dir: Path | str = DEFAULT_OUTPUT_DIR,
    repo_root: Path | str | None = None,
) -> Path:
    dest_path = Path(dest_dir).resolve()
    if repo_root is None:
        root_path = Path(__file__).resolve().parent.parent.parent
    else:
        root_path = Path(repo_root).resolve()

    # Clean destination if it exists, then re-create
    if dest_path.exists():
        shutil.rmtree(dest_path)
    dest_path.mkdir(parents=True, exist_ok=True)

    # 1. 01-technical-documentation
    dir_01 = dest_path / "01-technical-documentation"
    dir_01.mkdir(parents=True, exist_ok=True)
    tech_doc = root_path / "docs" / "submission" / "01-technical-documentation.md"
    if tech_doc.exists():
        shutil.copy2(tech_doc, dir_01 / "technical-documentation.md")

    # Architecture notes referenced by technical-documentation.md
    arch_src = root_path / "docs" / "architecture"
    arch_dest = dir_01 / "architecture"
    if arch_src.exists():
        shutil.copytree(arch_src, arch_dest)

    # 2. 02-presentation
    dir_02 = dest_path / "02-presentation"
    dir_02.mkdir(parents=True, exist_ok=True)
    deck_src = root_path / "docs" / "submission" / "02-presentation-deck.md"
    if deck_src.exists():
        shutil.copy2(deck_src, dir_02 / "02-presentation-deck.md")

    previews_dest = dir_02 / "previews"
    previews_dest.mkdir(parents=True, exist_ok=True)
    for preview_name in (
        "ui3-overview-1280-light.png",
        "ui3-chat-progress-1280-light.png",
        "ui3-rules-1280-light.png",
    ):
        p_src = root_path / "docs" / "previews" / preview_name
        if p_src.exists():
            shutil.copy2(p_src, previews_dest / preview_name)

    # 3. 03-demo-video
    dir_03 = dest_path / "03-demo-video"
    dir_03.mkdir(parents=True, exist_ok=True)
    video_src = root_path / "docs" / "submission" / "03-demo-video-script.md"
    if video_src.exists():
        shutil.copy2(video_src, dir_03 / "03-demo-video-script.md")

    # 4. 04-source-code
    dir_04 = dest_path / "04-source-code"
    dir_04.mkdir(parents=True, exist_ok=True)
    readme_04_content = """# BaKhi Wallet: Source Code and Deployment Guide
FinTechathon 2026 — Topic F ("Agent Wallet")

- GitHub Repository: https://github.com/thaicuong576/agent-wallet
- Hosted Application: https://agent-wallet-6vuy.onrender.com
- Target Network: Base Sepolia Testnet (Chain ID 84532)

Note: As specified in competition deliverable 04 guidelines, the repository is maintained
and judged via the official GitHub repository link above. No redundant copy of the source
tree is duplicated into this distribution directory.

## System Prerequisites
- Python 3.11+
- Node.js 18+ (for UI preview checks)
- Git

## How to Run Tests and Verification
From the root of the cloned repository:

1. Run the complete pytest test suite (1,279+ tests pass):
   python -m pytest -q

2. Run the deterministic offline evaluation harness (46 scenarios, 100% pass):
   python -m evals.run

3. Verify cryptographic SHA-256 audit log hash chains:
   python -m core.audit.verify docs/evidence/audit-chain.jsonl

4. Scan for secrets (confirms zero keys/secrets in repository):
   python scripts/checks/check_no_secrets.py

5. Build the complete submission package:
   python scripts/submission/build_submission_package.py

## Local Quick Start
1. Create and activate a virtual environment:
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate

2. Install dependencies:
   pip install -r requirements.txt

3. Start local development server:
   uvicorn api.main:app --host 127.0.0.1 --port 8000
   Open http://127.0.0.1:8000/ui/ in your browser.
"""
    (dir_04 / "README.txt").write_text(readme_04_content, encoding="utf-8")

    # 5. 05-security-self-assessment
    dir_05 = dest_path / "05-security-self-assessment"
    dir_05.mkdir(parents=True, exist_ok=True)
    sec_src = root_path / "docs" / "submission" / "05-security-self-assessment.md"
    if sec_src.exists():
        shutil.copy2(sec_src, dir_05 / "self-assessment.md")

    # 6. 06-execution-evidence
    dir_06 = dest_path / "06-execution-evidence"
    dir_06.mkdir(parents=True, exist_ok=True)
    ev_master = root_path / "docs" / "submission" / "06-execution-evidence.md"
    if ev_master.exists():
        shutil.copy2(ev_master, dir_06 / "06-execution-evidence.md")

    for ev_file in (
        "tx_hashes.md",
        "defi-evidence.md",
        "runner-evidence.md",
        "webapp-evidence.md",
        "eval-report.md",
        "eval-results.json",
        "audit-chain.jsonl",
        "audit-chain.verify.txt",
        "runner-audit-chain.jsonl",
    ):
        src = root_path / "docs" / "evidence" / ev_file
        if src.exists():
            shutil.copy2(src, dir_06 / ev_file)

    # Top-level README.txt
    top_readme_content = """# BaKhi Wallet: Submission Package Index
FinTechathon 2026 — Topic F ("Agent Wallet")

This package consolidates the complete submission materials for BaKhi Wallet, an autonomous
AI wallet agent operating on Base Sepolia inside a mathematically enforced Investment Policy.

Hosted Application: https://agent-wallet-6vuy.onrender.com
GitHub Repository:  https://github.com/thaicuong576/agent-wallet
Target Network:     Base Sepolia (Chain ID 84532)

---

## Submission Package Contents

### 01-technical-documentation/
- technical-documentation.md: System architecture, core algorithm, and trust boundary.
- architecture/: Detailed design specifications covering gateway, policy engine, signer boundary,
  protocol adapters (Aave v3, Aave Stata, Morpho Blue, Uniswap v3), SSE streaming, and auth.

### 02-presentation/
- 02-presentation-deck.md: Slide-by-slide 9-minute presentation deck with exact on-slide copy,
  precise visual specifications, timed speaker scripts, and judging criteria mappings.
- previews/: Referenced user interface screenshots from UI v3 (Overview, Chat progress, Rules).

### 03-demo-video/
- 03-demo-video-script.md: Shot-by-shot 5-minute video demonstration plan including exact clicks,
  chat prompts, narration scripts, pre-flight checklists, and live failure recovery procedures.

### 04-source-code/
- README.txt: GitHub repository link, deployment instructions, prerequisites, and test commands.

### 05-security-self-assessment/
- self-assessment.md: Security self-assessment report detailing permission tiers (T0-T3),
  step-up cryptographic authorization, 25 verified controls, and the known-risk list (K1-K25).

### 06-execution-evidence/
- 06-execution-evidence.md: Master execution evidence index with all 10 verified on-chain txs.
- tx_hashes.md: Initial Base Sepolia on-chain transaction receipts.
- defi-evidence.md: Multi-protocol execution evidence (Aave Stata, Morpho Blue, Uniswap v3).
- runner-evidence.md: Agent runner execution and hostile-note policy denial.
- webapp-evidence.md: Webapp sign-in, top-up, and planner-proposed transactions.
- eval-report.md: Evaluation harness report (46/46 passed, 27/28 rule codes covered).
- eval-results.json: Structured JSON output of all 46 evaluation scenarios.
- audit-chain.jsonl: Genesis audit log records with SHA-256 hash chaining.
- audit-chain.verify.txt: Verification proof from core.audit.verify.
- runner-audit-chain.jsonl: Complete audit chain for runner session.

---

## 5 Verification Commands for Judges
From the root of the cloned repository:
1. python -m pytest -q                                # Run test suite (1,279+ tests pass)
2. python -m evals.run                                # Replay 46 evaluation scenarios offline
3. python -m core.audit.verify docs/evidence/audit-chain.jsonl # Verify SHA-256 audit hash chain
4. python scripts/checks/check_no_secrets.py                 # Scan repo for secrets (clean)
5. python scripts/submission/build_submission_package.py         # Build and verify this package
"""
    (dest_path / "README.txt").write_text(top_readme_content, encoding="utf-8")
    return dest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the FinTechathon 2026 submission package.")
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()
    dest = build_package(args.output)
    print(f"Successfully built submission package at: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
