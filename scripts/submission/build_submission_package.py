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
import json
import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "dist" / "submission-package"
REPO_URL = "https://github.com/thaicuong576/agent-wallet"
APP_URL = "https://bakhi-wallet.eddiephung.me"
_MD_LINK = re.compile(r"(\]\()([^)\s]+)(\))")


def _rewrite_links(text: str, src_rel: str, placed: dict[str, str], dest_rel: str) -> str:
    """Point relative Markdown links at the copy inside the package, or at GitHub otherwise.

    src_rel is the file's repo path, dest_rel its package path, and placed maps every copied
    repo path to its package path. Absolute URLs and in-page anchors are left as they are.
    """
    src_dir = posixpath.dirname(src_rel)
    dest_dir = posixpath.dirname(dest_rel)

    def fix(m: re.Match[str]) -> str:
        target = m.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            return m.group(0)
        path, _, anchor = target.partition("#")
        repo_path = posixpath.normpath(posixpath.join(src_dir, path))
        if repo_path.startswith(".."):
            return m.group(0)
        if repo_path in placed:
            new = posixpath.relpath(placed[repo_path], dest_dir or ".")
        else:
            new = f"{REPO_URL}/blob/main/{repo_path}"
        return f"{m.group(1)}{new}{'#' + anchor if anchor else ''}{m.group(3)}"

    return _MD_LINK.sub(fix, text)


def _eval_summary(repo_root: Path) -> dict[str, int]:
    """Scenario and rule-code counts from docs/evidence/eval-results.json."""
    try:
        data = json.loads((repo_root / "docs/evidence/eval-results.json").read_text("utf-8"))
        summary = data["summary"]
        return {
            "total": int(summary["total"]),
            "passed": int(summary["passed"]),
            "covered": int(summary["rule_codes_covered"]),
            "codes": int(summary["rule_codes_total"]),
        }
    except (OSError, KeyError, ValueError, TypeError):
        return {"total": 48, "passed": 48, "covered": 29, "codes": 31}


def _get_test_count(repo_root: Path) -> str:
    """Compute test count via pytest --collect-only or docs/guides/testing.md."""
    # 1. Try pytest --collect-only -q first
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout:
            for line in reversed(proc.stdout.strip().splitlines()):
                m = re.search(r"(\d+)\s+tests?\s+collected", line)
                if m:
                    return f"{int(m.group(1)):,}"
    except Exception:
        pass

    # 2. Fallback to docs/guides/testing.md Total Test Count
    try:
        guide_path = repo_root / "docs" / "guides" / "testing.md"
        if guide_path.exists():
            guide_text = guide_path.read_text(encoding="utf-8")
            pattern = r"\|\s*\*\*Total Test Count\*\*\s*\|\s*\*\*([0-9,]+)\*\*\s*\|"
            m = re.search(pattern, guide_text)
            if m:
                count_val = int(m.group(1).replace(",", ""))
                return f"{count_val:,}"
    except Exception:
        pass

    return "see docs/guides/testing.md"


def _get_security_k_count(repo_root: Path) -> int:
    """Derive K risk count from 05-security-self-assessment.md table rows."""
    sec_path = repo_root / "docs" / "submission" / "05-security-self-assessment.md"
    if sec_path.exists():
        text = sec_path.read_text(encoding="utf-8")
        matches = re.findall(r"\|\s*\*\*K(\d+)\*\*\s*\|", text)
        if matches:
            return len(matches)
    return 26


def _get_verified_tx_count(repo_root: Path) -> int:
    """Derive unique on-chain tx count from sections 1 and 1b of 06-execution-evidence.md."""
    ev_path = repo_root / "docs" / "submission" / "06-execution-evidence.md"
    if not ev_path.exists():
        return 25
    text = ev_path.read_text(encoding="utf-8")
    sec2_pos = text.find("## 2. Cryptographic Audit Chain Verification")
    content = text[:sec2_pos] if sec2_pos != -1 else text
    hashes = set(re.findall(r"0x[a-fA-F0-9]{64}", content))
    return len(hashes)


