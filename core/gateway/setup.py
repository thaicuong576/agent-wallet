"""First-run secrets helper: python -m core.gateway.setup [--env-file PATH].

Generates AGENT_WALLET_KEYSTORE_SECRET and AGENT_WALLET_SESSION_SECRET into the env file only
when absent. Never prints a value, never overwrites a name that is already present.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path

NAMES = ("AGENT_WALLET_KEYSTORE_SECRET", "AGENT_WALLET_SESSION_SECRET")


def _present(text: str) -> set[str]:
    found = set()
    for name in NAMES:
        if re.search(rf"^\s*(export\s+)?{re.escape(name)}\s*=", text, re.MULTILINE):
            found.add(name)
    return found


def generate(env_file: Path) -> tuple[list[str], list[str]]:
    """Append missing secrets to env_file. Returns (created names, already present names)."""
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    present = _present(text)
    missing = [n for n in NAMES if n not in present]
    if missing:
        prefix = "" if not text or text.endswith("\n") else "\n"
        lines = "".join(f"{n}={secrets.token_hex(32)}\n" for n in missing)
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (prefix + lines).encode("utf-8"))
        finally:
            os.close(fd)
    return missing, sorted(present)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate first-run secrets into .env")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    created, present = generate(Path(args.env_file))
    for name in created:
        print(f"{name}: generated (value not shown)")
    for name in present:
        print(f"{name}: already set, left unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
