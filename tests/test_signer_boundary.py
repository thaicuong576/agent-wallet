"""Only core/gateway may import core.signer; the scanner also detects a planted violation."""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".worktrees", "node_modules", ".git", "tests", "__pycache__"}


def imports_signer(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
            if any(n == "core.signer" or n.startswith("core.signer.") for n in names):
                return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "core.signer" or module.startswith("core.signer."):
                return True
            if module == "core" and any(a.name == "signer" for a in node.names):
                return True
    return False


def violations(root: pathlib.Path) -> list[str]:
    found = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if SKIP_DIRS & set(rel.parts):
            continue
        if rel.parts[:2] in (("core", "gateway"), ("core", "signer")):
            continue
        if imports_signer(path.read_text(encoding="utf-8")):
            found.append(str(rel))
    return found


def test_only_gateway_imports_signer():
    assert violations(ROOT) == []


def test_scanner_detects_a_violation(tmp_path):
    (tmp_path / "evil.py").write_text("from core.signer import sign_and_send\n", encoding="utf-8")
    (tmp_path / "also_evil.py").write_text("import core.signer\n", encoding="utf-8")
    assert sorted(violations(tmp_path)) == ["also_evil.py", "evil.py"]
