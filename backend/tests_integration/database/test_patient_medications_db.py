# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A medication's frequency and category, against a real provisioned tenant.

The tenant is built from the template, not the chain, so this is also the
check that the template carries the two columns and the category constraint:
a template left behind would fail the first insert here.

* Both fields persist through the repository and reach the chart a draft is
  written against.
* The database refuses a category that is neither ``psychiatric`` nor
  ``other``.
* A clinician without a grant reads nothing, after a control that the
  grantee does.
* A session's transcription vocabulary reads the current and stopped
  medications and the allergy substances, and nothing without a grant.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.db import PLATFORM_SCHEMA
from app.db.provisioning import create_practice_schema, ensure_schemas
from app.medications.schemas import CreateMedicationRequest, UpdateMedicationRequest
from app.medications.service import MedicationService
from app.models import Patient
from app.notes.chart_context import ChartMedication, chart_context_for
from app.repositories.postgres.medication import PostgresMedicationRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.services.transcription_keyterms import session_keyterms
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_CLINICIAN_A = "5f0c7f0e-3c55-5b0e-9a55-0c6a3f4f2d11"
_CLINICIAN_B = "a1d6e7b2-91c4-5f3e-8d2a-7b4c6e9f0a22"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[str]:
    schema = f"practice_test_medications_{uuid.uuid4().hex[:8]}"
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


def test_frequency_and_category_persist_and_reach_the_chart(engine: Engine, tenant: str) -> None:
    patient = _new_patient(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = MedicationService(PostgresMedicationRepository(session))
        created = service.create(
            patient.id,
            _CLINICIAN_A,
            CreateMedicationRequest(
                drug_name="Sertraline",
                dose="50 mg AM / 25 mg PM",
                frequency="twice daily",
                category="psychiatric",
            ),
        )
        service.create(
            patient.id,
            _CLINICIAN_A,
            CreateMedicationRequest(drug_name="Lisinopril", dose="10 mg"),
        )
        service.update(
            str(created["id"]),
            _CLINICIAN_A,
            UpdateMedicationRequest(frequency="every morning"),
        )
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        rows = PostgresMedicationRepository(session).list_by_patient(patient.id, _CLINICIAN_A)
        chart = chart_context_for(patient, [], rows)

    assert set(chart.medications) == {
        ChartMedication("Sertraline", "50 mg AM / 25 mg PM", "every morning", "psychiatric"),
        ChartMedication("Lisinopril", "10 mg"),
    }

    with _session(engine, tenant, _CLINICIAN_B) as session:
        assert PostgresMedicationRepository(session).list_by_patient(patient.id, _CLINICIAN_B) == []


def test_the_database_refuses_an_unknown_category(engine: Engine, tenant: str) -> None:
    patient = _new_patient(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session, pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO patient_medications "
                "(id, patient_id, drug_name, dose, status, category, created_by, "
                "created_at, updated_at) "
                "VALUES (:id, :pid, 'Melatonin', '3 mg', 'active', 'supplement', :uid, "
                "now(), now())"
            ),
            {"id": str(uuid.uuid4()), "pid": patient.id, "uid": _CLINICIAN_A},
        )


def test_the_session_vocabulary_reads_current_and_stopped_medications_and_allergies(
    engine: Engine, tenant: str
) -> None:
    """The transcriber's vocabulary comes from this chart alone, read as the clinician
    who recorded the session: a stopped medication still primes its name, an allergy
    substance does too, and a clinician without a grant gets nothing."""
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Sam",
        last_name="Sample",
        created_at=now,
        updated_at=now,
        allergy_status="recorded",
        allergies=[{"substance": "penicillin", "reaction": "hives"}],
    )
    with _session(engine, tenant, _CLINICIAN_A) as session:
        PostgresPatientRepository(session).create(patient, _CLINICIAN_A)
        service = MedicationService(PostgresMedicationRepository(session))
        service.create(
            patient.id, _CLINICIAN_A, CreateMedicationRequest(drug_name="Sertraline", dose="50 mg")
        )
        stopped = service.create(
            patient.id, _CLINICIAN_A, CreateMedicationRequest(drug_name="trazodone", dose="50 mg")
        )
        service.update(
            str(stopped["id"]), _CLINICIAN_A, UpdateMedicationRequest(status="discontinued")
        )
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        terms = session_keyterms(session, patient.id, _CLINICIAN_A)
    assert terms == ["sertraline", "Zoloft", "trazodone", "Desyrel", "penicillin"]

    with _session(engine, tenant, _CLINICIAN_B) as session:
        assert session_keyterms(session, patient.id, _CLINICIAN_B) == []
