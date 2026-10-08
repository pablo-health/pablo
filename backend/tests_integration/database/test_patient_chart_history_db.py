# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart history, against a real provisioned tenant.

The tenant is built from the template, so this is also the check that the
template carries both tables.

* Isolation: ``patient_chart_history`` and its revisions take the
  ``has_patient_access`` row policy from the reconcile pass, with no
  hand-written policy. A clinician with a grant reads and writes; one without
  reads nothing and cannot insert into either table. Every invisibility check
  follows a control that the row is visible to the grantee.
* The trail: an edit and a removal each keep the value they replaced, with
  who wrote it and the note it came from, and the source note's date is read
  back with the field.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.chart_history.service import ChartHistoryService
from app.db import PLATFORM_SCHEMA
from app.db.provisioning import create_practice_schema, ensure_schemas
from app.models import Patient
from app.notes.chart_context import chart_context_for
from app.repositories.postgres.chart_history import PostgresChartHistoryRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_CLINICIAN_A = "0c2f6b3e-8a41-5d7e-9b13-4e6a2c8d1f01"
_CLINICIAN_B = "7d9e1a5c-3f62-5b8a-a4c7-2b1e9d6f3a02"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[str]:
    schema = f"practice_test_history_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@contextmanager
def _session(engine: Engine, schema: str, user_id: str) -> Iterator[Session]:
    """A session on one held connection, so a commit keeps its search_path and user."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
        conn.commit()
        with Session(bind=conn) as session:
            yield session


def _new_patient(engine: Engine, schema: str) -> Patient:
    """A chart created through the repository, granted to clinician A."""
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    with _session(engine, schema, _CLINICIAN_A) as session:
        PostgresPatientRepository(session).create(patient, _CLINICIAN_A)
        session.commit()
    return patient


def _new_note(engine: Engine, schema: str, patient_id: str) -> str:
    note_id = str(uuid.uuid4())
    with _session(engine, schema, _CLINICIAN_A) as session:
        session.execute(
            text(
                "INSERT INTO notes (id, patient_id, note_type, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'soap', "
                "'2026-07-14T15:00:00+00', now())"
            ),
            {"id": note_id, "pid": patient_id},
        )
        session.commit()
    return note_id


def test_grantee_reads_and_writes_history_and_an_outsider_sees_nothing(
    engine: Engine, tenant: str
) -> None:
    patient = _new_patient(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = ChartHistoryService(PostgresChartHistoryRepository(session))
        service.set(patient.id, "trauma_history", _CLINICIAN_A, "Denies a history of trauma.")
        service.set(patient.id, "trauma_history", _CLINICIAN_A, "Car accident at 19; no PTSD.")
        session.commit()
        assert service.entries(patient.id)["trauma_history"].text == (
            "Car accident at 19; no PTSD."
        ), "control"
        assert len(service.revisions(patient.id)) == 1, "control"

    with _session(engine, tenant, _CLINICIAN_B) as session:
        repo = PostgresChartHistoryRepository(session)
        assert repo.entries(patient.id) == []
        assert repo.revisions(patient.id) == []
        for table, columns, values in (
            (
                "patient_chart_history",
                "id, patient_id, field_key, text, updated_at",
                "gen_random_uuid(), CAST(:pid AS uuid), 'supports', 'X', now()",
            ),
            (
                "patient_chart_history_revisions",
                "id, patient_id, field_key, text, written_at, replaced_by, replaced_at",
                "gen_random_uuid(), CAST(:pid AS uuid), 'supports', 'X', now(), "
                f"CAST('{_CLINICIAN_B}' AS uuid), now()",
            ),
        ):
            with pytest.raises(ProgrammingError, match="row-level security"):
                session.execute(
                    text(f"INSERT INTO {table} ({columns}) VALUES ({values})"),  # noqa: S608
                    {"pid": patient.id},
                )
            session.rollback()


def test_every_write_keeps_the_value_it_replaced_and_the_chart_reads_it(
    engine: Engine, tenant: str
) -> None:
    patient = _new_patient(engine, tenant)
    note_id = _new_note(engine, tenant, patient.id)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = ChartHistoryService(PostgresChartHistoryRepository(session))
        service.set(patient.id, "living_situation", _CLINICIAN_A, "Lives with spouse.", note_id)
        service.set(patient.id, "medical_history", _CLINICIAN_A, "Hypertension.")
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = ChartHistoryService(PostgresChartHistoryRepository(session))
        entry = service.entries(patient.id)["living_situation"]
        assert entry.source_note_id == note_id
        assert entry.source_note_date == datetime(2026, 7, 14, 15, tzinfo=UTC)
        service.set(patient.id, "living_situation", _CLINICIAN_A, "Separated; lives alone.")
        service.remove(patient.id, "medical_history", _CLINICIAN_A)
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = ChartHistoryService(PostgresChartHistoryRepository(session))
        entries = service.entries(patient.id)
        revisions = service.revisions(patient.id)
        chart = chart_context_for(patient, [], history=entries.values())

    assert entries["living_situation"].text == "Separated; lives alone."
    assert entries["medical_history"].text is None
    assert {(r.field_key, r.text, r.source_note_id) for r in revisions} == {
        ("living_situation", "Lives with spouse.", note_id),
        ("medical_history", "Hypertension.", None),
    }
    assert all(r.written_by == _CLINICIAN_A and r.replaced_by == _CLINICIAN_A for r in revisions)
    assert [(h.key, h.text) for h in chart.history] == [
        ("living_situation", "Separated; lives alone.")
    ]