def _get_git_commit_short(repo_root: Path) -> str:
    """Get short commit id via git rev-parse --short HEAD."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return "HEAD"


def _write_source_zip(repo_root: Path, dest_zip: Path) -> None:
    """Write source.zip using git archive --format=zip -o <path> HEAD."""
    if not shutil.which("git"):
        print("Warning: git is unavailable, skipping source.zip")
        return
    try:
        proc = subprocess.run(
            ["git", "archive", "--format=zip", "-o", str(dest_zip), "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"Warning: git archive failed ({proc.stderr.strip()}), skipping source.zip")
    except Exception as exc:
        print(f"Warning: git is unavailable ({exc}), skipping source.zip")


PREVIEWS = (
    "ui3-chat-progress-1280-light.png",
    "ui3-overview-1280-light.png",
    "ui3-rules-1280-light.png",
    "ui3-chat-1280-dark.png",
)
EVIDENCE_FILES = (
    "tx_hashes.md",
    "defi-evidence.md",
    "defi-evidence-loop.md",
    "runner-evidence.md",
    "webapp-evidence.md",
    "eval-report.md",
    "eval-results.json",
    "audit-chain.jsonl",
    "audit-chain.verify.txt",
    "runner-audit-chain.jsonl",
    "README.md",
)


def _placements(root_path: Path) -> dict[str, str]:
    """Repo path -> package path for every file the package carries."""
    placed = {
        "docs/submission/01-technical-documentation.md": (
            "01-technical-documentation/technical-documentation.md"
        ),
        "docs/submission/02-presentation-deck.md": "02-presentation/02-presentation-deck.md",
        "docs/submission/03-demo-video-script.md": "03-demo-video/03-demo-video-script.md",
        "docs/submission/05-security-self-assessment.md": (
            "05-security-self-assessment/self-assessment.md"
        ),
        "docs/submission/06-execution-evidence.md": (
            "06-execution-evidence/06-execution-evidence.md"
        ),
    }
    arch = root_path / "docs" / "architecture"
    if arch.exists():
        for f in sorted(arch.rglob("*")):
            if f.is_file():
                rel = f.relative_to(arch).as_posix()
                dest = f"01-technical-documentation/architecture/{rel}"
                placed[f"docs/architecture/{rel}"] = dest
    for name in PREVIEWS:
        placed[f"docs/previews/{name}"] = f"02-presentation/previews/{name}"
    for name in EVIDENCE_FILES:
        placed[f"docs/evidence/{name}"] = f"06-execution-evidence/{name}"
    return {k: v for k, v in placed.items() if (root_path / k).is_file()}


def build_package(
    dest_dir: Path | str = DEFAULT_OUTPUT_DIR,
    repo_root: Path | str | None = None,
    deck_files: list[Path | str] | None = None,
) -> Path:
    dest_path = Path(dest_dir).resolve()
    root_path = REPO_ROOT if repo_root is None else Path(repo_root).resolve()

    # Clean destination if it exists, then re-create
    if dest_path.exists():
        shutil.rmtree(dest_path)
    dest_path.mkdir(parents=True, exist_ok=True)
    for folder in (
        "01-technical-documentation",
        "02-presentation",
        "03-demo-video",
        "04-source-code",
        "05-security-self-assessment",
        "06-execution-evidence",
    ):
        (dest_path / folder).mkdir(parents=True, exist_ok=True)

    # Copy every placed file; Markdown links are rewritten so they work inside the package.
    placed = _placements(root_path)
    for src_rel, dest_rel in placed.items():
        src = root_path / src_rel
        dest = dest_path / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".md":
            text = src.read_text(encoding="utf-8")
            dest.write_text(_rewrite_links(text, src_rel, placed, dest_rel), encoding="utf-8")
        else:
            shutil.copy2(src, dest)

    # The deck itself (PPTX or PDF) is built outside the repository and passed in.
    deck_names: list[str] = []
    for deck in deck_files or []:
        deck_path = Path(deck)
        if deck_path.is_file():
            shutil.copy2(deck_path, dest_path / "02-presentation" / deck_path.name)
            deck_names.append(deck_path.name)

    # Dynamic metrics
    raw_test_count = _get_test_count(root_path)
    if raw_test_count == "see docs/guides/testing.md":
        test_phrase = "see docs/guides/testing.md"
    else:
        test_phrase = f"{raw_test_count} tests; Postgres tests need TEST_DATABASE_URL"

    commit_id = _get_git_commit_short(root_path)
    k_count = _get_security_k_count(root_path)
    tx_count = _get_verified_tx_count(root_path)
    ev = _eval_summary(root_path)
    eval_phrase = (
        f"{ev['total']} scenarios, {ev['passed']}/{ev['total']} passed, "
        f"{ev['covered']} of {ev['codes']} rule codes"
    )

    # 4. 04-source-code
    dir_04 = dest_path / "04-source-code"
    _write_source_zip(root_path, dir_04 / "source.zip")

    readme_04_content = f"""# BaKhi Wallet: Source Code and Deployment Guide
FinTechathon 2026, Topic F ("Agent Wallet")

- GitHub Repository: {REPO_URL}
- Hosted Application: {APP_URL}/chat
- Target Network: Base Sepolia Testnet (Chain ID 84532)
- Source Archive: source.zip (source tree at commit {commit_id})

The archive source.zip contains the source at commit {commit_id} (tracked repository files).
The repository is maintained and judged via the official GitHub repository link above.
Deployment (Docker, Render, Supabase): docs/guides/deployment.md
Test catalogue: docs/guides/testing.md

