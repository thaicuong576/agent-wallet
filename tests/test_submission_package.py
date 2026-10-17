"""Test the build_submission_package script produces the required folders and files."""

from pathlib import Path

from scripts.submission.build_submission_package import build_package

EXPECTED_FOLDERS = [
    "01-technical-documentation",
    "02-presentation",
    "03-demo-video",
    "04-source-code",
    "05-security-self-assessment",
    "06-execution-evidence",
]


def test_build_package_produces_six_folders_and_expected_files(tmp_path: Path):
    out_dir = tmp_path / "pkg"
    result = build_package(dest_dir=out_dir)

    assert result == out_dir
    assert out_dir.exists()

    # 1. Check all six folders exist
    for folder_name in EXPECTED_FOLDERS:
        folder = out_dir / folder_name
        assert folder.exists(), f"Missing folder: {folder_name}"
        assert folder.is_dir(), f"{folder_name} is not a directory"

    # 2. Check top-level README.txt exists and lists all folders
    top_readme = out_dir / "README.txt"
    assert top_readme.exists()
    top_readme_text = top_readme.read_text(encoding="utf-8")
    for folder_name in EXPECTED_FOLDERS:
        assert folder_name in top_readme_text
    assert "https://github.com/thaicuong576/agent-wallet" in top_readme_text
    assert "https://agent-wallet-6vuy.onrender.com" in top_readme_text

    # 3. Check specific files in each folder
    # 01-technical-documentation
    dir_01 = out_dir / "01-technical-documentation"
    assert (dir_01 / "technical-documentation.md").is_file()
    assert (dir_01 / "technical-documentation.md").stat().st_size > 0
    assert (dir_01 / "architecture").is_dir()
    assert list((dir_01 / "architecture").glob("*.md")), "architecture folder has markdown files"

    # 02-presentation
    dir_02 = out_dir / "02-presentation"
    assert (dir_02 / "02-presentation-deck.md").is_file()
    assert (dir_02 / "02-presentation-deck.md").stat().st_size > 0
    assert (dir_02 / "previews").is_dir()
    assert list((dir_02 / "previews").glob("*.png")), "previews folder has png files"

    # 03-demo-video
    dir_03 = out_dir / "03-demo-video"
    assert (dir_03 / "03-demo-video-script.md").is_file()
    assert (dir_03 / "03-demo-video-script.md").stat().st_size > 0

    # 04-source-code
    dir_04 = out_dir / "04-source-code"
    readme_04 = dir_04 / "README.txt"
    assert readme_04.is_file()
    assert readme_04.stat().st_size > 0
    readme_04_text = readme_04.read_text(encoding="utf-8")
    assert "https://github.com/thaicuong576/agent-wallet" in readme_04_text
    assert "https://agent-wallet-6vuy.onrender.com" in readme_04_text
    assert "python -m pytest" in readme_04_text

    # 05-security-self-assessment
    dir_05 = out_dir / "05-security-self-assessment"
    assert (dir_05 / "self-assessment.md").is_file()
    assert (dir_05 / "self-assessment.md").stat().st_size > 0

    # 06-execution-evidence
    dir_06 = out_dir / "06-execution-evidence"
    expected_evidence_files = [
        "06-execution-evidence.md",
        "tx_hashes.md",
        "defi-evidence.md",
        "runner-evidence.md",
        "webapp-evidence.md",
        "eval-report.md",
        "eval-results.json",
        "audit-chain.jsonl",
        "audit-chain.verify.txt",
        "runner-audit-chain.jsonl",
    ]
    for ef in expected_evidence_files:
        p = dir_06 / ef
        assert p.is_file(), f"Missing evidence file: {ef}"
        assert p.stat().st_size > 0, f"Empty evidence file: {ef}"
