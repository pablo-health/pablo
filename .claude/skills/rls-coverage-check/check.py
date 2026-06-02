#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""RLS coverage check — pure Python, no DB required.

Reports two classes of problems:

1. **Unclassified tenant tables** — ORM tables whose column shape would
   cause ``enable_rls_on_schema`` to raise RuntimeError (the deny-all
   guard), meaning the table would be force-RLS'd with no policy.

2. **Uncovered RLS-forced tables** — tables returned by
   ``rls_forced_tenant_tables()`` that are not listed in
   ``TENANT_SCOPED_TABLES`` in ``test_rls_invariants.py`` and not in
   ``EXEMPT_RLS_FORCED_TABLES``.

Exit 0 when clean; non-zero on any finding with an actionable message.

Run from the repo root:
    python .claude/skills/rls-coverage-check/check.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup: add backend/ to sys.path so app.* imports resolve.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# ---------------------------------------------------------------------------
# Import the app modules (no DB connection needed — Base.metadata is built
# at import time by the ORM declarative machinery).
# ---------------------------------------------------------------------------
from app.db import enable_rls_on_schema, rls_forced_tenant_tables  # noqa: E402  # isort: skip
from app.db.models import Base  # noqa: E402  # isort: skip


# ---------------------------------------------------------------------------
# Escape hatch: tables exempt from the coverage requirement.
# Empty by design. Add entries ONLY with a written reason + CODEOWNERS review.
# ---------------------------------------------------------------------------
EXEMPT_RLS_FORCED_TABLES: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# _FakeSession: mirrors the harness in test_enable_rls_policy_coverage.py
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str, str]]:
        return self._rows


class _FakeSession:
    """Records executed SQL; answers the column query from a fixture."""

    def __init__(self, columns_by_table: dict[str, set[str]]) -> None:
        self._columns_by_table = columns_by_table

    def execute(self, statement: object, params: object = None) -> _FakeResult:  # noqa: ARG002
        sql = str(statement)
        if sql.strip().upper().startswith("SELECT TABLE_NAME"):
            rows = [
                (table, col)
                for table, cols in self._columns_by_table.items()
                for col in cols
            ]
            return _FakeResult(rows)
        return _FakeResult([])

    def commit(self) -> None:
        return None


def _columns_for_rls(table: object) -> set[str]:
    """Return the subset of columns enable_rls_on_schema queries for."""
    return {c.name for c in table.columns} & {"user_id", "patient_id", "id"}  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Check 1: unclassified tenant tables
# ---------------------------------------------------------------------------

def _find_unclassified() -> list[str]:
    unclassified: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        cols = _columns_for_rls(table)
        if not cols:
            # No scoping columns at all — won't reach the policy loop.
            continue
        try:
            session = _FakeSession({table_name: cols})
            enable_rls_on_schema(session, "practice_test")  # type: ignore[arg-type]
        except RuntimeError:
            unclassified.append(table_name)
    return sorted(unclassified)


# ---------------------------------------------------------------------------
# Check 2: patient-access tables not in invariant suite
# ---------------------------------------------------------------------------

def _parse_tenant_scoped_tables() -> set[str]:
    """Parse TENANT_SCOPED_TABLES from test_rls_invariants.py via AST."""
    invariants_path = (
        REPO_ROOT / "backend" / "tests_integration" / "database" / "test_rls_invariants.py"
    )
    tree = ast.parse(invariants_path.read_text())
    curated: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TENANT_SCOPED_TABLES":
                    if isinstance(node.value, ast.Tuple):
                        curated = {
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
                    break
    return curated


def _find_uncovered() -> list[str]:
    curated = _parse_tenant_scoped_tables()
    derived = rls_forced_tenant_tables()
    effective = curated | derived
    uncovered = (derived - EXEMPT_RLS_FORCED_TABLES) - effective
    return sorted(uncovered)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    findings: list[str] = []

    unclassified = _find_unclassified()
    if unclassified:
        findings.append(
            "rls-coverage-check: FAIL — unclassified tenant tables "
            "(enable_rls_on_schema has no policy for them):\n"
            + "".join(f"  - {t}\n" for t in unclassified)
            + "\nFix: add a policy branch in enable_rls_on_schema "
            "(backend/app/db/__init__.py) OR call "
            "register_overlay_not_row_scoped() for tables whose isolation "
            "boundary is the tenant schema. See CLAUDE.md guardrail #4."
        )

    uncovered = _find_uncovered()
    if uncovered:
        findings.append(
            "rls-coverage-check: FAIL — RLS-forced tables not "
            "covered by the RLS invariant suite:\n"
            + "".join(f"  - {t}\n" for t in uncovered)
            + "\nFix:\n"
            "  1. Add each table to TENANT_SCOPED_TABLES in\n"
            "     backend/tests_integration/database/test_rls_invariants.py\n"
            "  2. Add a real-Postgres isolation test proving the security "
            "boundary (see CLAUDE.md guardrail #4).\n"
            "  3. If coverage must be deferred, add the table to "
            "EXEMPT_RLS_FORCED_TABLES in this script with a reason."
        )

    if findings:
        for msg in findings:
            print(msg, file=sys.stderr)
        return 1

    derived_count = len(rls_forced_tenant_tables())
    print(
        f"rls-coverage-check: OK — "
        f"{len(Base.metadata.tables)} ORM tables classified, "
        f"{derived_count} RLS-forced table(s) covered."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
