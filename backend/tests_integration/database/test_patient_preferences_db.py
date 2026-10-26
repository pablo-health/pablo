# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient preferences against a real provisioned tenant.

* Isolation: ``patient_preferences`` takes the ``has_patient_access`` row
  policy from the reconcile pass, with no hand-written policy. A clinician with
  a grant reads and writes; one without reads nothing, through the one-client
  and the many-client reads, and cannot insert. Every invisibility check
  follows a control that the row is visible to the grantee, so an empty table
  cannot pass it.
* Retiring keeps the row, and deleting the client takes its rows with it.
* The database refuses a row with neither a value nor words.

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
from app.models import Patient
from app.preferences.kinds import AVAILABILITY, HARD_LIMIT, PRACTICAL_NOTE
from app.preferences.schemas import AddPreferenceRequest
from app.preferences.service import PreferenceService
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.patient_preference import PostgresPatientPreferenceRepository
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_CLINICIAN_A = "5f0e2a51-7c3b-5d8e-9a14-3b6c2d1e0f9a"
_CLINICIAN_B = "c2d4e6f8-1a3b-5c7d-9e0f-2a4b6c8d0e1f"
_AFTERNOONS = {"days": ["mon", "thu"], "times": ["afternoon"], "modality": "either"}


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[str]:
    schema = f"practice_test_preferences_{uuid.uuid4().hex[:8]}"
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


def _new_patient(engine: Engine, schema: str) -> str:
    """A chart created through the repository, granted to clinician A."""
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Maya",
        last_name="Sample",
        created_at=now,
        updated_at=now,
    )
    with _session(engine, schema, _CLINICIAN_A) as session:
        PostgresPatientRepository(session).create(patient, _CLINICIAN_A)
        session.commit()
    return patient.id


def test_grantee_reads_and_writes_and_an_outsider_sees_nothing(engine: Engine, tenant: str) -> None:
    patient_id = _new_patient(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = PreferenceService(PostgresPatientPreferenceRepository(session))
        service.add(
            patient_id,
            _CLINICIAN_A,
            AddPreferenceRequest(kind=AVAILABILITY, value=_AFTERNOONS),
        )
        session.commit()
        assert [p.kind for p in service.preferences(patient_id)] == [AVAILABILITY], "control"
        assert len(service.active_for_patients([patient_id], [AVAILABILITY])) == 1, "control"

    with _session(engine, tenant, _CLINICIAN_B) as session:
        service = PreferenceService(PostgresPatientPreferenceRepository(session))
        assert service.preferences(patient_id, include_retired=True) == []
        assert service.active_for_patients([patient_id], [AVAILABILITY]) == []
        with pytest.raises(ProgrammingError, match="row-level security"):
            session.execute(
                text(
                    "INSERT INTO patient_preferences "
                    "(id, patient_id, domain, kind, text, source, stated_at, "
                    "created_at, updated_at) "
                    "VALUES (gen_random_uuid(), CAST(:pid AS uuid), 'practical', "
                    "'practical.note', 'x', 'clinician', now(), now(), now())"
                ),
                {"pid": patient_id},
            )
        session.rollback()


def test_retiring_keeps_the_row_and_the_client_record_takes_them_with_it(
    engine: Engine, tenant: str
) -> None:
    patient_id = _new_patient(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = PreferenceService(PostgresPatientPreferenceRepository(session))
        limit = service.add(
            patient_id,
            _CLINICIAN_A,
            AddPreferenceRequest(kind=HARD_LIMIT, value=_AFTERNOONS, text="Work"),
        )
        service.add(
            patient_id,
            _CLINICIAN_A,
            AddPreferenceRequest(kind=PRACTICAL_NOTE, text="Parking at the back"),
        )
        service.retire(patient_id, limit.id, _CLINICIAN_A)
        session.commit()
        assert [p.kind for p in service.preferences(patient_id)] == [PRACTICAL_NOTE]
        kept = service.preferences(patient_id, include_retired=True)
        assert {p.kind for p in kept} == {HARD_LIMIT, PRACTICAL_NOTE}
        stored = next(p for p in kept if p.kind == HARD_LIMIT)
        assert stored.value == _AFTERNOONS
        assert stored.strength == "hard"
        assert stored.retired_by == _CLINICIAN_A

        session.execute(text("DELETE FROM patients WHERE id = CAST(:p AS uuid)"), {"p": patient_id})
        session.commit()
        count = session.execute(
            text("SELECT count(*) FROM patient_preferences WHERE patient_id = CAST(:p AS uuid)"),
            {"p": patient_id},
        ).scalar_one()
        assert count == 0


def test_a_row_with_neither_value_nor_words_is_refused(engine: Engine, tenant: str) -> None:
    patient_id = _new_patient(engine, tenant)
    with _session(engine, tenant, _CLINICIAN_A) as session:
        with pytest.raises(IntegrityError, match="ck_patient_preferences_has_content"):
            session.execute(
                text(
                    "INSERT INTO patient_preferences "
                    "(id, patient_id, domain, kind, source, stated_at, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), CAST(:pid AS uuid), 'practical', "
                    "'practical.note', 'clinician', now(), now(), now())"
                ),
                {"pid": patient_id},
            )
        session.rollback()
