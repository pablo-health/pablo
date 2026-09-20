# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient-facing reads, run against real Postgres as a real patient.

``test_patient_principal_rls.py`` proves the ROW half: with patient A's
principal armed, patient B's chart and calendar are unreachable. This file is
the COLUMN half, and the two are not substitutes. Row-level security has no
column granularity — the policy that grants a patient their own row grants
every column of it, so at this layer the clinician's working diagnosis, the
note about what this person can afford, and the visit's billing coding are all
readable. Nothing in the database will refuse them.

What refuses them is the projection in the repository, and a projection is
exactly the kind of change that passes against an in-memory double and fails on
the wire. So both reads run here against a schema built by the real
``create_practice_schema``, as the ``pablo`` role the integration conftest
creates ``NOSUPERUSER NOBYPASSRLS``, over rows whose staff-authored columns are
all populated.

Non-vacuity is enforced rather than hoped for: every "the withheld column is
absent" assertion is preceded by a control proving the row was found and the
shown columns came back with the values that were seeded.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.models.patient_facing import (
    APPOINTMENT_COLUMN_DECISIONS,
    PATIENT_COLUMN_DECISIONS,
    shown_columns,
    withheld_columns,
)
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_CLINICIAN = "1f4b2c07-6a9e-4d31-b8f2-5c0e7a913d46"

# Distinctive values, so an assertion catches the CONTENT reaching a patient
# and not only a column name.
_DIAGNOSIS = "F41.1 generalised anxiety, provisional"
_SLIDING_SCALE_NOTE = "agreed 90 a session while between jobs"
_CLINICIAN_NOTE = "flat affect again, raise at supervision"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_pfp_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def seeded(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """One patient and one appointment, staff columns all filled in.

    Seeded clinician-side, as the app writes them. A fixture that left these
    at their defaults would let both reads pass while returning every one of
    them.
    """
    patient_id = str(uuid.uuid4())
    appointment_id = str(uuid.uuid4())
    start = datetime.now(UTC) + timedelta(days=3)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, email, phone, status, date_of_birth, diagnosis, "
                "session_count, created_at, updated_at, chart_closure_reason, "
                "rate_cents, sliding_scale_note, origin, address_line1, city, "
                "state, postal_code, sex) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', 'ada', 'lovelace', "
                "'ada@example.test', '+15555550100', 'active', DATE '1990-03-14', "
                ":diagnosis, 4, now(), now(), 'Moved out of state', 9000, :note, "
                "'voice', '12 Analytical Engine Way', 'London', 'NY', '10001', 'F')"
            ),
            {"pid": patient_id, "diagnosis": _DIAGNOSIS, "note": _SLIDING_SCALE_NOTE},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO appointments (id, user_id, patient_id, title, start_at, "
                "end_at, duration_minutes, status, session_type, video_link, "
                "video_platform, notes, note_type, service_code, unit_count, "
                "place_of_service, diagnosis_codes, ehr_appointment_url, "
                "google_event_id, ical_uid, is_exception, reminder_24h_sent, "
                "reminder_1h_sent, created_at, updated_at) "
                "VALUES (CAST(:aid AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), "
                "'Session', :start, :end, 50, 'confirmed', 'therapy', "
                "'https://example.test/room', 'zoom', :notes, 'soap', '90834', 1, "
                "'02', CAST('[\"F41.1\"]' AS jsonb), "
                "'https://ehr.example.test/appointments/1', 'gcal-1', 'ical-1', "
                "false, false, false, now(), now())"
            ),
            {
                "aid": appointment_id,
                "u": _CLINICIAN,
                "pid": patient_id,
                "start": start,
                "end": start + timedelta(minutes=50),
                "notes": _CLINICIAN_NOTE,
            },
        )
    return patient_id, appointment_id


def _as_patient(engine: Engine, schema: str, patient_id: str):  # type: ignore[no-untyped-def]
    """An ORM session scoped to *schema* with only the patient GUC armed."""
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    session.execute(text("RESET app.current_user_id"))
    session.execute(
        text("SELECT set_config('app.current_patient_id', :p, false)"),
        {"p": patient_id},
    )
    return session


class TestTheDatabaseWouldHandOverEverything:
    """The premise. Without it the two suites below prove nothing.

    If the policy DID restrict columns, the projections would be belt and
    braces rather than the control, and a regression in them would be
    invisible here.
    """

    def test_a_patient_principal_can_read_its_own_staff_columns(
        self, engine: Engine, tenant_schema: str, seeded: tuple[str, str]
    ) -> None:
        patient_id, _ = seeded
        session = _as_patient(engine, tenant_schema, patient_id)
        try:
            row = session.execute(
                text("SELECT diagnosis, sliding_scale_note, rate_cents FROM patients")
            ).one()
        finally:
            session.close()

        assert row.diagnosis == _DIAGNOSIS
        assert row.sliding_scale_note == _SLIDING_SCALE_NOTE
        assert row.rate_cents == 9000

    def test_a_patient_principal_can_read_its_own_visit_coding(
        self, engine: Engine, tenant_schema: str, seeded: tuple[str, str]
    ) -> None:
        patient_id, _ = seeded
        session = _as_patient(engine, tenant_schema, patient_id)
        try:
            row = session.execute(
                text("SELECT notes, service_code, diagnosis_codes FROM appointments")
            ).one()
        finally:
            session.close()

        assert row.notes == _CLINICIAN_NOTE
        assert row.service_code == "90834"
        assert row.diagnosis_codes == ["F41.1"]


