"""The runner module must not reach the signer, owner controls or the intent store."""

from __future__ import annotations

import ast
import pathlib

RUNNER_DIR = pathlib.Path(__file__).resolve().parents[2] / "core" / "runner"
FORBIDDEN = ("core.signer", "core.tiers.owner", "core.gateway.intents")


def forbidden_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module] + [f"{module}.{a.name}" for a in node.names]
        else:
            continue
        for name in names:
            if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
                found.append(name)
    return found


def test_runner_imports() -> None:
    offenders = {
        p.name: forbidden_imports(p.read_text(encoding="utf-8")) for p in RUNNER_DIR.glob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_scanner_detects_planted_violations() -> None:
    assert forbidden_imports("from core.signer import Signer")
    assert forbidden_imports("import core.tiers.owner")
    assert forbidden_imports("from core.tiers import owner")
    assert forbidden_imports("from core.gateway.intents import IntentStore")
    assert forbidden_imports("from core.gateway.gateway import Gateway") == []
