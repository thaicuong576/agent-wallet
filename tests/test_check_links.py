"""Tests for scripts/check_links.py."""

from __future__ import annotations

import pathlib

from scripts.check_links import (
    ALLOWED_PENDING_PATHS,
    check_all_markdown_links,
    check_file_links,
    extract_anchors,
    find_repo_root,
    slugify_heading,
)


def test_slugify_heading() -> None:
    assert slugify_heading("Quick Start") == "quick-start"
    assert slugify_heading("How It Works (`api.py`)") == "how-it-works-apipy"
    assert slugify_heading("Section 1.2: Details & Specs") == "section-12-details--specs"
    assert slugify_heading("Custom Heading {#my-custom-anchor}") == "custom-heading"
    assert slugify_heading("[Link Title](https://example.com)") == "link-title"


def test_extract_anchors() -> None:
    doc = """# Header One
## Header Two
### Header Two
```python
# Not A Header
```
<a id="explicit-html-anchor"></a>
### Explicit Custom {#custom-anchor}
"""
    anchors = extract_anchors(doc)
    assert "header-one" in anchors
    assert "header-two" in anchors
    assert "header-two-1" in anchors
    assert "explicit-html-anchor" in anchors
    assert "custom-anchor" in anchors
    assert "not-a-header" not in anchors


def test_check_file_links_detects_broken(tmp_path: pathlib.Path) -> None:
    target_md = tmp_path / "target.md"
    target_md.write_text("# Target Section\nSome text.\n", encoding="utf-8")

    source_md = tmp_path / "source.md"
    source_md.write_text(
        """
[Valid Target](target.md)
[Valid Target With Anchor](target.md#target-section)
[Invalid Target](missing.md)
[Invalid Anchor](target.md#missing-section)
[Allowed Pending](docs/submission/02-presentation-deck.md)
[External Link](https://sepolia.base.org)
""",
        encoding="utf-8",
    )

    cache: dict[pathlib.Path, set[str]] = {}
    broken = check_file_links(source_md, tmp_path, cache, allowed_pending=ALLOWED_PENDING_PATHS)

    reasons = [b.reason for b in broken]
    targets = [b.raw_target for b in broken]

    assert "missing.md" in targets
    assert "target.md#missing-section" in targets
    assert any("Target path does not exist" in r for r in reasons)
    assert any("Anchor '#missing-section' not found" in r for r in reasons)
    assert len(broken) == 2


def test_repository_markdown_links_are_clean() -> None:
    root = find_repo_root()
    broken = check_all_markdown_links(root)
    assert not broken, f"Broken markdown links detected: {broken}"
