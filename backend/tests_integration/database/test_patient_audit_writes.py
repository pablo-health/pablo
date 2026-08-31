# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A patient acting on their own record can be audited — proven against Postgres.

``audit_logs`` used to take the ordinary direct-ownership policy: a row is
writable when its ``user_id`` matches ``app.current_user_id``. A patient
principal never arms that GUC, so under the NOBYPASSRLS role every audit
row a patient's own action produced was refused at INSERT — the action
happened and nothing recorded it, which is the one failure a
§ 164.312(b) record cannot have.

These run under the same non-superuser posture as production, so the
policy has to actually hold rather than being bypassed by the role. The
first test is the red one: it fails against the policy this change
replaces.

``DATABASE_URL`` comes from the session-scoped bootstrap fixture in
``tests_integration/conftest.py``, which spins up a disposable Postgres
when no URL is exported.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_RLS_DENIED = (IntegrityError, InternalError, ProgrammingError)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_patient_audit_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _insert_audit_row(
    conn: object,
    *,
    user_id: str,
    actor_type: str,
    patient_id: str | None = None,
) -> str:
    row_id = str(uuid.uuid4())
    conn.execute(  # type: ignore[attr-defined]
        text(
            'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id, '
            "actor_type, action, resource_type, resource_id, patient_id) "
            "VALUES (CAST(:id AS uuid), now(), now() + interval '6 years', "
            ":user_id, :actor_type, 'read', 'patient', 'res-1', "
            "CAST(:patient_id AS uuid))"
        ),
        {
            "id": row_id,
            "user_id": user_id,
            "actor_type": actor_type,
            "patient_id": patient_id,
        },
    )
    return row_id


def _as_patient(conn: object, schema: str, patient_id: str) -> None:
    """Arm a patient principal, transaction-locally.

    ``set_config(..., true)`` is what ``arm_current_patient_id`` does in
    production, and it is what keeps one test's identity off the next
    test's pooled connection — a session-scoped GUC here would let a
    later reader inherit an earlier writer's clinician id and quietly
    see rows the policy never granted it.
    """
    conn.execute(text(f"SET search_path = {schema}, platform, public"))  # type: ignore[attr-defined]
    conn.execute(  # type: ignore[attr-defined]
        text("SELECT set_config('app.current_patient_id', :p, true)"), {"p": patient_id}
    )


def _as_clinician(conn: object, schema: str, user_id: str) -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))  # type: ignore[attr-defined]
    conn.execute(  # type: ignore[attr-defined]
        text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id}
    )


def _create_patient(engine: Engine, schema: str, patient_id: str, clinician_id: str) -> None:
    """A real chart row, because the grant table has a foreign key to it."""
    with engine.begin() as conn:
        _as_clinician(conn, schema, clinician_id)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), 'Test', 'Patient', 'test', 'patient', "
                "'active', 0, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": patient_id},
        )


def _grant_access(engine: Engine, schema: str, patient_id: str, clinician_id: str) -> None:
    """Give a clinician a treating relationship with a patient."""
    _create_patient(engine, schema, patient_id, clinician_id)
    with engine.begin() as conn:
        # patient_clinicians is itself row-owned by the clinician the grant
        # is for, so writing one needs that clinician armed.
        _as_clinician(conn, schema, clinician_id)
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by) "
                "VALUES (CAST(:p AS uuid), CAST(:u AS uuid), 'primary', CAST(:u AS uuid)) "
                "ON CONFLICT DO NOTHING"
            ),
            {"p": patient_id, "u": clinician_id},
        )


