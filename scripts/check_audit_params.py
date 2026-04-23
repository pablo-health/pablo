#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pre-commit hook: forbid underscore-prefixed _audit / _http_request parameters
in route handlers under backend/app/routes/.

The underscore prefix silences every linter by telling Python the parameter
is intentionally unused. In a PHI-touching route handler, that's a silent
bypass of the HIPAA § 164.312(b) audit-logging guardrail (CLAUDE.md #1).

This runs on every commit and blocks merges before CI even starts — so the
regression pattern we found in sessions.py can't land again.

The pytest in backend/tests/test_route_audit_guardrails.py does the same
check (plus stronger ones) at CI time; this is the fast local gate.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = REPO_ROOT / "backend" / "app" / "routes"

FORBIDDEN = {"_audit", "_http_request"}
HTTP_METHODS = {"get", "post", "patch", "put", "delete"}


def _is_route_handler(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        if not isinstance(dec.func, ast.Attribute):
            continue
        if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != "router":
            continue
        if dec.func.attr in HTTP_METHODS:
            return True
    return False


def main() -> int:
    violations: list[str] = []
    for py_file in sorted(ROUTES_DIR.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not _is_route_handler(node):
                continue
            for arg in (*node.args.args, *node.args.kwonlyargs):
                if arg.arg in FORBIDDEN:
                    violations.append(
                        f"{py_file.relative_to(REPO_ROOT)}:{arg.lineno}: "
                        f"{node.name}() declares forbidden parameter `{arg.arg}` — "
                        f"rename to `{arg.arg.lstrip('_')}` and call it."
                    )

    if violations:
        print("Route handler audit-param check failed:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nUnderscore-prefixed _audit / _http_request silences every linter "
            "while leaving PHI access unaudited. See CLAUDE.md guardrail #1.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
