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
* The dashboard's waiting counts see exactly what the grantee could open:
  pending proposals on live notes, none for an outsider.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.families import ChartWriters
from app.chart_proposals.models import (
    Considered,
    DraftedProposal,
    Evidence,
    MedicationChange,
    ProposalRun,
)
from app.chart_proposals.service import ChartProposalService, Choice
from app.db import PLATFORM_SCHEMA
from app.db.provisioning import create_practice_schema, ensure_schemas
from app.medications.service import MedicationService
from app.models import Note, Patient
from app.notes.chart_context import ChartContext
from app.repositories.postgres.chart_history import PostgresChartHistoryRepository
from app.repositories.postgres.chart_proposals import PostgresChartProposalRepository
from app.repositories.postgres.medication import PostgresMedicationRepository
from app.repositories.postgres.patient import PostgresPatientRepository
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
    assert run.considered == ()
    assert outsider is None


def test_a_proposal_considered_and_not_offered_is_kept_on_the_run(
    engine: Engine, tenant: str
) -> None:
    patient, note = _patient_and_note(engine, tenant)
    considered = Considered(
        field_key="psychotherapy_history",
        proposed_text="None. Therapy intake appointment scheduled for next week.",
        what_changed="Intake scheduled",
        evidence_segment_ids=(4, 9),
        origin="transcript",
        reason="transient",
    )

    with _session(engine, tenant, _CLINICIAN_A) as session:
        repo = PostgresChartProposalRepository(session)
        repo.record_run(
            ProposalRun(note.id, patient.id, "ok", datetime.now(UTC), considered=(considered,))
        )
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        run = PostgresChartProposalRepository(session).run(note.id)

    assert run is not None
    assert run.considered == (considered,)


def test_a_medication_change_round_trips_and_accepting_writes_the_list(
    engine: Engine, tenant: str
) -> None:
    patient, note = _patient_and_note(engine, tenant)
    start = DraftedProposal(
        field_key="medications",
        item_key="hydroxyzine",
        proposed_text="hydroxyzine 25 mg, in the afternoon as needed",
        what_changed="Started for afternoon anxiety",
        evidence=(Evidence(5, "[00:26] Clinician: Start hydroxyzine 25 mg as needed."),),
        change=MedicationChange(
            action="start",
            drug_name="hydroxyzine",
            dose="25 mg",
            frequency="in the afternoon as needed",
            category="psychiatric",
        ),
    )

    with _session(engine, tenant, _CLINICIAN_A) as session:
        patients = PostgresPatientRepository(session)
        service = ChartProposalService(
            PostgresChartProposalRepository(session),
            ChartWriters(
                history=ChartHistoryService(PostgresChartHistoryRepository(session)),
                patients=patients,
                medications=MedicationService(PostgresMedicationRepository(session)),
            ),
            visit_date=date(2026, 10, 6),
        )
        service.refresh(note, ChartContext(), {}, [start])
        session.commit()
        (stored,) = service.proposals(note.id)
        assert stored.change == start.change
        current = patients.get(patient.id, _CLINICIAN_A)
        assert current is not None
        service.decide(note, current, stored.id, Choice("accept"), _CLINICIAN_A)
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        (row,) = PostgresMedicationRepository(session).list_by_patient(patient.id, _CLINICIAN_A)

    assert (
        row["drug_name"],
        row["frequency"],
        row["category"],
        row["started_at"],
        row["source_note_id"],
    ) == ("hydroxyzine", "in the afternoon as needed", "psychiatric", date(2026, 10, 6), note.id)


def test_waiting_counts_cover_pending_proposals_on_live_notes_for_the_grantee(
    engine: Engine, tenant: str
) -> None:
    patient, note = _patient_and_note(engine, tenant)
    _, deleted_note = _patient_and_note(engine, tenant)
    other_patient, other_note = _patient_and_note(engine, tenant)
    second = DraftedProposal(
        field_key="supports",
        proposed_text="Sister visits on weekends.",
        what_changed="New support",
        evidence=(Evidence(4, "[00:22] Client: My sister comes by on weekends."),),
    )

    # The tenant is shared across this module, so the total is measured as a
    # change from what the earlier tests left behind.
    with _session(engine, tenant, _CLINICIAN_A) as session:
        baseline = PostgresChartProposalRepository(session).count_patients_with_pending(
            _CLINICIAN_A
        )

    with _session(engine, tenant, _CLINICIAN_A) as session:
        history = ChartHistoryService(PostgresChartHistoryRepository(session))
        patients = PostgresPatientRepository(session)
        service = ChartProposalService(
            PostgresChartProposalRepository(session),
            ChartWriters(history=history, patients=patients),
        )
        service.refresh(note, ChartContext(), {}, [_PROPOSAL, second])
        service.refresh(other_note, ChartContext(), {}, [_PROPOSAL])
        service.refresh(deleted_note, ChartContext(), {}, [_PROPOSAL])
        session.execute(
            text("UPDATE notes SET deleted_at = now() WHERE id = CAST(:id AS uuid)"),
            {"id": deleted_note.id},
        )
        session.commit()
        before = PostgresChartProposalRepository(session).count_pending_by_patient(
            _CLINICIAN_A, [patient.id, deleted_note.patient_id]
        )
        assert before == {patient.id: 2}, "control: the grantee sees both, not the deleted note's"

        current = patients.get(patient.id, _CLINICIAN_A)
        assert current is not None
        stored = service.proposals(note.id)
        service.decide(note, current, stored[0].id, Choice("accept"), _CLINICIAN_A)
        session.commit()

    with _session(engine, tenant, _CLINICIAN_A) as session:
        repo = PostgresChartProposalRepository(session)
        after = repo.count_pending_by_patient(_CLINICIAN_A, [patient.id, other_patient.id])
        total = repo.count_patients_with_pending(_CLINICIAN_A)
        none_asked = repo.count_pending_by_patient(_CLINICIAN_A, [])
    with _session(engine, tenant, _CLINICIAN_B) as session:
        repo = PostgresChartProposalRepository(session)
        outsider = repo.count_pending_by_patient(_CLINICIAN_B, [patient.id, other_patient.id])
        outsider_total = repo.count_patients_with_pending(_CLINICIAN_B)

    assert after == {patient.id: 1, other_patient.id: 1}
    # Two clients with something waiting; the deleted note's client is not one.
    assert total - baseline == 2
    assert none_asked == {}
    assert (outsider, outsider_total) == ({}, 0)