class TestTheChartReadProjects:
    def test_it_returns_the_shown_columns_with_their_values(
        self, engine: Engine, tenant_schema: str, seeded: tuple[str, str]
    ) -> None:
        """The control: the read works and found the right row."""
        from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415

        patient_id, _ = seeded
        session = _as_patient(engine, tenant_schema, patient_id)
        try:
            own = PostgresPatientRepository(session).get_for_patient_principal(patient_id)
        finally:
            session.close()

        assert own is not None
        assert own.first_name == "Ada"
        assert own.last_name == "Lovelace"
        assert own.date_of_birth == "1990-03-14"

    def test_the_withheld_columns_are_not_on_what_comes_back(
        self, engine: Engine, tenant_schema: str, seeded: tuple[str, str]
    ) -> None:
        from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415

        patient_id, _ = seeded
        session = _as_patient(engine, tenant_schema, patient_id)
        try:
            own = PostgresPatientRepository(session).get_for_patient_principal(patient_id)
        finally:
            session.close()

        assert own is not None
        assert set(type(own).model_fields) == shown_columns(PATIENT_COLUMN_DECISIONS)
        for withheld in withheld_columns(PATIENT_COLUMN_DECISIONS):
            assert not hasattr(own, withheld), f"{withheld} came back for a patient principal"

    def test_the_statement_never_names_a_withheld_column(self) -> None:
        """Asserted on the SQL, because that is where "never read" is decided.

        A column the query does not select cannot be serialised by accident,
        logged by accident, or reached for by a later refactor.
        """
        from app.repositories.postgres.patient import PATIENT_FACING_COLUMNS  # noqa: PLC0415

        selected = {column.key for column in PATIENT_FACING_COLUMNS}
        assert selected == shown_columns(PATIENT_COLUMN_DECISIONS)
        assert not selected & set(withheld_columns(PATIENT_COLUMN_DECISIONS))


class TestTheCalendarReadProjects:
    def test_it_returns_the_shown_columns_with_their_values(
        self, engine: Engine, tenant_schema: str, seeded: tuple[str, str]
    ) -> None:
        """The control: the read works and found the right row."""
        from app.repositories.postgres.appointment import (  # noqa: PLC0415
            PostgresAppointmentRepository,
        )

        patient_id, appointment_id = seeded
        session = _as_patient(engine, tenant_schema, patient_id)
        try:
            own = PostgresAppointmentRepository(session).list_for_patient_principal(patient_id)
        finally:
            session.close()

        assert [a.id for a in own] == [appointment_id]
        assert own[0].status == "confirmed"
        assert own[0].session_type == "therapy"
        assert own[0].video_link == "https://example.test/room"

    def test_the_withheld_columns_are_not_on_what_comes_back(
        self, engine: Engine, tenant_schema: str, seeded: tuple[str, str]
    ) -> None:
        from app.repositories.postgres.appointment import (  # noqa: PLC0415
            PostgresAppointmentRepository,
        )

        patient_id, _ = seeded
        session = _as_patient(engine, tenant_schema, patient_id)
        try:
            own = PostgresAppointmentRepository(session).list_for_patient_principal(patient_id)
        finally:
            session.close()

        assert own
        assert set(type(own[0]).model_fields) == shown_columns(APPOINTMENT_COLUMN_DECISIONS)
        for withheld in withheld_columns(APPOINTMENT_COLUMN_DECISIONS):
            assert not hasattr(own[0], withheld), f"{withheld} came back for a patient principal"

    def test_the_clinician_note_is_nowhere_in_what_comes_back(
        self, engine: Engine, tenant_schema: str, seeded: tuple[str, str]
    ) -> None:
        """Belt and braces: the field is gone, and so is its content."""
        from app.repositories.postgres.appointment import (  # noqa: PLC0415
            PostgresAppointmentRepository,
        )

        patient_id, _ = seeded
        session = _as_patient(engine, tenant_schema, patient_id)
        try:
            own = PostgresAppointmentRepository(session).list_for_patient_principal(patient_id)
        finally:
            session.close()

        serialised = [a.model_dump_json() for a in own]
        assert serialised
        for body in serialised:
            assert _CLINICIAN_NOTE not in body
            assert "90834" not in body
            assert "F41.1" not in body

    def test_the_statement_never_names_a_withheld_column(self) -> None:
        from app.repositories.postgres.appointment import (  # noqa: PLC0415
            PATIENT_FACING_COLUMNS,
        )

        selected = {column.key for column in PATIENT_FACING_COLUMNS}
        assert selected == shown_columns(APPOINTMENT_COLUMN_DECISIONS)
        assert not selected & set(withheld_columns(APPOINTMENT_COLUMN_DECISIONS))
