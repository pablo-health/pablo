# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Test preflight for single-practice conversion."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import (
    DEFAULT_PRACTICE_ID,
    DEFAULT_PRACTICE_SCHEMA,
    PLATFORM_SCHEMA,
    enable_rls_on_schema,
)
from app.db.provisioning import create_practice_schema
from app.db.single_practice_migration import (
    PreflightError,
    Shape,
    _classify,
    migrate,
    preflight,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


from .helpers import _counts, _seed_patient

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


#: What ``enable_rls_on_schema`` names the policy it creates, per shape. The
#: pre-flight's predicates are only correct while these still line up; a new
#: branch there without one here fails this test rather than silently making the
#: pre-flight under-report.
_POLICY_NAME_BY_SHAPE = {
    Shape.USER_ID: "rls_user_isolation",
    Shape.AUDIT_LOGS: "rls_audit_actor_access",
    Shape.UPLOADER: "rls_user_isolation",
    Shape.PATIENT_ACCESS_BY_ID: "rls_patient_access",
    Shape.PATIENT_ACCESS_BY_PATIENT_ID: "rls_patient_access",
    Shape.PATIENT_DOCUMENTS: "rls_patient_doc_access",
    Shape.NOTES: "rls_note_access",
    Shape.NOTE_CHILD: "rls_note_child_access",
    Shape.CHAT_MESSAGES: "rls_chat_message_access",
    Shape.PRACTICE_ANSWERS: "rls_practice_answers",
}


def test_classification_matches_the_policies_actually_created(engine: Engine) -> None:
    """Pin the pre-flight's model of RLS against the real thing.

    ``single_practice_migration`` mirrors ``enable_rls_on_schema``'s branch
    chain, and a copy can drift from its original. A drifted copy is worse than
    no copy: it reports zero invisible rows for a shape it no longer
    understands. So apply the REAL policies to a real schema, read
    ``pg_policies`` back, and assert the classifier agrees.
    """

    schema = f"practice_classify_{uuid.uuid4().hex[:8]}"
    try:
        create_practice_schema(engine, schema)
        with Session(engine) as session:
            # The policies call ``has_patient_access`` unqualified and it is
            # schema-local, so it has to be resolvable from the search_path.
            session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            enable_rls_on_schema(session, schema)
            session.commit()

        with engine.connect() as conn:
            actual: dict[str, set[str]] = {}
            for table, policy in conn.execute(
                text("SELECT tablename, policyname FROM pg_policies WHERE schemaname = :s"),
                {"s": schema},
            ):
                actual.setdefault(table, set()).add(policy)

            columns: dict[str, set[str]] = {}
            for table, column in conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name != 'alembic_version'"
                ),
                {"s": schema},
            ):
                columns.setdefault(table, set()).add(column)

        assert actual, f"no policies found on {schema} — the fixture is wrong, not the code"

        for table, policies in actual.items():
            shape = _classify(table, columns[table])
            expected = _POLICY_NAME_BY_SHAPE[shape]
            assert expected in policies, (
                f"pre-flight classifies {table} as {shape.value}, which implies policy "
                f"'{expected}', but enable_rls_on_schema created {sorted(policies)}. "
                f"The pre-flight's predicate for this table is describing a policy that "
                f"no longer exists."
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_preflight_is_clean_for_properly_granted_data(old_shape: Engine) -> None:
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        for _ in range(3):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id)

    counts = preflight(engine, DEFAULT_PRACTICE_SCHEMA)

    patients = _counts(counts, "patients")
    assert patients.total == 3
    assert patients.orphaned == 0
    assert all(c.orphaned == 0 for c in counts), [
        (c.table, c.orphaned) for c in counts if c.orphaned
    ]


def test_preflight_counts_a_patient_with_no_grant(old_shape: Engine) -> None:
    """The failure mode the whole pre-flight exists for.

    A patients row with no live ``patient_clinicians`` grant does not error under
    the policy — it disappears.
    """
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=True)
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=False)

    patients = _counts(preflight(engine, DEFAULT_PRACTICE_SCHEMA), "patients")

    assert patients.total == 2
    assert patients.orphaned == 1
    assert patients.shape is Shape.PATIENT_ACCESS_BY_ID


def test_an_expired_grant_counts_as_no_grant(old_shape: Engine) -> None:
    """``has_patient_access`` checks ``expires_at``; so must the pre-flight.

    An expired grant is the subtle case — the row LOOKS granted, and a
    liveness-blind check would report the migration safe while the chart
    vanishes.
    """
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        patient_id = _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=True)
        conn.execute(
            text(
                f"UPDATE {DEFAULT_PRACTICE_SCHEMA}.patient_clinicians "  # noqa: S608 — schema/table are validated identifiers, never user input
                f"SET expires_at = now() - interval '1 day' WHERE patient_id = :p"
            ),
            {"p": patient_id},
        )

    assert _counts(preflight(engine, DEFAULT_PRACTICE_SCHEMA), "patients").orphaned == 1


def test_migrate_refuses_when_the_preflight_is_dirty(old_shape: Engine) -> None:
    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()), with_grant=False)

    with pytest.raises(PreflightError) as exc:
        migrate(engine)

    assert "patients" in str(exc.value)
    # And nothing moved.
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": DEFAULT_PRACTICE_SCHEMA},
        ).first()
        row = conn.execute(
            text(f"SELECT schema_name FROM {PLATFORM_SCHEMA}.practices WHERE id = :id"),  # noqa: S608
            {"id": DEFAULT_PRACTICE_ID},
        ).one()
    assert row[0] == DEFAULT_PRACTICE_SCHEMA
