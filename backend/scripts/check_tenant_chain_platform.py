# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Static guardrail: a tenant Alembic revision must not touch ``platform.``.

CLAUDE.md guardrail #4: the tenant chain is re-run once per practice schema and
after the platform chain, so platform DDL in it must be hand-idempotent and can
silently undo platform-side work. Platform DDL belongs in
``backend/alembic_platform/``. A tenant table may still *reference*
``platform.users`` (a foreign key creates nothing in platform), so these are
allowed:

  * ``ForeignKey("platform.users.id")`` / ``ForeignKeyConstraint`` targets
  * ``REFERENCES platform.users(id)`` inside raw SQL
  * ``platform.`` in docstrings and comments

Everything else (``schema="platform"``, ``ALTER TABLE platform.x``, DML against
platform tables, ...) is a violation unless the revision is in
``LEGACY_PLATFORM_REVISIONS``. That set is frozen: it may only shrink as
PABLO-k7it unwinds the chain. Pure-stdlib AST check, no DB.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

TENANT_VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Frozen: revisions that predate this guard. Only ever remove entries (PABLO-k7it).
LEGACY_PLATFORM_REVISIONS: frozenset[str] = frozenset(
    {
        "0924e5a542b6",
        "7d2e9f4a1b38",
        "931e7eda0911",
        "9f4c1a7b2e60",
        "a1d7f3c5e8b9",
        "a4c91b6e3f08",
        "a4f7e2c81b9d",
        "a7d3e9f14c62",
        "a7e3c5d19b42",
        "a8f3c7d2b916",
        "b2e7d4c91f05",
        "b2e8c4f6a1d3",
        "b2f9d3e1a6c4",
        "b523a68b7137",
        "c1d7e4a9f2b6",
        "c2e8a1f5d4b9",
        "c3f8b1d92e47",
        "c4d81e6a2f09",
        "c4e8b1f7a2d9",
        "c7f3e1a4b9d2",
        "c8d2f4a71e93",
        "d4b7e9a1c305",
        "d5a3e9b71f6c",
        "d7a3f1c8e2b4",
        "d8f3b6c04e17",
        "e480337eb6bd",
        "e6a1c9d4b7f2",
        "e7c4f1a8d293",
        "e8c54fe3d577",
        "f1c8d4a92b65",
        "f3a9c1e84b27",
        "f9b2e7d1a4c8",
    }
)

_PLATFORM_REF = re.compile(r"\bplatform\.")
_FK_REFERENCE = re.compile(r"REFERENCES\s+platform\.", re.IGNORECASE)
_FK_CALLS = {"ForeignKey", "ForeignKeyConstraint"}

_FIX_HINT = (
    'CLAUDE.md guardrail #4: "Put platform DDL in the platform chain, not the tenant chain" '
    "-- put it in backend/alembic_platform/ instead (regen: "
    "poetry run python backend/scripts/regen_platform_schema.py). Foreign keys INTO "
    "platform.users are fine."
)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _platform_uses(tree: ast.Module) -> list[int]:
    """Line numbers where the revision creates or alters something in platform."""
    skip = _docstring_nodes(tree)
    lines: list[int] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Call) and _call_name(node) in _FK_CALLS:
            return  # FK targets only point at platform; they create nothing there
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _PLATFORM_REF.search(_FK_REFERENCE.sub("", node.value))
        ):
            lines.append(node.lineno)
        if (
            isinstance(node, ast.keyword)
            and node.arg == "schema"
            and isinstance(node.value, ast.Constant)
            and node.value.value == "platform"
        ):
            lines.append(node.value.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return sorted(set(lines))


def _revision_id(tree: ast.Module) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(t, ast.Name) and t.id == "revision" for t in targets):
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
    return None


def platform_revisions(paths: list[Path] | None = None) -> dict[str, tuple[Path, list[int]]]:
    """Every tenant revision that creates/alters something in ``platform.``."""
    found: dict[str, tuple[Path, list[int]]] = {}
    for path in paths if paths is not None else sorted(TENANT_VERSIONS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _platform_uses(tree)
        revision = _revision_id(tree)
        if lines and revision:
            found[revision] = (path, lines)
    return found


def find_violations(paths: list[Path] | None = None) -> list[str]:
    return [
        f"{path.name}:{','.join(map(str, lines))} (revision {revision}) touches platform."
        for revision, (path, lines) in platform_revisions(paths).items()
        if revision not in LEGACY_PLATFORM_REVISIONS
    ]


def main(argv: list[str] | None = None) -> int:
    paths = [Path(a) for a in argv] if argv else None
    violations = find_violations(paths)
    if not violations:
        return 0
    print("Tenant-chain platform guardrail failed:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(f"\n{_FIX_HINT}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