# ---------------------------------------------------------------------------
# Proposals from an accepted intake form
# ---------------------------------------------------------------------------


def _accepted_form(engine: Engine, schema: str, patient_id: str) -> str:
    """An accepted intake form on ``patient_id``'s chart."""
    template_id, version_id, assignment_id = (str(uuid.uuid4()) for _ in range(3))
    with _session(engine, schema, _CLINICIAN_A) as session:
        session.execute(
            text(
                "INSERT INTO intake_packet_templates (id, name, created_by, created_at) "
                "VALUES (CAST(:tid AS uuid), 'Intake', CAST(:u AS uuid), now())"
            ),
            {"tid": template_id, "u": _CLINICIAN_A},
        )
        session.execute(
            text(
                "INSERT INTO intake_packet_versions "
                "(id, template_id, version, published_at, published_by, created_at) "
                "VALUES (CAST(:vid AS uuid), CAST(:tid AS uuid), 1, now(), "
                "CAST(:u AS uuid), now())"
            ),
            {"vid": version_id, "tid": template_id, "u": _CLINICIAN_A},
        )
        session.execute(
            text(
                "INSERT INTO patient_intake_assignments "
                "(id, patient_id, version_id, status, assigned_by, assigned_at, "
                "submitted_at, accepted_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:vid AS uuid), "
                "'accepted', CAST(:u AS uuid), now(), now(), now(), now())"
            ),
            {"id": assignment_id, "pid": patient_id, "vid": version_id, "u": _CLINICIAN_A},
        )
        session.commit()
    return assignment_id


_PRONOUNS = DraftedProposal(
    field_key="pronouns",
    proposed_text="they/them",
    what_changed="Answered on the intake",
    evidence=(Evidence(2, "they/them", item_id="item-pronouns", question="What pronouns?"),),
    origin="intake",
)


def test_a_form_s_proposals_round_trip_are_decided_and_stay_with_the_grantee(
    engine: Engine, tenant: str
) -> None:
    patient, _note = _patient_and_note(engine, tenant)
    form = _accepted_form(engine, tenant, patient.id)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        repo = PostgresChartProposalRepository(session)
        patients = PostgresPatientRepository(session)
        service = ChartProposalService(
            repo,
            ChartWriters(
                history=ChartHistoryService(PostgresChartHistoryRepository(session)),
                patients=patients,
            ),
        )
        service.refresh_intake(form, patient.id, [_PROPOSAL, _PRONOUNS])
        session.commit()
        (stored,) = [p for p in repo.list_for_intake(form) if p.field_key == "pronouns"]
        assert stored.note_id is None
        assert stored.intake_assignment_id == form
        assert stored.evidence == _PRONOUNS.evidence
        assert repo.count_pending_by_patient(_CLINICIAN_A, [patient.id]) == {patient.id: 2}

        current = patients.get(patient.id, _CLINICIAN_A)
        assert current is not None
        service.decide_for_intake(form, current, stored.id, Choice("accept"), _CLINICIAN_A)
        session.commit()
        saved = patients.get(patient.id, _CLINICIAN_A)
        assert saved is not None
        assert saved.pronouns == "they/them"

        # Proposing again replaces what is pending and keeps the decision.
        service.refresh_intake(form, patient.id, [_PRONOUNS])
        session.commit()
        listed = repo.list_for_intake(form)
        assert [(p.field_key, p.decision) for p in listed] == [("pronouns", "accepted")]

    with _session(engine, tenant, _CLINICIAN_B) as session:
        repo = PostgresChartProposalRepository(session)
        assert repo.list_for_intake(form) == []
        assert repo.list_pending_for_patient(patient.id) == []


@pytest.mark.parametrize("case", ["neither", "both", "another chart"])
def test_a_proposal_names_exactly_one_source_on_the_form_s_own_chart(
    engine: Engine, tenant: str, case: str
) -> None:
    patient, note = _patient_and_note(engine, tenant)
    other, _ = _patient_and_note(engine, tenant)
    form = _accepted_form(engine, tenant, patient.id)
    values = {
        "neither": {"note": None, "form": None, "pid": patient.id},
        "both": {"note": note.id, "form": form, "pid": patient.id},
        "another chart": {"note": None, "form": form, "pid": other.id},
    }[case]
    expected = (
        "fk_note_chart_proposals_intake"
        if case == "another chart"
        else "ck_note_chart_proposals_one_source"
    )
    with (
        _session(engine, tenant, _CLINICIAN_A) as session,
        pytest.raises(IntegrityError, match=expected),
    ):
        session.execute(
            text(
                "INSERT INTO note_chart_proposals (id, note_id, intake_assignment_id, "
                "patient_id, field_key, proposed_text, what_changed, origin, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:note AS uuid), CAST(:form AS uuid), "
                "CAST(:pid AS uuid), 'pronouns', 'they/them', 'x', 'intake', now())"
            ),
            {"id": str(uuid.uuid4()), **values},
        )
