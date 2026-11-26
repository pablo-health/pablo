# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Changes to the applier that mutation testing found no test noticed.

Each test here pins one behaviour a surviving mutant showed was unguarded:
statements running with nothing else on the search path, a policy the file
does not comment on staying uncommented, a stray name that needs quoting, a
lost comment costing a rebuild without being called a change, and the log
naming the tables the applier cannot act on.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db.rls_apply import APPLIED_PREFIX, DesiredTable, apply_tenant_rls
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

from .helpers import (
    _COLLEAGUE,
    _TEMPLATE,
    _apply,
    _ddl,
    _drop_schema,
    _patients_read_by,
    _policies,
    _policy_oids,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Requires Postgres",
)


def _with_extra_patients_policy(
    desired: dict[str, DesiredTable], name: str, statement: str
) -> dict[str, DesiredTable]:
    patients = desired["patients"]
    return {
        **desired,
        "patients": DesiredTable(
            enabled=patients.enabled,
            forced=patients.forced,
            policies={**patients.policies, name: statement},
            comments=dict(patients.comments),
        ),
    }


def test_the_sqls_statements_run_with_nothing_else_on_the_search_path(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """A name the SQL leaves unqualified must not resolve through the caller's path.

    The caller here has the practice schema on its path, where
    ``has_patient_access`` lives. The statement names that function with no
    schema, as the file never does, and has to fail: if it resolved, a policy
    could be bound to whatever the session happened to have in front.
    """
    unqualified = _with_extra_patients_policy(
        desired,
        "rls_unqualified",
        "CREATE POLICY rls_unqualified ON __TENANT_SCHEMA__.patients FOR SELECT "
        "USING (has_patient_access(id, 'nobody'::character varying));",
    )

    with OrmSession(engine) as session:
        session.execute(text(f"SET search_path = {practice}, platform, public"))
        # The driver's own error: the statements go through a bare cursor.
        with pytest.raises(Exception, match=r"function has_patient_access.* does not exist"):
            apply_tenant_rls(session, practice, unqualified)
        session.rollback()

    assert ("patients", "rls_unqualified") not in _policies(engine, practice)


def test_a_policy_the_sql_does_not_comment_on_gets_no_comment(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    extended = _with_extra_patients_policy(
        desired,
        "rls_plain",
        "CREATE POLICY rls_plain ON __TENANT_SCHEMA__.patients "
        "AS RESTRICTIVE FOR SELECT USING (true);",
    )

    assert _apply(engine, practice, extended).altered == 1

    with engine.connect() as conn:
        comment = conn.execute(
            text(
                "SELECT obj_description(p.oid, 'pg_policy') FROM pg_policy p "
                "JOIN pg_class c ON c.oid = p.polrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relname = 'patients' AND p.polname = 'rls_plain'"
            ),
            {"s": practice},
        ).scalar_one()
    assert comment is None


def test_a_stray_policy_whose_name_needs_quoting_is_dropped(
    engine: Engine, practice: str, desired: dict[str, DesiredTable]
) -> None:
    """Live names come from the catalog and can be anything a person typed."""
    odd = 'Left "by hand"'
    quoted = '"' + odd.replace('"', '""') + '"'
    _ddl(engine, f'CREATE POLICY {quoted} ON "{practice}".patients FOR SELECT USING (true)')
    assert ("patients", odd) in _policies(engine, practice)

    assert _apply(engine, practice, desired).altered == 1

    assert ("patients", odd) not in _policies(engine, practice)
    assert _patients_read_by(engine, practice, _COLLEAGUE) == 0


def test_a_wrong_comment_costs_a_rebuild_and_is_not_reported_as_a_change(
    engine: Engine, desired: dict[str, DesiredTable]
) -> None:
    """Where a policy reads back unlike its statement, only its comment vouches for it.

    Lose or change that comment and the applier has to build the policy again
    to know what the statement becomes. The policy ends up exactly as it was,
    so no table is reported altered, and the pass after that touches nothing.
    """
    from app.db.provisioning import (
        TEMPLATE_ROW_SECURITY_MARKER,
        apply_template_sql,
    )

    schema = f"practice_test_apply_note_{uuid.uuid4().hex[:8]}"
    body, _, row_security = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    apply_template_sql(engine, schema, body)
    apply_template_sql(engine, schema, row_security)
    try:
        _apply(engine, schema, desired)
        with engine.connect() as conn:
            table, policy = conn.execute(
                text(
                    "SELECT c.relname, p.polname FROM pg_policy p "
                    "JOIN pg_class c ON c.oid = p.polrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s "
                    "AND obj_description(p.oid, 'pg_policy') LIKE :prefix ORDER BY 1, 2 LIMIT 1"
                ),
                {"s": schema, "prefix": APPLIED_PREFIX + "%"},
            ).one()
        settled = _policies(engine, schema)
        oids = _policy_oids(engine, schema)
        _ddl(engine, f'COMMENT ON POLICY "{policy}" ON "{schema}"."{table}" IS \'not ours\'')

        assert _apply(engine, schema, desired).altered == 0
        assert _policy_oids(engine, schema)[table, policy] != oids[table, policy], (
            "the policy was not rebuilt, so nothing checked what its statement becomes"
        )
        assert _policies(engine, schema) == settled

        rebuilt = _policy_oids(engine, schema)
        assert _apply(engine, schema, desired).altered == 0
        assert _policy_oids(engine, schema) == rebuilt
    finally:
        _drop_schema(engine, schema)


def test_tables_it_cannot_act_on_are_named_in_the_log_and_a_clean_pass_warns_of_nothing(
    engine: Engine,
    practice: str,
    desired: dict[str, DesiredTable],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One warning names the tables no SQL creates, another the ones the schema lacks."""
    with caplog.at_level(logging.WARNING, logger="app.db.rls_apply"):
        _apply(engine, practice, desired)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    _ddl(engine, f'CREATE TABLE "{practice}".added_later (id uuid PRIMARY KEY)')
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.db.rls_apply"):
        _apply(engine, practice, {**desired, "not_migrated_yet": DesiredTable()})

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 2
    missing = next(w for w in warnings if "not_migrated_yet" in w)
    unknown = next(w for w in warnings if "added_later" in w)
    assert "added_later" not in missing
    assert "not_migrated_yet" not in unknown
    assert practice in missing
    assert practice in unknown
