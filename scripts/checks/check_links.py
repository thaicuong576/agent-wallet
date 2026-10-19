"""Standard library markdown link and anchor checker.

Audits relative links and anchor fragments across repository documentation.
Exits 0 if all links resolve, 1 otherwise.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import urllib.parse
from typing import NamedTuple

# Explicitly allowed pending submission files
ALLOWED_PENDING_PATHS = {
    "docs/submission/02-presentation-deck.md",
    "docs/submission/03-demo-video-script.md",
    "docs/submission/06-execution-evidence.md",
}

LINK_PATTERN = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")
REF_LINK_PATTERN = re.compile(r"^\[([^\]]+)\]:\s*(\S+)")
HTML_ANCHOR_PATTERN = re.compile(r'<a\s+[^>]*(?:id|name)=["\']([^"\']+)["\']', re.IGNORECASE)


class BrokenLink(NamedTuple):
    file_path: pathlib.Path
    line_number: int
    raw_target: str
    reason: str


def slugify_heading(text: str) -> str:
    """Generate GitHub-compatible anchor slug from heading text."""
    # Strip custom anchor identifier if present: {#custom-id}
    text = re.sub(r"\{#[^}]+\}", "", text)
    # Strip markdown links inside headings: [Label](url) -> Label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Strip code spans: `foo` -> foo
    text = text.replace("`", "")
    # Lowercase
    slug = text.strip().lower()
    # Remove punctuation except hyphen, underscore, and space
    slug = re.sub(r"[^\w\s-]", "", slug)
    # Replace spaces with hyphens (GitHub keeps multiple hyphens if spaces/punctuation were deleted)
    slug = re.sub(r"\s", "-", slug)
    return slug.strip("-")


def extract_anchors(content: str) -> set[str]:
    """Extract all valid anchor identifiers from markdown content."""
    anchors: set[str] = set()
    heading_counts: dict[str, int] = {}

    in_code_block = False
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("```") or trimmed.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Check for HTML anchors
        for m in HTML_ANCHOR_PATTERN.finditer(line):
            anchors.add(m.group(1))

        # Check for Markdown headings
        if trimmed.startswith("#"):
            heading_content = trimmed.lstrip("#").strip()
            # Explicit anchor syntax: {#custom-id}
            custom_match = re.search(r"\{#([^}]+)\}", heading_content)
            if custom_match:
                anchors.add(custom_match.group(1))

            base_slug = slugify_heading(heading_content)
            if base_slug:
                count = heading_counts.get(base_slug, 0)
                heading_counts[base_slug] = count + 1
                slug = base_slug if count == 0 else f"{base_slug}-{count}"
                anchors.add(slug)

    return anchors


def find_repo_root(start: pathlib.Path | None = None) -> pathlib.Path:
    current = (start or pathlib.Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return current


def check_file_links(
    file_path: pathlib.Path,
    repo_root: pathlib.Path,
    anchor_cache: dict[pathlib.Path, set[str]],
    allowed_pending: set[str] | None = None,
) -> list[BrokenLink]:
    """Check all relative links in a single markdown file."""
    allowed = allowed_pending if allowed_pending is not None else ALLOWED_PENDING_PATHS
    broken: list[BrokenLink] = []

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [BrokenLink(file_path, 0, "", f"Could not read file: {exc}")]

    lines = content.splitlines()
    in_code_block = False

    for line_idx, line in enumerate(lines, start=1):
        trimmed = line.strip()
        if trimmed.startswith("```") or trimmed.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        raw_targets: list[str] = []

        # Find inline links
        for match in LINK_PATTERN.finditer(line):
            target = match.group(2).strip()
            raw_targets.append(target)

        # Find reference links
        ref_match = REF_LINK_PATTERN.match(trimmed)
        if ref_match:
            raw_targets.append(ref_match.group(2).strip())

        for raw_target in raw_targets:
            # Clean target: remove surrounding < > or quotes, split trailing title
            target = raw_target
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            if " " in target:
                target = target.split(" ", 1)[0]
            target = target.strip().strip('"').strip("'")

            if not target:
                continue

            # Ignore absolute URLs, schemes, mailto, etc.
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            if target.startswith("//"):
                continue

            parsed = urllib.parse.urldefrag(target)
            url_path = urllib.parse.unquote(parsed.url)
            fragment = urllib.parse.unquote(parsed.fragment)

            # Determine destination target file
            if url_path:
                target_path = (file_path.parent / url_path).resolve()
            else:
                target_path = file_path.resolve()

            # Check if allowed pending relative to repo root
            try:
                rel_to_repo = target_path.relative_to(repo_root).as_posix()
            except ValueError:
                rel_to_repo = ""

            if rel_to_repo in allowed:
                continue

            # Check if file/directory exists
            if not target_path.exists():
                broken.append(
                    BrokenLink(
                        file_path,
                        line_idx,
                        raw_target,
                        f"Target path does not exist: {target_path}",
                    )
                )
                continue

            # If target is a directory, verify it has README.md or index.html/md
            if target_path.is_dir():
                if not fragment:
                    continue
                # If directory has an anchor, treat it as pointing to README.md inside it
                readme_path = target_path / "README.md"
                if readme_path.exists():
                    target_path = readme_path
                else:
                    broken.append(
                        BrokenLink(
                            file_path,
                            line_idx,
                            raw_target,
                            f"Directory has anchor but no README.md: {target_path}",
                        )
                    )
                    continue

            # Verify anchor if present and target is a markdown file
            if fragment and target_path.suffix.lower() in {".md", ".markdown"}:
                if target_path not in anchor_cache:
                    try:
                        target_text = target_path.read_text(encoding="utf-8")
                        anchor_cache[target_path] = extract_anchors(target_text)
                    except Exception as exc:
                        broken.append(
                            BrokenLink(
                                file_path,
                                line_idx,
                                raw_target,
                                f"Failed to read target file for anchors: {exc}",
                            )
                        )
                        continue

                valid_anchors = anchor_cache[target_path]
                if fragment not in valid_anchors:
                    broken.append(
                        BrokenLink(
                            file_path,
                            line_idx,
                            raw_target,
                            f"Anchor '#{fragment}' not found in {target_path.name}",
                        )
                    )

    return broken


def check_all_markdown_links(
    root: pathlib.Path | None = None,
    allowed_pending: set[str] | None = None,
) -> list[BrokenLink]:
    """Scan and verify all relative markdown links in repository."""
    repo_root = find_repo_root(root)
    anchor_cache: dict[pathlib.Path, set[str]] = {}
    all_broken: list[BrokenLink] = []

    ignore_dirs = {
        ".git",
        ".venv",
        ".worktrees",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    }

    for path in repo_root.rglob("*.md"):
        if any(part in ignore_dirs or part.startswith(".") for part in path.parts):
            continue
        broken = check_file_links(path, repo_root, anchor_cache, allowed_pending)
        all_broken.extend(broken)

    return all_broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check relative markdown links and anchors.")
    parser.add_argument("paths", nargs="*", help="Optional specific files or dirs to check")
    args = parser.parse_args(argv)

    repo_root = find_repo_root()

    if args.paths:
        anchor_cache: dict[pathlib.Path, set[str]] = {}
        all_broken: list[BrokenLink] = []
        for p in args.paths:
            path = pathlib.Path(p).resolve()
            if path.is_file() and path.suffix.lower() == ".md":
                all_broken.extend(check_file_links(path, repo_root, anchor_cache))
            elif path.is_dir():
                for sub in path.rglob("*.md"):
                    all_broken.extend(check_file_links(sub, repo_root, anchor_cache))
    else:
        all_broken = check_all_markdown_links(repo_root)

    if not all_broken:
        print("All relative links and anchors verified successfully.")
        return 0

    print(f"Found {len(all_broken)} broken relative link(s):", file=sys.stderr)
    for b in all_broken:
        try:
            rel = b.file_path.relative_to(repo_root).as_posix()
        except ValueError:
            rel = str(b.file_path)
        print(f"  {rel}:{b.line_number} -> {b.raw_target} ({b.reason})", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
