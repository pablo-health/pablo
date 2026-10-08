# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note chart proposals, against a real provisioned tenant.

The tenant is built from the template, so this is also the check that the
template carries the table.

* Isolation: ``note_chart_proposals`` takes the ``has_patient_access`` row
  policy from the reconcile pass, with no hand-written policy. A clinician
  with a grant stores, reads and decides proposals; one without reads
  nothing and cannot insert. The invisibility checks follow a control that
  the rows are visible to the grantee.
* Accepting writes the chart's history with the note as its source, and the
  evidence round-trips as stored.

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
from app.chart_proposals.families import ChartWriters
from app.chart_proposals.models import DraftedProposal, Evidence, ProposalRun
from app.chart_proposals.service import ChartProposalService, Choice
from app.db import PLATFORM_SCHEMA
from app.db.provisioning import create_practice_schema, ensure_schemas
from app.models import Note, Patient
from app.notes.chart_context import ChartContext
from app.repositories.postgres.chart_history import PostgresChartHistoryRepository
from app.repositories.postgres.chart_proposals import PostgresChartProposalRepository
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

_CLINICIAN_A = "1d3a7c5e-9b42-5e8f-a024-5f7b3d9e2a11"
_CLINICIAN_B = "8e0f2b6d-4a73-5c9b-b5d8-3c2f0e7a4b12"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[str]:
    schema = f"practice_test_proposals_{uuid.uuid4().hex[:8]}"
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


def _patient_and_note(engine: Engine, schema: str) -> tuple[Patient, Note]:
    """A chart granted to clinician A, with one follow-up note."""
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    note = Note(
        id=str(uuid.uuid4()),
        patient_id=patient.id,
        note_type="custom.psychiatric_follow_up",
        created_at=now,
        updated_at=now,
    )
    with _session(engine, schema, _CLINICIAN_A) as session:
        PostgresPatientRepository(session).create(patient, _CLINICIAN_A)
        session.execute(
            text(
                "INSERT INTO notes (id, patient_id, note_type, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), :type, now(), now())"
            ),
            {"id": note.id, "pid": patient.id, "type": note.note_type},
        )
        session.commit()
    return patient, note


_PROPOSAL = DraftedProposal(
    field_key="work_school",
    proposed_text="Worked at the library until March; laid off, no longer working there.",
    what_changed="Laid off",
    evidence=(Evidence(3, "[00:20] Client: They let me go in March."),),
)


def test_grantee_stores_and_decides_and_an_outsider_sees_nothing(
    engine: Engine, tenant: str
) -> None:
    patient, note = _patient_and_note(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        history = ChartHistoryService(PostgresChartHistoryRepository(session))
        history.set(patient.id, "work_school", _CLINICIAN_A, "Works at the library.")
        service = ChartProposalService(
            PostgresChartProposalRepository(session),
            ChartWriters(history=history, patients=PostgresPatientRepository(session)),
        )
        service.refresh(note, ChartContext(), {}, [_PROPOSAL])
        session.commit()
        (stored,) = service.proposals(note.id)
        assert stored.evidence == _PROPOSAL.evidence, "control"

    with _session(engine, tenant, _CLINICIAN_B) as session:
        assert PostgresChartProposalRepository(session).list_for_note(note.id) == []
        with pytest.raises(ProgrammingError, match="row-level security"):
            session.execute(
                text(
                    "INSERT INTO note_chart_proposals (id, note_id, patient_id, field_key, "
                    "proposed_text, what_changed, origin, created_at) VALUES (gen_random_uuid(), "
                    "CAST(:nid AS uuid), CAST(:pid AS uuid), 'supports', 'X', 'X', "
                    "'transcript', now())"
                ),
                {"nid": note.id, "pid": patient.id},
            )
        session.rollback()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        history = ChartHistoryService(PostgresChartHistoryRepository(session))
        patients = PostgresPatientRepository(session)
        service = ChartProposalService(
            PostgresChartProposalRepository(session),
            ChartWriters(history=history, patients=patients),
        )
        current = patients.get(patient.id, _CLINICIAN_A)
        assert current is not None
        service.decide(note, current, stored.id, Choice("accept"), _CLINICIAN_A)
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        history = ChartHistoryService(PostgresChartHistoryRepository(session))
        entry = history.entries(patient.id)["work_school"]
        (decided,) = PostgresChartProposalRepository(session).list_for_note(note.id)

    assert (entry.text, entry.source_note_id) == (_PROPOSAL.proposed_text, note.id)
    assert (decided.decision, decided.decided_by) == ("accepted", _CLINICIAN_A)


def test_a_run_record_is_kept_per_note_and_replaced(engine: Engine, tenant: str) -> None:
    patient, note = _patient_and_note(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        repo = PostgresChartProposalRepository(session)
        now = datetime.now(UTC)
        repo.record_run(ProposalRun(note.id, patient.id, "failed", now, "TimeoutError"))
        repo.record_run(ProposalRun(note.id, patient.id, "ok", now))
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        run = PostgresChartProposalRepository(session).run(note.id)
    with _session(engine, tenant, _CLINICIAN_B) as session:
        outsider = PostgresChartProposalRepository(session).run(note.id)

    assert run is not None
    assert (run.status, run.error_class) == ("ok", None)
    assert outsider is None
