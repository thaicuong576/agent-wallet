"""Only core/gateway may import core.signer; the scanner also detects a planted violation."""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".worktrees", "node_modules", ".git", "tests", "__pycache__"}
ALLOWED_SIGNER = (("core", "gateway"), ("core", "signer"))
ALLOWED_CUSTODY = (("core", "gateway"),)


def imports_module(source: str, dotted: str) -> bool:
    """True if the source imports `dotted` (a module) or one of its submodules."""
    parent, _, leaf = dotted.rpartition(".")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
            if any(n == dotted or n.startswith(dotted + ".") for n in names):
                return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == dotted or module.startswith(dotted + "."):
                return True
            if module == parent and any(a.name == leaf for a in node.names):
                return True
    return False


def imports_signer(source: str) -> bool:
    return imports_module(source, "core.signer")


def imports_custody(source: str) -> bool:
    return imports_module(source, "core.gateway.custody")


def violations(root: pathlib.Path, check=imports_signer, allowed=ALLOWED_SIGNER) -> list[str]:
    found = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if SKIP_DIRS & set(rel.parts):
            continue
        if rel.parts[:2] in allowed:
            continue
        if check(path.read_text(encoding="utf-8")):
            found.append(str(rel))
    return found


def test_only_gateway_imports_signer():
    assert violations(ROOT) == []


def test_scanner_detects_a_violation(tmp_path):
    (tmp_path / "evil.py").write_text("from core.signer import sign_and_send\n", encoding="utf-8")
    (tmp_path / "also_evil.py").write_text("import core.signer\n", encoding="utf-8")
    assert sorted(violations(tmp_path)) == ["also_evil.py", "evil.py"]


def test_only_gateway_imports_custody():
    assert violations(ROOT, imports_custody, ALLOWED_CUSTODY) == []


def test_custody_scanner_detects_a_violation(tmp_path):
    (tmp_path / "a.py").write_text("import core.gateway.custody\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "from core.gateway.custody import open_signer\n", encoding="utf-8"
    )
    (tmp_path / "c.py").write_text("from core.gateway import custody\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("from core.gateway import tenant\n", encoding="utf-8")
    found = violations(tmp_path, imports_custody, ALLOWED_CUSTODY)
    assert sorted(found) == ["a.py", "b.py", "c.py"]