class TestPatientPrincipalCanBeAudited:
    """The red-first case: a patient's own action gets a row."""

    def test_a_patient_actor_insert_succeeds_under_the_patient_guc(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Fails against the policy this change replaces — that policy only
        ever compared against the clinician GUC."""
        patient_id = str(uuid.uuid4())

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, patient_id)
            row_id = _insert_audit_row(
                conn, user_id=patient_id, actor_type="patient", patient_id=patient_id
            )

        assert row_id

    def test_a_patient_cannot_write_a_row_in_another_patients_name(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        armed = str(uuid.uuid4())
        someone_else = str(uuid.uuid4())

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, armed)
            with pytest.raises(_RLS_DENIED):
                _insert_audit_row(
                    conn, user_id=someone_else, actor_type="patient", patient_id=someone_else
                )

    def test_a_clinician_shaped_insert_still_needs_the_clinician_guc(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The patient arm must not become a way around the clinician one."""
        patient_id = str(uuid.uuid4())

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, patient_id)
            with pytest.raises(_RLS_DENIED):
                _insert_audit_row(conn, user_id=str(uuid.uuid4()), actor_type="clinician")

    def test_a_clinician_actor_insert_is_unchanged(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Everything that is not a patient keeps the predicate it had."""
        clinician_id = str(uuid.uuid4())

        with engine.begin() as conn:
            _as_clinician(conn, tenant_schema, clinician_id)
            row_id = _insert_audit_row(conn, user_id=clinician_id, actor_type="clinician")

        assert row_id

    @pytest.mark.parametrize("actor_type", ["anonymous", "system", "platform_staff"])
    def test_the_other_actor_kinds_keep_writing(
        self, engine: Engine, tenant_schema: str, actor_type: str
    ) -> None:
        """A public-booking row and a cron's row are scoped to a user and arm
        the user GUC; splitting the policy must not have cost them that."""
        owner_id = str(uuid.uuid4())

        with engine.begin() as conn:
            _as_clinician(conn, tenant_schema, owner_id)
            row_id = _insert_audit_row(conn, user_id=owner_id, actor_type=actor_type)

        assert row_id


class TestWhoCanReadPatientActorRows:
    """Reads stay clinician-side, and only for the treating clinician."""

    def test_a_treating_clinician_sees_their_patients_rows(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        patient_id = str(uuid.uuid4())
        clinician_id = str(uuid.uuid4())
        _grant_access(engine, tenant_schema, patient_id, clinician_id)

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, patient_id)
            row_id = _insert_audit_row(
                conn, user_id=patient_id, actor_type="patient", patient_id=patient_id
            )

        with engine.begin() as conn:
            _as_clinician(conn, tenant_schema, clinician_id)
            found = conn.execute(
                text("SELECT id FROM audit_logs WHERE id = CAST(:id AS uuid)"), {"id": row_id}
            ).fetchall()

        assert len(found) == 1

    def test_a_stranger_clinician_sees_nothing_of_them(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        patient_id = str(uuid.uuid4())
        treating = str(uuid.uuid4())
        stranger = str(uuid.uuid4())
        _grant_access(engine, tenant_schema, patient_id, treating)

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, patient_id)
            row_id = _insert_audit_row(
                conn, user_id=patient_id, actor_type="patient", patient_id=patient_id
            )

        with engine.begin() as conn:
            _as_clinician(conn, tenant_schema, stranger)
            found = conn.execute(
                text("SELECT id FROM audit_logs WHERE id = CAST(:id AS uuid)"), {"id": row_id}
            ).fetchall()

        assert found == []

    def test_two_patients_are_isolated_from_each_other(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        first = str(uuid.uuid4())
        second = str(uuid.uuid4())
        clinician = str(uuid.uuid4())
        _grant_access(engine, tenant_schema, first, clinician)

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, first)
            first_row = _insert_audit_row(
                conn, user_id=first, actor_type="patient", patient_id=first
            )
        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, second)
            _insert_audit_row(conn, user_id=second, actor_type="patient", patient_id=second)

        # The clinician treats only the first patient.
        with engine.begin() as conn:
            _as_clinician(conn, tenant_schema, clinician)
            visible = {
                str(r[0])
                for r in conn.execute(
                    text("SELECT id FROM audit_logs WHERE actor_type = 'patient'")
                )
            }

        assert first_row in visible
        assert len(visible) == 1

    def test_a_patient_principal_reads_no_audit_rows(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Self-accounting is a surface of its own; being able to write here
        does not open reading."""
        patient_id = str(uuid.uuid4())

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, patient_id)
            _insert_audit_row(conn, user_id=patient_id, actor_type="patient", patient_id=patient_id)

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, patient_id)
            found = conn.execute(text("SELECT id FROM audit_logs")).fetchall()

        assert found == []


class TestAppendOnlyStillHolds:
    """The new rows are as immutable as every other row in this table."""

    def test_a_patient_actor_row_cannot_be_updated_or_deleted(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        patient_id = str(uuid.uuid4())
        clinician_id = str(uuid.uuid4())
        _grant_access(engine, tenant_schema, patient_id, clinician_id)

        with engine.begin() as conn:
            _as_patient(conn, tenant_schema, patient_id)
            row_id = _insert_audit_row(
                conn, user_id=patient_id, actor_type="patient", patient_id=patient_id
            )

        for statement in (
            "UPDATE audit_logs SET action = 'tamper' WHERE id = CAST(:id AS uuid)",
            "DELETE FROM audit_logs WHERE id = CAST(:id AS uuid)",
        ):
            with engine.begin() as conn:
                _as_clinician(conn, tenant_schema, clinician_id)
                with pytest.raises(_RLS_DENIED):
                    conn.execute(text(statement), {"id": row_id})