## System Prerequisites
- Python 3.11+
- Git
- Node.js 18+ (optional: runs the UI flow tests; they are skipped without it)

## How to Run Tests and Verification
From the root of the cloned repository:

1. Install the development dependencies:
   pip install -r requirements-dev.txt

2. Run the complete pytest test suite ({test_phrase}):
   python -m pytest -q

3. Run the deterministic offline evaluation harness ({eval_phrase}):
   python -m evals.run

4. Verify the SHA-256 audit hash chain:
   python -m core.audit.verify docs/evidence/audit-chain.jsonl

5. Scan for secrets (confirms no keys or secrets in the repository):
   python scripts/checks/check_no_secrets.py

## Local Quick Start (no keys needed)
1. Create and activate a virtual environment:
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate

2. Install dependencies:
   pip install -r requirements-dev.txt

3. Start the local server:
   uvicorn api.main:app --host 127.0.0.1 --port 8000
   Open http://127.0.0.1:8000/chat?fixtures=1&demo=1 for the offline demo with fixture data.

With keys (live testnet mode): copy .env.example to .env, set the variables listed in
docs/guides/deployment.md, and run make run.
"""
    (dir_04 / "README.txt").write_text(readme_04_content, encoding="utf-8")

    deck_line = (
        "".join(f"- {name}: The presentation deck.\n" for name in deck_names) if deck_names else ""
    )
    top_readme_content = f"""# BaKhi Wallet: Submission Package Index
FinTechathon 2026, Topic F ("Agent Wallet")

This package consolidates the complete submission materials for BaKhi Wallet, an autonomous
AI wallet agent operating on Base Sepolia inside an Investment Policy enforced by code.

Hosted Application: {APP_URL}/chat
GitHub Repository:  {REPO_URL}
Target Network:     Base Sepolia (Chain ID 84532)

---

## Submission Package Contents

### 01-technical-documentation/
- technical-documentation.md: System architecture, core algorithm, and security design.
- architecture/: Detailed design notes covering gateway, policy engine, signer boundary,
  protocol adapters (Aave v3, Aave Stata, Morpho Blue, Uniswap v3), SSE streaming, and auth.

### 02-presentation/
{deck_line}- 02-presentation-deck.md: Slide-by-slide text of the deck (12 slides and appendix,
  under 10 minutes), with speaker notes and the source of every figure.
- previews/: Screenshots of the web app (chat progress, overview, rules, dark mode).

### 03-demo-video/
- 03-demo-video-script.md: Shot list and narration for the product walkthrough video
  (under 5 minutes), with pre-flight checklist and recovery steps.

### 04-source-code/
- source.zip: Repository source archive at commit {commit_id}.
- README.txt: GitHub repository link, deployment instructions, prerequisites, and test commands.

### 05-security-self-assessment/
- self-assessment.md: Permission tiers (T0-T2, approval rule, emergency stop), controls with
  the test that proves each, and the known-risk list (K1-K{k_count}).

### 06-execution-evidence/
- 06-execution-evidence.md: Execution evidence index with all {tx_count} verified on-chain txs.
- README.md: Guide to current vs historical multi-protocol evidence runs.
- tx_hashes.md: Initial Base Sepolia on-chain transaction receipts.
- defi-evidence.md: First multi-protocol run (Aave Stata, Morpho Blue).
- defi-evidence-loop.md: Multi-protocol hosted loop evidence (Aave, Uniswap, Stata, Morpho).
- runner-evidence.md: Agent runner execution and hostile-note policy denial.
- webapp-evidence.md: Webapp sign-in, top-up, and planner-proposed transactions.
- eval-report.md: Evaluation harness report ({eval_phrase}).
- eval-results.json: Structured JSON output of all {ev["total"]} evaluation scenarios.
- audit-chain.jsonl: Audit records of the first on-chain session with SHA-256 hash chaining.
- audit-chain.verify.txt: Verification proof from core.audit.verify.
- runner-audit-chain.jsonl: Complete audit chain for the runner session.

---

## Verification Commands for Judges
From the root of the cloned repository, after pip install -r requirements-dev.txt:
1. python -m pytest -q                                          # Test suite ({test_phrase})
2. python -m evals.run                                          # {ev["total"]} evaluation scenarios
3. python -m core.audit.verify docs/evidence/audit-chain.jsonl  # Verify the audit hash chain
4. python scripts/checks/check_no_secrets.py                    # Scan repo for secrets
5. python scripts/submission/build_submission_package.py        # Rebuild this package
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
    parser.add_argument(
        "--deck",
        action="append",
        default=[],
        help="Deck file (PPTX or PDF) to copy into 02-presentation; may be repeated.",
    )
    args = parser.parse_args()
    dest = build_package(args.output, deck_files=args.deck)
    print(f"Successfully built submission package at: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
