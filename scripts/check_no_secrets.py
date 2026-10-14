"""Fail if a tracked file looks like it holds a secret. Read-only; run by `make check` and CI."""

import pathlib
import re
import subprocess
import sys

PATTERNS = {
    "private key assignment": re.compile(r"PRIVATE_KEY\s*=\s*['\"]?(0x)?[0-9a-fA-F]{64}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "PEM private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "seed phrase assignment": re.compile(r"(MNEMONIC|SEED_PHRASE)\s*=\s*['\"]?\w+( \w+){10,}"),
}
FORBIDDEN_TRACKED = re.compile(r"(^|/)\.env($|\.(?!example$))")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def main() -> int:
    problems = []
    for rel in tracked_files():
        if FORBIDDEN_TRACKED.search(rel):
            problems.append(f"{rel}: environment file is tracked")
            continue
        path = pathlib.Path(rel)
        if not path.is_file() or path.suffix in {".pdf", ".png", ".jpg"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{rel}: looks like a {label}")
    for p in problems:
        print("SECRET-SCAN:", p)
    print("secret scan:", "FAIL" if problems else "ok", f"({len(tracked_files())} tracked files)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
