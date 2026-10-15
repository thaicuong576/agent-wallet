"""The runtime image has no httpx (dev-only): production RPC paths must not import it."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_DIRS = ("core", "api")


def test_runtime_code_does_not_import_httpx() -> None:
    offenders = []
    for d in RUNTIME_DIRS:
        for path in (ROOT / d).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "import httpx" in text or "from httpx" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"httpx is a dev dependency; offenders: {offenders}"
