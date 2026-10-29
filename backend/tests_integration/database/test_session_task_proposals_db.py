# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Accepting something the clinician said they would do, against a real tenant.

What only Postgres can show:

* a task proposal's due date round-trips through ``note_chart_proposals.change``
  next to the medication changes that share the column;
* accepting files one task of the clinician's own, tied to the client, filed
  from the session, with the date they chose; a second accept is refused and
  a second filing for the same proposal finds the first (``ux_tasks_source_ref``);
* a redraft's chart refresh leaves the session's tasks alone, and a redraft's
  task refresh does not offer again one already accepted.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.families import SESSION_TASK, ChartWriters
from app.chart_proposals.models import Commitment, DraftedProposal, Evidence
from app.chart_proposals.service import ChartProposalService, Choice, ProposalDecidedError
from app.db import PLATFORM_SCHEMA
from app.db.provisioning import create_practice_schema, ensure_schemas
from app.models import Note, Patient
from app.notes.chart_context import ChartContext
from app.repositories.postgres.chart_history import PostgresChartHistoryRepository
from app.repositories.postgres.chart_proposals import PostgresChartProposalRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.task import PostgresTaskRepository, TaskFilter
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_CLINICIAN = "3a5c7e9b-1d2f-5a4b-8c6d-0e1f2a3b4c05"
_FRIDAY = date(2026, 10, 23)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[str]:
    schema = f"practice_test_session_tasks_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@contextmanager
def _session(engine: Engine, schema: str) -> Iterator[Session]:
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
        conn.commit()
        with Session(bind=conn) as session:
            yield session


def _client_and_note(session: Session) -> tuple[Patient, Note]:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Omar", last_name="Haddad", created_at=now, updated_at=now
    )
    PostgresPatientRepository(session).create(patient, _CLINICIAN)
    note = Note(
        id=str(uuid.uuid4()),
        patient_id=patient.id,
        note_type="soap",
        created_at=now,
        updated_at=now,
    )
    session.execute(
        text(
            "INSERT INTO notes (id, patient_id, note_type, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'soap', now(), now())"
        ),
        {"id": note.id, "pid": patient.id},
    )
    return patient, note


def _referral(line: int = 4) -> DraftedProposal:
    return DraftedProposal(
        field_key=SESSION_TASK,
        item_key=f"S{line}",
        proposed_text="Send a referral to a psychiatrist",
        what_changed="by Friday",
        evidence=(Evidence(line, "[12:40] Therapist: I'll send that referral by Friday."),),
        commitment=Commitment(due_date=_FRIDAY, due_said="by Friday"),
    )


def _writers(session: Session) -> ChartWriters:
    return ChartWriters(
        history=ChartHistoryService(PostgresChartHistoryRepository(session)),
        patients=PostgresPatientRepository(session),
        tasks=PostgresTaskRepository(session),
    )


def test_accepting_files_one_task_for_the_client_from_the_session(
    engine: Engine, tenant: str
) -> None:
    with _session(engine, tenant) as session:
        patient, note = _client_and_note(session)
        proposals = PostgresChartProposalRepository(session)
        ChartProposalService(proposals).refresh_session_tasks(note, [_referral()])

        [stored] = proposals.list_for_note(note.id)
        assert stored.commitment == Commitment(due_date=_FRIDAY, due_said="by Friday")
        assert stored.change is None

        service = ChartProposalService(proposals, _writers(session))
        monday = date(2026, 10, 26)
        decided = service.decide(
            note,
            patient,
            stored.id,
            Choice("edit", "Send the psychiatry referral", due_date=monday),
            _CLINICIAN,
        )
        assert decided.decision == "edited"

        tasks = PostgresTaskRepository(session)
        [task] = tasks.list_by_user(_CLINICIAN, TaskFilter(patient_id=patient.id))
        assert task.label == "Send the psychiatry referral"
        assert task.due_date == monday
        assert task.source == "session"
        assert task.category == "practice"
        source_ref = session.execute(
            text("SELECT source_ref FROM tasks WHERE id = CAST(:id AS uuid)"), {"id": task.id}
        ).scalar_one()
        assert source_ref == stored.id

        with pytest.raises(ProposalDecidedError):
            service.decide(note, patient, stored.id, Choice("accept"), _CLINICIAN)
        # Filed again for the same proposal (an accept retried after a lost response):
        # the first task comes back and no second one is written.
        again = tasks.file_once(replace(task, id=str(uuid.uuid4())), source_ref=stored.id)
        assert again.id == task.id
        assert len(tasks.list_by_user(_CLINICIAN, TaskFilter(patient_id=patient.id))) == 1
        session.rollback()


def test_a_redraft_keeps_the_sessions_tasks_apart_from_the_chart(
    engine: Engine, tenant: str
) -> None:
    with _session(engine, tenant) as session:
        patient, note = _client_and_note(session)
        proposals = PostgresChartProposalRepository(session)
        service = ChartProposalService(proposals, _writers(session))
        service.refresh_session_tasks(note, [_referral()])
        [stored] = proposals.list_for_note(note.id)
        service.decide(note, patient, stored.id, Choice("accept"), _CLINICIAN)

        # The chart call's answer to a redraft replaces the chart's pending
        # proposals; it never touches the session's tasks.
        service.refresh(note, ChartContext(allergy_status="nkda"), {}, [])
        assert [p.id for p in proposals.list_for_note(note.id)] == [stored.id]

        # The commitment call's answer to the redraft, reworded, on the same line:
        # accepted once, not offered again.
        reworded = DraftedProposal(
            field_key=SESSION_TASK,
            item_key="S4",
            proposed_text="Refer to psychiatry",
            what_changed="",
            evidence=_referral().evidence,
            commitment=Commitment(),
        )
        service.refresh_session_tasks(note, [reworded, _referral(line=9)])
        listed = proposals.list_for_note(note.id)
        assert sorted(p.item_key for p in listed) == ["S4", "S9"]
        assert [p.decision for p in listed if p.item_key == "S4"] == ["accepted"]
        session.rollback()
