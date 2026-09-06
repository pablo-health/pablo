"""Policy-coverage guards for ``enable_rls_on_schema``.

These exercise the per-table policy decision without a real Postgres by
driving the function with a fake session that records the DDL it would
run and serves a canned ``information_schema.columns`` result. The
invariant under test: a table is never left force-RLS'd with no policy
(a silent deny-all), which is the trap that hid behind the historical
BYPASSRLS-on-role posture.

The end-to-end RLS behavior (rows actually filtered) is covered by the
integration suite against a real database; this is the cheap unit-level
regression that the three deny-all tables (compliance_documents,
ehr_routes, users) would have tripped.

Two additional guards (L3 — runs in ``make test``, no DB required):

* ``test_every_real_tenant_table_is_classified`` — iterates every table
  in the real ORM metadata and asserts ``enable_rls_on_schema`` does NOT
  raise.  Catches a newly-added tenant table that has no policy branch
  before it reaches the real-Postgres integration suite.

* ``test_rls_forced_tables_are_covered_by_rls_invariants`` — asserts
  that every table returned by ``rls_forced_tenant_tables()`` is
  present in the effective coverage set used by
  ``test_rls_invariants.py``.  Closes the gap where a new RLS-forced
  table is auto-covered by RLS but silently skipped by the integration
  suite.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from app.db import (
    PATIENT_READABLE_TABLES,
    PATIENT_WRITABLE_TABLES,
    enable_rls_on_schema,
    rls_forced_tenant_tables,
)
from app.db.models import Base


class _FakeResult:
    def __init__(self, rows: list[tuple[str, str]]):
        self._rows = rows

    def fetchall(self) -> list[tuple[str, str]]:
        return self._rows


class _FakeSession:
    """Records executed SQL; answers the column query from a fixture."""

    def __init__(self, columns_by_table: dict[str, set[str]]):
        self._columns_by_table = columns_by_table
        self.executed: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append(sql)
        if sql.strip().upper().startswith("SELECT TABLE_NAME"):
            rows = [(table, col) for table, cols in self._columns_by_table.items() for col in cols]
            return _FakeResult(rows)
        return _FakeResult([])

    def commit(self) -> None:
        return None


def _run(columns_by_table: dict[str, set[str]]) -> _FakeSession:
    session = _FakeSession(columns_by_table)
    enable_rls_on_schema(session, "practice_test")  # type: ignore[arg-type]
    return session


def test_compliance_documents_gets_uploaded_by_user_id_policy() -> None:
    session = _run({"compliance_documents": {"id"}})
    ddl = " ".join(session.executed)
    assert "CREATE POLICY rls_user_isolation ON practice_test.compliance_documents" in ddl
    assert "uploaded_by_user_id::text = current_setting('app.current_user_id', true)" in ddl
    assert "ENABLE ROW LEVEL SECURITY" in ddl


@pytest.mark.parametrize("table", ["ehr_routes", "payers", "users"])
def test_not_row_scoped_tables_are_left_unforced(table: str) -> None:
    session = _run({table: {"id"}})
    ddl = " ".join(session.executed)
    assert f"ALTER TABLE practice_test.{table} DISABLE ROW LEVEL SECURITY" in ddl
    # Never force RLS or attach a policy to a not-row-scoped table.
    assert "FORCE ROW LEVEL SECURITY" not in ddl
    assert "CREATE POLICY" not in ddl


def test_unknown_id_only_table_raises_rather_than_deny_all() -> None:
    # A future table with only ``id`` and no policy shape must fail loud
    # instead of silently shipping a deny-all configuration.
    with pytest.raises(RuntimeError, match="no RLS policy defined"):
        _run({"some_new_widget": {"id"}})


def test_user_id_table_still_gets_isolation_policy() -> None:
    session = _run({"appointments": {"id", "user_id"}})
    ddl = " ".join(session.executed)
    assert "CREATE POLICY rls_user_isolation ON practice_test.appointments" in ddl
    assert "user_id::text = current_setting('app.current_user_id', true)" in ddl


# ---------------------------------------------------------------------------
# L3 unit guards — real ORM metadata, no DB
# ---------------------------------------------------------------------------

# Escape hatch for rls_forced_tenant_tables() entries that are not yet
# covered by test_rls_invariants.py.  Add a table here ONLY with a written
# reason and CODEOWNERS review; this set is empty by design today.
# (Mirrors the route-guardrail exemption pattern.)
EXEMPT_RLS_FORCED_TABLES: frozenset[str] = frozenset()
# ^ Add entries here ONLY with a written justification, e.g.:
#   frozenset({"some_table"})  # reason: <why coverage is deferred>


def _columns_for_rls(table: object) -> set[str]:
    """Return the subset of columns enable_rls_on_schema queries for."""
    return {c.name for c in table.columns} & {"user_id", "patient_id", "id"}  # type: ignore[union-attr]


def test_every_real_tenant_table_is_classified() -> None:
    """Every ORM table must map to a policy branch, not the deny-all guard.

    Drives ``enable_rls_on_schema`` via _FakeSession seeded with each
    table's REAL columns (intersected to {user_id, patient_id, id} as the
    function queries).  Asserts no RuntimeError is raised.

    On failure: the error message names the offending table.  Fix by adding
    a policy branch in ``enable_rls_on_schema`` OR listing it in
    ``not_row_scoped`` / ``register_overlay_not_row_scoped`` if it is not
    row-owned.
    """
    unclassified: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        cols = _columns_for_rls(table)
        if not cols:
            # Table has none of {user_id, patient_id, id} — it won't reach
            # the policy loop (e.g. ehr_prompts, alembic_version).  Skip.
            continue
        try:
            session = _FakeSession({table_name: cols})
            enable_rls_on_schema(session, "practice_test")  # type: ignore[arg-type]
        except RuntimeError:
            unclassified.append(table_name)

    assert not unclassified, (
        f"enable_rls_on_schema has no RLS policy defined for: {unclassified}. "
        "These tables would be force-RLS'd with no policy — a silent deny-all. "
        "Fix: add a policy branch in enable_rls_on_schema (backend/app/db/__init__.py) "
        "OR call register_overlay_not_row_scoped() for tables whose isolation boundary "
        "is the tenant schema, not a per-row predicate. "
        "See CLAUDE.md guardrail #4."
    )


def test_rls_forced_tables_are_covered_by_rls_invariants() -> None:
    """Every auto-derived RLS-forced table must be in the invariant suite.

    Parses TENANT_SCOPED_TABLES from test_rls_invariants.py (AST, no import)
    and checks that rls_forced_tenant_tables() minus EXEMPT_RLS_FORCED_TABLES
    is a subset of the effective coverage.

    On failure: the message names which tables to add to TENANT_SCOPED_TABLES in
    backend/tests_integration/database/test_rls_invariants.py and
    instructs the author to add a real-Postgres isolation test.
    See CLAUDE.md guardrail #4.
    """
    invariants_path = (
        Path(__file__).resolve().parents[1]
        / "tests_integration"
        / "database"
        / "test_rls_invariants.py"
    )
    tree = ast.parse(invariants_path.read_text())

    # Extract TENANT_SCOPED_TABLES from the AST (it's a module-level tuple literal).
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

    # Effective coverage = curated + auto-derived (mirrors _EFFECTIVE_TABLES).
    derived = rls_forced_tenant_tables()
    effective = curated | derived

    # Tables that are auto-derived but not yet in effective coverage.
    uncovered = (derived - EXEMPT_RLS_FORCED_TABLES) - effective

    assert not uncovered, (
        f"The following RLS-forced tables are not covered by the RLS "
        f"invariant suite: {sorted(uncovered)}. "
        f"Add each one to TENANT_SCOPED_TABLES in "
        f"backend/tests_integration/database/test_rls_invariants.py AND "
        f"add a real-Postgres isolation test proving the security boundary. "
        f"See CLAUDE.md guardrail #4. "
        f"If coverage must be deferred, add the table name to "
        f"EXEMPT_RLS_FORCED_TABLES in this file with a written reason."
    )


def test_patient_scoped_registry_names_real_tables_with_their_key_column() -> None:
    """Every patient-scoped registration must match a real table and column.

    ``enable_rls_on_schema`` raises when a registered table lacks its key
    column, which is the right runtime behaviour but only fires during
    provisioning. This catches the same mistake at unit-test speed, and
    catches the other direction too: a table renamed or dropped out from
    under the registry.
    """
    problems: list[str] = []
    for table_name, key_column in PATIENT_READABLE_TABLES.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            problems.append(f"{table_name}: registered patient-scoped but not an ORM table")
            continue
        if key_column not in {c.name for c in table.columns}:
            problems.append(
                f"{table_name}: registered on '{key_column}' which the table does not have"
            )

    assert not problems, (
        "PATIENT_READABLE_TABLES is out of sync with the schema: "
        f"{problems}. A registration whose key column is missing would make "
        "enable_rls_on_schema raise during tenant provisioning."
    )


def test_patient_writable_tables_are_a_subset_of_readable() -> None:
    """Write access must never be granted to a table a patient cannot read.

    The two registries are separate so granting reads never silently
    grants writes — but the reverse (writable without readable) would
    produce an UPDATE policy on a table with no patient SELECT arm, which
    is incoherent rather than merely strict.
    """
    orphans = set(PATIENT_WRITABLE_TABLES) - set(PATIENT_READABLE_TABLES)
    assert not orphans, (
        f"tables registered patient-writable but not patient-readable: {sorted(orphans)}"
    )


def test_patients_is_registered_read_only() -> None:
    """Core's single seed, pinned.

    A patient reads their own demographics; nothing in core lets them
    write that record. If this changes it should be a deliberate edit to
    this assertion, not a side effect.
    """
    assert PATIENT_READABLE_TABLES.get("patients") == "id"
    assert "patients" not in PATIENT_WRITABLE_TABLES
