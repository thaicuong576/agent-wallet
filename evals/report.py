"""Report generator for markdown and JSON evidence outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.scenario import ScenarioResult

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
RULE_CODES_PATH = CONTRACTS_DIR / "rule-codes.json"
FIXED_REPORT_TIMESTAMP = "2026-10-14T12:00:00Z"


def load_all_rule_codes() -> list[dict[str, Any]]:
    """Load the complete official rule code catalog from contracts/rule-codes.json."""
    with open(RULE_CODES_PATH, encoding="utf-8") as f:
        return json.load(f)


def generate_results_data(
    results: list[ScenarioResult],
    all_rule_codes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile structured results data for JSON and Markdown reports."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    pass_rate = (passed / total) if total > 0 else 0.0

    # Collect rule codes covered by passing scenarios
    covered_codes_set: set[str] = set()
    code_to_scenario: dict[str, str] = {}

    # First pass: map primary expected rule codes
    for r in results:
        for code in r.expected_rule_codes:
            covered_codes_set.add(code)
            if code not in code_to_scenario:
                code_to_scenario[code] = r.scenario_id

    # Second pass: map secondary actual rule codes
    for r in results:
        for code in r.actual_rule_codes:
            covered_codes_set.add(code)
            if code not in code_to_scenario:
                code_to_scenario[code] = r.scenario_id

    catalog_codes = [item["code"] for item in all_rule_codes]
    covered_in_catalog = [c for c in catalog_codes if c in covered_codes_set]
    uncovered_in_catalog = [c for c in catalog_codes if c not in covered_codes_set]

    # Category breakdown
    categories: dict[str, dict[str, Any]] = {}
    for r in results:
        cat = r.category
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0}
        categories[cat]["total"] += 1
        if r.passed:
            categories[cat]["passed"] += 1
        else:
            categories[cat]["failed"] += 1

    for cat_data in categories.values():
        t = cat_data["total"]
        p = cat_data["passed"]
        cat_data["pass_rate"] = round(p / t, 4) if t > 0 else 0.0

    failing_scenarios = [r.scenario_id for r in results if not r.passed]

    return {
        "schema_version": 1,
        "evaluated_at": FIXED_REPORT_TIMESTAMP,
        "command": "python -m evals.run",
        "mode": "replay",
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(pass_rate, 4),
            "rule_codes_covered": len(covered_in_catalog),
            "rule_codes_total": len(catalog_codes),
            "coverage_rate": round(len(covered_in_catalog) / len(catalog_codes), 4)
            if catalog_codes
            else 0.0,
            "failing_scenarios": failing_scenarios,
        },
        "categories": categories,
        "rule_coverage": {
            "covered": sorted(covered_in_catalog),
            "uncovered": sorted(uncovered_in_catalog),
            "total_catalog": len(catalog_codes),
            "mapping": {
                code: code_to_scenario.get(code, "") for code in sorted(covered_in_catalog)
            },
        },
        "scenarios": [r.to_dict() for r in results],
    }


def render_markdown_report(data: dict[str, Any], all_rule_codes: list[dict[str, Any]]) -> str:
    """Render deterministic Github-flavored markdown evaluation report."""
    summary = data["summary"]
    categories = data["categories"]
    rule_cov = data["rule_coverage"]
    scenarios = data["scenarios"]

    overall_status = "PASSED" if summary["failed"] == 0 else "FAILED"
    pass_pct = f"{summary['pass_rate'] * 100:.1f}%"
    failing_text = (
        f"None. All {summary['total']} scenarios passed."
        if summary["failed"] == 0
        else ", ".join(summary["failing_scenarios"])
    )
    cov_pct = f"{summary['coverage_rate'] * 100:.1f}%"
    s_tot = summary["total"]
    s_pass = summary["passed"]
    s_fail = summary["failed"]
    cov_count = summary["rule_codes_covered"]
    cov_tot = summary["rule_codes_total"]

    lines: list[str] = [
        "# Evaluation Report: Replay Mode",
        "",
        (
            "Deterministic replay-mode evaluation harness for "
            "FinTechathon 2026 (Topic F, Agent Wallet)."
        ),
        (
            "Evaluates compliance with the Investment Policy Statement (IPS), "
            "permission tiers, emergency stop, approval lifecycle, prompt injection defenses, "
            "and cryptographic audit log integrity."
        ),
        "",
        f"- **Generated at:** {data['evaluated_at']} (Deterministic evaluation clock)",
        "- **Environment:** Offline, replay mode, testnet only, no network, no private keys",
        f"- **Overall Result:** {overall_status} ({s_pass}/{s_tot}, {pass_pct})",
        f"- **Failing Scenarios:** {failing_text}",
        f"- **Rule Code Coverage:** {cov_count} / {cov_tot} codes ({cov_pct})",
        "- **Command to Reproduce:**",
        "  ```sh",
        f"  {data['command']}",
        "  ```",
        "",
        "## Summary by Category",
        "",
        "| Category | Total | Passed | Failed | Pass Rate |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for cat_name in sorted(categories.keys()):
        c = categories[cat_name]
        c_pct = f"{c['pass_rate'] * 100:.1f}%"
        lines.append(f"| `{cat_name}` | {c['total']} | {c['passed']} | {c['failed']} | {c_pct} |")

    lines.append(f"| **Total** | **{s_tot}** | **{s_pass}** | **{s_fail}** | **{pass_pct}** |")
    lines.append("")

    # Rule code coverage section
    lines.append(
        f"## Rule Code Coverage ({summary['rule_codes_covered']} / {summary['rule_codes_total']})"
    )
    lines.append("")
    lines.append("| Rule Code | Family | Description | Status | Covered by Scenario |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")

    mapping = rule_cov.get("mapping", {})
    for item in all_rule_codes:
        code = item["code"]
        family = item.get("family", "")
        desc = item.get("description", "")
        if code in mapping:
            status = "✅ Covered"
            scenario_ref = f"`{mapping[code]}`"
        else:
            status = "⚠️ Not covered"
            scenario_ref = "-"
        lines.append(f"| `{code}` | {family} | {desc} | {status} | {scenario_ref} |")

    lines.append("")

    # Detailed scenarios by category
    lines.append("## Detailed Scenario Results")
    lines.append("")

    # Group scenarios by category
    scenarios_by_cat: dict[str, list[dict[str, Any]]] = {}
    for sc in scenarios:
        sc_cat = sc["category"]
        if sc_cat not in scenarios_by_cat:
            scenarios_by_cat[sc_cat] = []
        scenarios_by_cat[sc_cat].append(sc)

    for cat_name in sorted(scenarios_by_cat.keys()):
        lines.append(f"### Category: `{cat_name}`")
        lines.append("")
        lines.append("| Scenario ID | Description | Expected | Actual | Pass |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")

        for sc in scenarios_by_cat[cat_name]:
            pass_str = "✅ Pass" if sc["passed"] else "❌ Fail"
            exp = sc["expected_outcome"]
            if sc.get("expected_rule_codes"):
                exp += f" ({', '.join(sc['expected_rule_codes'])})"
            act = sc["actual_outcome"]
            if sc.get("actual_rule_codes"):
                act += f" ({', '.join(sc['actual_rule_codes'])})"
            lines.append(
                f"| `{sc['scenario_id']}` | {sc['description']} | `{exp}` | `{act}` | {pass_str} |"
            )

        lines.append("")

    return "\n".join(lines) + "\n"


def write_reports(
    results: list[ScenarioResult],
    report_md_path: Path | None = None,
    results_json_path: Path | None = None,
) -> tuple[Path, Path]:
    """Generate and write both eval-report.md and eval-results.json."""
    if report_md_path is None:
        report_md_path = REPO_ROOT / "docs" / "evidence" / "eval-report.md"
    if results_json_path is None:
        results_json_path = REPO_ROOT / "docs" / "evidence" / "eval-results.json"

    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    results_json_path.parent.mkdir(parents=True, exist_ok=True)

    all_rule_codes = load_all_rule_codes()
    data = generate_results_data(results, all_rule_codes)

    # Render deterministic Markdown
    md_content = render_markdown_report(data, all_rule_codes)
    report_md_path.write_text(md_content, encoding="utf-8")

    # Render deterministic JSON
    json_content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    results_json_path.write_text(json_content, encoding="utf-8")

    return report_md_path, results_json_path
