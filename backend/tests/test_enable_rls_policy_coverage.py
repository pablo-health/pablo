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
"""

from __future__ import annotations

import pytest
from app.db import enable_rls_on_schema


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
    assert "uploaded_by_user_id = current_setting('app.current_user_id', true)" in ddl
    assert "ENABLE ROW LEVEL SECURITY" in ddl


@pytest.mark.parametrize("table", ["ehr_routes", "users"])
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
    assert "user_id = current_setting('app.current_user_id', true)" in ddl
