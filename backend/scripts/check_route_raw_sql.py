# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Static guardrail #3: no raw SQL in route handlers.

Code under ``app/routes/`` must not run a query itself
(``session.execute(select(...))`` and its kin); it goes through
``app/repositories/``, which keeps tenant scoping and error shapes in one
place. Pure-stdlib AST check, so CI runs it (via
``test_route_raw_sql_guardrail.py``) without importing the app.

A call is a raw query when its method is ``execute`` / ``scalars`` /
``scalar`` and either its first argument is built from a SQL statement
constructor (``select``, ``insert``, ``update``, ``delete``, ``text``, also
as ``sa.select`` and so on) or it is called on a database handle named
``session``, ``db``, ``conn`` or ``connection``. That keeps out unrelated
``.execute()`` methods, such as a scheduler's.

Sites older than the rule sit in ``route_raw_sql_baseline.txt``, one line
per call, and the baseline only shrinks: a new site fails, and so does a
line whose site is gone.

Run: ``python backend/scripts/check_route_raw_sql.py`` (exits 1 with the list
of violations, 0 if clean).
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
ROUTES_ROOT = _BACKEND / "app" / "routes"
BASELINE = _BACKEND / "scripts" / "route_raw_sql_baseline.txt"

_QUERY_METHODS = frozenset({"execute", "scalars", "scalar"})
_STATEMENT_CONSTRUCTORS = frozenset({"select", "insert", "update", "delete", "text"})
_DB_HANDLES = frozenset({"session", "db", "conn", "connection"})


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _builds_statement(node: ast.expr) -> bool:
    """True when ``node`` is a chain like ``select(X).where(...)`` rooted in a
    statement constructor."""
    innermost: ast.Call | None = None
    while isinstance(node, ast.Call | ast.Attribute):
        if isinstance(node, ast.Call):
            innermost = node
            node = node.func
        else:
            node = node.value
    return innermost is not None and _called_name(innermost.func) in _STATEMENT_CONSTRUCTORS


def _is_raw_query(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in _QUERY_METHODS:
        return False
    if call.args and _builds_statement(call.args[0]):
        return True
    return _called_name(call.func.value) in _DB_HANDLES


def _find_sites(node: ast.AST, scope: list[str], sites: list[tuple[int, str]]) -> None:
    """Append ``(line, dotted enclosing function)`` for each raw query under ``node``."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            _find_sites(child, [*scope, child.name], sites)
            continue
        if isinstance(child, ast.Call) and _is_raw_query(child):
            sites.append((child.lineno, ".".join(scope) or "<module>"))
        _find_sites(child, scope, sites)


def raw_sql_sites(files: list[Path], root: Path = ROUTES_ROOT) -> list[tuple[str, int, str]]:
    """``(path relative to app/'s parent, line, enclosing function)`` per raw query."""
    sites: list[tuple[str, int, str]] = []
    for py_file in files:
        rel = py_file.resolve().relative_to(root.resolve().parent.parent).as_posix()
        found: list[tuple[int, str]] = []
        _find_sites(ast.parse(py_file.read_text()), [], found)
        sites.extend((rel, line, func) for line, func in found)
    return sorted(sites)


def load_baseline(path: Path = BASELINE) -> Counter[str]:
    """Read the grandfathered ``path:function`` lines (``#`` comments ignored)."""
    if not path.is_file():
        return Counter()
    lines = (line.strip() for line in path.read_text().splitlines())
    return Counter(line for line in lines if line and not line.startswith("#"))


def route_files(root: Path = ROUTES_ROOT) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def raw_sql_violations(
    files: list[Path], *, root: Path = ROUTES_ROOT, baseline: Counter[str] | None = None
) -> list[str]:
    """New raw-query sites, plus baseline lines whose site no longer exists."""
    if baseline is None:
        baseline = load_baseline()
    remaining = Counter(baseline)
    out: list[str] = []
    for rel, line, func in raw_sql_sites(files, root):
        key = f"{rel}:{func}"
        if remaining[key] > 0:
            remaining[key] -= 1
            continue
        out.append(
            f"{rel}:{line} {func} runs a query in a route handler. Move it into a "
            f"repository under app/repositories/ and call that (CLAUDE.md guardrail #3)"
        )
    out.extend(
        f"{BASELINE.name} lists `{key}` once more than there are raw queries in it: "
        f"delete the line (the baseline only shrinks)"
        for key, count in sorted(remaining.items())
        for _ in range(count)
    )
    return out


def main() -> int:
    violations = raw_sql_violations(route_files())
    if not violations:
        return 0
    print("Route raw-SQL guardrail failed:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
