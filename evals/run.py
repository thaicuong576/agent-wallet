"""Main entry point for running the evaluation harness in replay mode."""

from __future__ import annotations

import sys
from pathlib import Path

from evals.driver import drive_scenario
from evals.report import write_reports
from evals.scenario import ScenarioResult
from evals.scenarios import get_all_scenarios


def run_evals(
    report_md_path: Path | None = None,
    results_json_path: Path | None = None,
    verbose: bool = True,
) -> tuple[int, list[ScenarioResult]]:
    """Execute all evaluation scenarios in replay mode and write evidence files."""
    scenarios = get_all_scenarios()
    results: list[ScenarioResult] = []

    if verbose:
        print(f"Running {len(scenarios)} evaluation scenarios in replay mode...")

    for sc in scenarios:
        res = drive_scenario(sc)
        results.append(res)
        if verbose:
            status = "PASS" if res.passed else "FAIL"
            print(f"[{status}] {sc.id} ({sc.category}): {res.details}")

    report_md, results_json = write_reports(
        results,
        report_md_path=report_md_path,
        results_json_path=results_json_path,
    )

    passed_count = sum(1 for r in results if r.passed)
    failed_count = len(results) - passed_count

    if verbose:
        print("\nEvaluation Run Summary:")
        print(f"Total: {len(results)} | Passed: {passed_count} | Failed: {failed_count}")
        print(f"Wrote report to: {report_md}")
        print(f"Wrote results to: {results_json}")

    exit_code = 0 if failed_count == 0 else 1
    return exit_code, results


def main() -> int:
    """CLI entry point for python -m evals.run."""
    exit_code, _ = run_evals(verbose=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
