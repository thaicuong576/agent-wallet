"""Only core.gateway may import core.gateway.intents; boundary test."""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".worktrees", "node_modules", ".git", "tests", "__pycache__"}


def imports_intents(source: str) -> bool:
    """Return True if source AST imports core.gateway.intents."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
            if any(
                n == "core.gateway.intents" or n.startswith("core.gateway.intents.") for n in names
            ):
                return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "core.gateway.intents" or module.startswith("core.gateway.intents."):
                return True
            if module == "core.gateway" and any(
                a.name in ("intents", "IntentStore") for a in node.names
            ):
                return True
    return False


def violations(root: pathlib.Path) -> list[str]:
    """Find files outside core/gateway that import core.gateway.intents."""
    found = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if SKIP_DIRS & set(rel.parts):
            continue
        if rel.parts[:2] == ("core", "gateway"):
            continue
        if imports_intents(path.read_text(encoding="utf-8")):
            found.append(str(rel))
    return found


def test_only_gateway_imports_intents() -> None:
    """Verify that only core.gateway imports order_intents / IntentStore."""
    assert violations(ROOT) == []


def test_scanner_detects_a_violation(tmp_path: pathlib.Path) -> None:
    """Verify the boundary scanner detects planted violations."""
    (tmp_path / "evil.py").write_text(
        "from core.gateway.intents import IntentStore\n", encoding="utf-8"
    )
    (tmp_path / "also_evil.py").write_text("import core.gateway.intents\n", encoding="utf-8")
    assert sorted(violations(tmp_path)) == ["also_evil.py", "evil.py"]
