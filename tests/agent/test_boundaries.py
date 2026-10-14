"""Boundary tests: core.agent must never import signer, gateway internals, or tiers.owner."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "core" / "agent"

FORBIDDEN_MODULES = (
    "core.signer",
    "core.gateway",
    "core.tiers.owner",
)


def find_forbidden_imports(source: str) -> list[str]:
    """Inspect AST for forbidden module imports."""
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in FORBIDDEN_MODULES:
                    if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                        found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for forbidden in FORBIDDEN_MODULES:
                if mod == forbidden or mod.startswith(f"{forbidden}."):
                    found.append(mod)
                # Check from core import signer / gateway / tiers
                if forbidden.startswith("core."):
                    sub = forbidden.split(".", 1)[1]
                    if mod == "core" and any(a.name == sub for a in node.names):
                        found.append(f"core.{sub}")
                    if "." in sub:
                        parent, child = sub.split(".", 1)
                        if mod == f"core.{parent}" and any(a.name == child for a in node.names):
                            found.append(forbidden)
    return found


def test_agent_package_never_imports_signer_gateway_or_owner_tiers():
    """Verify core.agent package does not import signer, gateway, or owner tiers."""
    assert AGENT_DIR.is_dir()
    py_files = list(AGENT_DIR.glob("**/*.py"))
    assert len(py_files) >= 3  # __init__.py, client.py, reader.py, planner.py

    violations: dict[str, list[str]] = {}
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        found = find_forbidden_imports(content)
        if found:
            violations[str(py_file.relative_to(ROOT))] = found

    assert violations == {}, f"Forbidden imports detected in agent package: {violations}"


def test_boundary_scanner_detects_planted_violations():
    """Verify test scanner correctly detects planted violations."""
    fake_source1 = "import core.signer\n"
    fake_source2 = "from core.gateway.gateway import Gateway\n"
    fake_source3 = "from core.tiers.owner import OwnerTierManager\n"
    fake_source4 = "from core import signer\n"

    assert find_forbidden_imports(fake_source1) == ["core.signer"]
    assert find_forbidden_imports(fake_source2) == ["core.gateway.gateway"]
    assert find_forbidden_imports(fake_source3) == ["core.tiers.owner"]
    assert find_forbidden_imports(fake_source4) == ["core.signer"]
