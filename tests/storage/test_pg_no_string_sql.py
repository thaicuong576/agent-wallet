"""No string-built SQL in the storage package."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORAGE = ROOT / "core" / "storage"
SCAN = [
    *sorted((STORAGE / "postgres").glob("*.py")),
    STORAGE / "migrate.py",
    STORAGE / "factory.py",
]
EXECUTE_NAMES = {"execute", "executemany"}
# A migration file shipped with the code is the one non-literal statement, run without parameters.
ALLOWED_FUNCTIONS = {("migrate.py", "_run_migration_file")}


def _is_literal_sql(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return name == "SQL" and len(node.args) == 1 and _is_literal_sql(node.args[0])
    return False


def scan_source(source: str, filename: str = "<src>") -> list[str]:
    """Every execute call whose first argument is not a literal or sql.SQL(literal)."""
    tree = ast.parse(source)
    allowed = [
        (n.lineno, n.end_lineno or n.lineno)
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and (Path(filename).name, n.name) in ALLOWED_FUNCTIONS
    ]
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in EXECUTE_NAMES or not node.args:
            continue
        if any(lo <= node.lineno <= hi for lo, hi in allowed):
            continue
        if not _is_literal_sql(node.args[0]):
            found.append(f"{filename}:{node.lineno}: execute argument is not a string literal")
    return found


def test_no_string_built_sql() -> None:
    assert len(SCAN) >= 5, "nothing to scan"
    problems: list[str] = []
    for path in SCAN:
        problems += scan_source(path.read_text(encoding="utf-8"), str(path))
    assert problems == []


def test_scanner_detects_string_built_sql() -> None:
    bad = (
        "def f(cur, x):\n"
        "    cur.execute(f'SELECT {x}')\n"
        "    cur.execute('SELECT ' + x)\n"
        "    cur.execute('SELECT %s'.format(x))\n"
        "    cur.execute(x)\n"
    )
    assert len(scan_source(bad)) == 4


def test_scanner_accepts_literals() -> None:
    good = (
        "def f(cur, sql):\n"
        "    cur.execute('SELECT %s', (1,))\n"
        "    cur.execute('SELECT '\n"
        "                'x')\n"
        "    cur.execute(sql.SQL('SELECT 1'))\n"
    )
    assert scan_source(good) == []


def test_only_the_migration_file_runner_is_exempt() -> None:
    assert ALLOWED_FUNCTIONS == {("migrate.py", "_run_migration_file")}
    other = "def other(conn, text):\n    conn.execute(text)\n"
    assert scan_source(other, "migrate.py") != []
