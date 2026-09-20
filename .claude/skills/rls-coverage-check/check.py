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

from sqlalchemy import Uuid

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
from app.db import (  # noqa: E402  # isort: skip
    PATIENT_READABLE_TABLES,
    PATIENT_WRITABLE_TABLES,
    enable_rls_on_schema,
    rls_forced_tenant_tables,
)
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
    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str, ...]]:
        return self._rows


class _FakeSession:
    """Records executed SQL; answers the column query from a fixture.

    The column query returns ``(table_name, column_name, data_type)``:
    the policy builder reads ``patient_id``'s type to decide whether
    ``has_patient_access`` has an overload for it, so the fixture answers
    with the type the ORM declares rather than a guess from the name.
    """

    def __init__(
        self,
        columns_by_table: dict[str, set[str]],
        types_by_table: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._columns_by_table = columns_by_table
        self._types_by_table = types_by_table or {}

    def execute(self, statement: object, params: object = None) -> _FakeResult:  # noqa: ARG002
        sql = str(statement)
        if sql.strip().upper().startswith("SELECT TABLE_NAME"):
            rows: list[tuple[str, ...]] = [
                (table, col, self._types_by_table.get(table, {}).get(col, "text"))
                for table, cols in self._columns_by_table.items()
                for col in cols
            ]
            return _FakeResult(rows)
        return _FakeResult([])

    def commit(self) -> None:
        return None


def _sql_type(column: object) -> str:
    """``uuid`` for a UUID-typed ORM column, ``text`` for anything else.

    ``Uuid`` is the base of every UUID type SQLAlchemy emits, including the
    PostgreSQL dialect's; ``python_type`` is not a reliable tell because a
    column declared ``Uuid(as_uuid=False)`` reports ``str``.
    """
    return "uuid" if isinstance(column.type, Uuid) else "text"  # type: ignore[attr-defined]


def _columns_for_rls(table: object) -> set[str]:
    """Return the columns enable_rls_on_schema needs to see for this table.

    The clinician policy branches switch on ``{user_id, patient_id, id}``,
    but the patient arm also checks that the column a table is registered
    on is present — and registrations are not limited to those three
    (``chat_messages`` is registered on ``conversation_id``). Mirrors
    ``_columns_for_rls`` in ``test_enable_rls_policy_coverage.py``.
    """
    names = {c.name for c in table.columns}  # type: ignore[union-attr]
    shape_columns = names & {"user_id", "patient_id", "id"}
    registered_on = {
        col
        for registry in (PATIENT_READABLE_TABLES, PATIENT_WRITABLE_TABLES)
        for tbl, col in registry.items()
        if tbl == table.name  # type: ignore[union-attr]
    }
    return shape_columns | (registered_on & names)


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
        types = {
            c.name: _sql_type(c)
            for c in table.columns  # type: ignore[union-attr]
            if c.name in cols
        }
        try:
            session = _FakeSession({table_name: cols}, {table_name: types})
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
