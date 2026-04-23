#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Prioritized scan for AI-slop patterns across backend + frontend.

Seven checks, grouped into HIGH / MED / LOW. HIGH findings flip exit
code to 1 so the command can gate a commit. See SKILL.md for the full
matrix.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_APP = REPO_ROOT / "backend" / "app"
BACKEND_ROUTES = BACKEND_APP / "routes"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
FRONTEND_APP = REPO_ROOT / "frontend" / "app"
TAILWIND_CONFIG = REPO_ROOT / "frontend" / "tailwind.config.ts"

IGNORED_DIR_PARTS: frozenset[str] = frozenset(
    {"node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}
)

# --- patterns ----------------------------------------------------------------

BANNER_RE = re.compile(r"^\s*(#|//)\s*={3,}\s*\S.*\S\s*={3,}\s*$")
TODO_CONSIDER_RE = re.compile(r"(?i)(?://|#)\s*TODO:\s*consider\b")
ISSUE_LINK_RE = re.compile(r"(#\d+|https?://\S+)")
AS_UNKNOWN_AS_RE = re.compile(r"\bas\s+unknown\s+as\s+\w")
ANY_SUPPRESS_RE = re.compile(
    r"(?://|/\*)\s*eslint-disable(?:-next-line|-line)?\s+[^\n*]*@typescript-eslint/no-explicit-any"
)
HEX_COLOR_RE = re.compile(r"(?<![\w#])#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
MOCK_DATA_FROM_RE = re.compile(r"""from\s+['"]([^'"]*\bmockData\b[^'"]*)['"]""")

HTTP_METHODS = frozenset({"get", "post", "patch", "put", "delete"})


# --- utilities ---------------------------------------------------------------


def _walk(root: Path, suffixes: Iterable[str]) -> Iterable[Path]:
    if not root.is_dir():
        return
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in suffixes:
            continue
        if any(part in IGNORED_DIR_PARTS for part in p.parts):
            continue
        yield p


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def _is_test_path(p: Path) -> bool:
    return any(part in {"test", "tests", "__tests__", "e2e"} for part in p.parts) or p.name.endswith(
        (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
    )


# --- checks ------------------------------------------------------------------


def check_underscore_depends() -> list[str]:
    out: list[str] = []
    if not BACKEND_ROUTES.is_dir():
        return out
    for py in sorted(BACKEND_ROUTES.glob("*.py")):
        if py.name == "__init__.py":
            continue
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            is_route = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "router"
                and d.func.attr in HTTP_METHODS
                for d in node.decorator_list
            )
            if not is_route:
                continue
            for arg in (*node.args.args, *node.args.kwonlyargs):
                if not arg.arg.startswith("_"):
                    continue
                if arg.annotation is None:
                    continue
                default_is_depends = False
                defaults = node.args.defaults + node.args.kw_defaults
                for default in defaults:
                    if (
                        isinstance(default, ast.Call)
                        and isinstance(default.func, ast.Name)
                        and default.func.id == "Depends"
                    ):
                        default_is_depends = True
                        break
                if default_is_depends:
                    out.append(
                        f"{_rel(py)}:{arg.lineno}  {node.name}() param `{arg.arg}` "
                        f"is Depends()-injected but underscore-prefixed"
                    )
    return out


def check_mock_data_imports() -> list[str]:
    out: list[str] = []
    for root in (FRONTEND_SRC, FRONTEND_APP):
        for p in _walk(root, (".ts", ".tsx")):
            if _is_test_path(p):
                continue
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                m = MOCK_DATA_FROM_RE.search(line)
                if m:
                    out.append(f"{_rel(p)}:{i}  from '{m.group(1)}'")
    return out


def check_as_unknown_as() -> list[str]:
    out: list[str] = []
    for root in (FRONTEND_SRC, FRONTEND_APP):
        for p in _walk(root, (".ts", ".tsx")):
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if AS_UNKNOWN_AS_RE.search(line):
                    out.append(f"{_rel(p)}:{i}  {line.strip()[:120]}")
    return out


def check_stacked_any_suppressions() -> list[str]:
    out: list[str] = []
    for root in (FRONTEND_SRC, FRONTEND_APP):
        for p in _walk(root, (".ts", ".tsx")):
            hits = [
                i
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1)
                if ANY_SUPPRESS_RE.search(line)
            ]
            if len(hits) >= 2:
                out.append(
                    f"{_rel(p)}  {len(hits)} `@typescript-eslint/no-explicit-any` "
                    f"suppressions at lines {hits}"
                )
    return out


def check_hardcoded_hex() -> list[str]:
    out: list[str] = []
    for root in (FRONTEND_SRC, FRONTEND_APP):
        for p in _walk(root, (".ts", ".tsx", ".css", ".scss")):
            if p.resolve() == TAILWIND_CONFIG.resolve():
                continue
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                m = HEX_COLOR_RE.search(line)
                if m:
                    out.append(f"{_rel(p)}:{i}  {m.group(0)}  {line.strip()[:120]}")
    return out


def check_banner_comments() -> list[str]:
    out: list[str] = []
    for root, suffixes in (
        (BACKEND_APP, (".py",)),
        (FRONTEND_SRC, (".ts", ".tsx")),
        (FRONTEND_APP, (".ts", ".tsx")),
    ):
        for p in _walk(root, suffixes):
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if BANNER_RE.match(line):
                    out.append(f"{_rel(p)}:{i}  {line.strip()[:120]}")
    return out


def check_todo_consider() -> list[str]:
    out: list[str] = []
    for root, suffixes in (
        (BACKEND_APP, (".py",)),
        (FRONTEND_SRC, (".ts", ".tsx")),
        (FRONTEND_APP, (".ts", ".tsx")),
    ):
        for p in _walk(root, suffixes):
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if TODO_CONSIDER_RE.search(line) and not ISSUE_LINK_RE.search(line):
                    out.append(f"{_rel(p)}:{i}  {line.strip()[:120]}")
    return out


# --- driver ------------------------------------------------------------------


CHECKS: list[tuple[str, str, str, callable]] = [  # type: ignore[type-arg]
    ("HIGH", "underscore-Depends", "Underscore-prefixed Depends() in a route handler", check_underscore_depends),
    ("HIGH", "mock-data-import", "mockData imported outside test files", check_mock_data_imports),
    ("MED", "as-unknown-as", "TypeScript `as unknown as X` cast", check_as_unknown_as),
    ("MED", "stacked-any-suppression", "≥2 `no-explicit-any` suppressions in one file", check_stacked_any_suppressions),
    ("MED", "hardcoded-hex", "Raw hex color outside tailwind.config.ts", check_hardcoded_hex),
    ("LOW", "banner-comment", "Banner-style divider comment", check_banner_comments),
    ("LOW", "todo-consider", "`TODO: consider…` with no issue link", check_todo_consider),
]


def main() -> int:
    results: dict[str, list[str]] = defaultdict(list)
    summary: list[tuple[str, str, int]] = []
    any_high = False

    for severity, key, desc, fn in CHECKS:
        findings = fn()
        results[severity].extend(
            f"[{key}] {line}" for line in findings
        )
        summary.append((severity, desc, len(findings)))
        if severity == "HIGH" and findings:
            any_high = True

    for severity in ("HIGH", "MED", "LOW"):
        entries = results[severity]
        if not entries:
            continue
        print(f"\n## {severity} ({len(entries)})\n")
        for line in entries:
            print(f"  {line}")

    total = sum(n for _, _, n in summary)
    print("\n---")
    print(f"slop-sweep summary: {total} finding(s)")
    for severity, desc, n in summary:
        flag = "!" if n and severity == "HIGH" else " "
        print(f"  {flag} {severity:<4} {n:>3}  {desc}")

    if any_high:
        print("\nHIGH findings fail the command. Fix them or justify in-review.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
