"""Unit and integration tests for the evaluation harness itself."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from evals.driver import drive_scenario
from evals.report import (
    generate_results_data,
    load_all_rule_codes,
)
from evals.run import main, run_evals
from evals.scenario import Scenario
from evals.scenarios import get_all_scenarios


def test_scenario_catalog_count_and_categories():
    """Harness must contain at least 25 scenarios covering all required categories."""
    scenarios = get_all_scenarios()
    assert len(scenarios) >= 25, f"Expected at least 25 scenarios, got {len(scenarios)}"

    categories = {s.category for s in scenarios}
    expected_categories = {
        "allow",
        "deny",
        "escalate",
        "no_action",
        "tier_and_stop",
        "injection",
        "integrity",
    }
    assert expected_categories.issubset(categories)


def test_all_scenarios_pass_in_replay_mode():
    """All 44 baseline scenarios must pass in replay mode."""
    exit_code, results = run_evals(verbose=False)
    assert exit_code == 0
    assert len(results) >= 25
    failing = [r.scenario_id for r in results if not r.passed]
    assert failing == [], f"Failing scenarios: {failing}"


def test_deliberately_wrong_outcome_expectation_is_detected():
    """Harness verification: a wrong expected outcome must be reported as a failure."""
    scenarios = get_all_scenarios()
    base_sc = next(s for s in scenarios if s.id == "allow-01-approve")

    # Deliberately falsify expected outcome to 'denied'
    wrong_sc = Scenario(
        id="wrong-expectation-outcome",
        category=base_sc.category,
        description="Deliberately wrong expected outcome",
        inputs=base_sc.inputs,
        expected_outcome="denied",  # Reality is 'executed'
        expected_rule_codes=[],
    )

    result = drive_scenario(wrong_sc)
    assert result.passed is False
    assert result.actual_outcome == "executed"
    assert result.expected_outcome == "denied"


def test_deliberately_wrong_rule_code_expectation_is_detected():
    """Harness verification: a wrong expected rule code must be reported as a failure."""
    scenarios = get_all_scenarios()
    base_sc = next(s for s in scenarios if s.id == "deny-limit-per-tx")

    # Deliberately falsify expected rule code
    wrong_sc = Scenario(
        id="wrong-expectation-code",
        category=base_sc.category,
        description="Deliberately wrong expected rule code",
        inputs=base_sc.inputs,
        expected_outcome="denied",
        expected_rule_codes=["ACCESS.TOKEN_NOT_ALLOWED"],  # Reality is LIMIT.PER_TX
    )

    result = drive_scenario(wrong_sc)
    assert result.passed is False
    assert "LIMIT.PER_TX" in result.actual_rule_codes
    assert "ACCESS.TOKEN_NOT_ALLOWED" not in result.actual_rule_codes


def test_failing_scenario_causes_nonzero_exit_code(tmp_path: Path):
    """When any scenario fails, run_evals returns non-zero exit code 1."""
    scenarios = get_all_scenarios()
    bad_sc = Scenario(
        id="deliberately-broken",
        category="allow",
        description="Fails unconditionally",
        inputs=scenarios[0].inputs,
        expected_outcome="invalid_expected_outcome",
        expected_rule_codes=[],
    )

    with patch("evals.run.get_all_scenarios", return_value=[bad_sc]):
        exit_code, results = run_evals(
            report_md_path=tmp_path / "report.md",
            results_json_path=tmp_path / "results.json",
            verbose=False,
        )
        assert exit_code == 1
        assert len(results) == 1
        assert results[0].passed is False


def test_eval_reports_are_byte_identical_across_runs(tmp_path: Path):
    """Harness must be strictly deterministic: two runs produce byte-identical reports."""
    report1 = tmp_path / "run1" / "eval-report.md"
    json1 = tmp_path / "run1" / "eval-results.json"
    report2 = tmp_path / "run2" / "eval-report.md"
    json2 = tmp_path / "run2" / "eval-results.json"

    exit1, res1 = run_evals(report_md_path=report1, results_json_path=json1, verbose=False)
    exit2, res2 = run_evals(report_md_path=report2, results_json_path=json2, verbose=False)

    assert exit1 == 0
    assert exit2 == 0
    assert report1.read_bytes() == report2.read_bytes()
    assert json1.read_bytes() == json2.read_bytes()


# Codes added by the webapp contract change (ticket C1). The eval scenarios for them land with
# ticket C2 (policy and gateway); C2 removes this set and restores full coverage.
PENDING_WEBAPP_CODES = {
    "ACCESS.OWNER_ONLY_FUNCTION",
    "TIER.GLOBAL_STOP",
    "TIER.POLICY_CHANGED",
}


def test_rule_codes_catalog_coverage():
    """Every rule code except the pending webapp codes must be covered by the scenarios."""
    all_codes = load_all_rule_codes()
    assert len(all_codes) == 25 + len(PENDING_WEBAPP_CODES)

    _, results = run_evals(verbose=False)
    data = generate_results_data(results, all_codes)

    cov = data["rule_coverage"]
    assert len(cov["covered"]) == 25
    assert set(cov["uncovered"]) == PENDING_WEBAPP_CODES


def test_markdown_report_structure(tmp_path: Path):
    """Generated markdown report must have required headers and summary table."""
    md_path = tmp_path / "eval-report.md"
    json_path = tmp_path / "eval-results.json"
    exit_code, results = run_evals(
        report_md_path=md_path, results_json_path=json_path, verbose=False
    )
    assert exit_code == 0

    content = md_path.read_text(encoding="utf-8")
    assert "# Evaluation Report: Replay Mode" in content
    assert "## Summary by Category" in content
    assert "## Rule Code Coverage" in content
    assert "## Detailed Scenario Results" in content
    assert "python -m evals.run" in content
    assert "**Overall Result:** PASSED" in content


def test_cli_main_entry_point():
    """CLI main function returns exit code 0 on passing suite."""
    with patch("evals.run.print"):
        exit_code = main()
        assert exit_code == 0
