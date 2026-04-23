#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Report PHI-touching route handlers that skip AuditService.

Enforces CLAUDE.md guardrail #1. Walks every module in
``backend/app/routes/`` with the ``ast`` module, resolves each handler's
full URL (``APIRouter(prefix=...)`` + decorator path), and flags any
handler whose URL matches a PHI marker but either does not inject
``audit: AuditService`` or does not call ``audit.<helper>(...)`` in its
body. Output is a markdown table plus quick-fix snippets, exit code 1
when anything is flagged.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTES_DIR = REPO_ROOT / "backend" / "app" / "routes"

PHI_PATH_MARKERS: tuple[str, ...] = (
    "/patients",
    "/sessions",
    "/appointments",
    "/transcript",
    "/audio",
    "/soap",
    "/client",
)

HTTP_METHODS: frozenset[str] = frozenset({"get", "post", "patch", "put", "delete"})


def _router_prefix(tree: ast.Module) -> str:
    """Return the ``prefix=...`` kwarg from ``router = APIRouter(...)``, or ''."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if node.targets[0].id != "router":
            continue
        if not isinstance(node.value, ast.Call):
            continue
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                value = kw.value.value
                if isinstance(value, str):
                    return value
    return ""


def _handler_decorator(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, str] | None:
    """Return (method, decorator_path) for @router.<method>('<path>') handlers."""
    for dec in func.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        if not isinstance(dec.func, ast.Attribute):
            continue
        if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != "router":
            continue
        if dec.func.attr not in HTTP_METHODS:
            continue
        if not dec.args or not isinstance(dec.args[0], ast.Constant):
            continue
        path = dec.args[0].value
        if isinstance(path, str):
            return dec.func.attr, path
    return None


def _injects_audit_service(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for arg in (*func.args.args, *func.args.kwonlyargs):
        if arg.annotation is not None and "AuditService" in ast.unparse(arg.annotation):
            return True
    return False


def _calls_audit(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "audit":
            return True
    return False


def main() -> int:
    if not ROUTES_DIR.is_dir():
        print(f"routes dir not found: {ROUTES_DIR}", file=sys.stderr)
        return 2

    findings: list[dict[str, str]] = []

    for py_file in sorted(ROUTES_DIR.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text())
        prefix = _router_prefix(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            dec = _handler_decorator(node)
            if dec is None:
                continue
            method, dec_path = dec
            full_path = f"{prefix}{dec_path}" if dec_path else prefix
            if not any(marker in full_path for marker in PHI_PATH_MARKERS):
                continue

            injects = _injects_audit_service(node)
            calls = _calls_audit(node)
            if injects and calls:
                continue

            if not injects:
                violation = "missing `audit: AuditService` parameter"
                fix = (
                    "    audit: AuditService = Depends(get_audit_service),\n"
                    "    # ... then in the body: await audit.log_<action>(...)"
                )
            else:
                violation = "injects AuditService but never calls audit.*"
                fix = (
                    "    # add before returning:\n"
                    f"    await audit.log_<action>(actor=ctx.user.id, resource_id=...)"
                )

            findings.append(
                {
                    "file": f"{py_file.relative_to(REPO_ROOT)}:{node.lineno}",
                    "method": method.upper(),
                    "path": full_path,
                    "func": node.name,
                    "violation": violation,
                    "fix": fix,
                }
            )

    if not findings:
        print("audit-coverage-check: clean - every PHI route injects and calls AuditService.")
        return 0

    print("# Audit Coverage Violations\n")
    print("| File:Line | Method | Path | Handler | Violation |")
    print("|-----------|--------|------|---------|-----------|")
    for f in findings:
        print(
            f"| `{f['file']}` | {f['method']} | `{f['path']}` "
            f"| `{f['func']}` | {f['violation']} |"
        )

    print("\n## Quick fixes\n")
    for f in findings:
        print(f"### `{f['file']}` — `{f['func']}`")
        print("```python")
        print(f["fix"])
        print("```\n")

    print(
        "\nSee CLAUDE.md guardrail #1 and "
        "`backend/tests/test_route_audit_guardrails.py`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
