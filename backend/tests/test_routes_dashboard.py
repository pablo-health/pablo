# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route tests for GET /api/dashboard/summary.

The dashboard summary replaces the panel-by-panel fan-out. Two properties
matter beyond "it returns data":

1. Counts are computed over the *full* accessible set, not a 20-row page —
   so a backlog of >5 pending-review sessions reports the true total while
   only the inline rows come back. (The old per-panel filter ran over a
   single page and silently undercounted.)
2. Audit reflects only what is disclosed: the awaiting-review rows shown are
   audited ``session_viewed``; the today-appointment patients' last-visit
   dates are joined without a blind patient-list read, so no spurious
   ``patient_viewed`` rows are written.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from app.chart_proposals.dependencies import get_chart_proposal_repository
from app.chart_proposals.models import ChartProposal, Decision
from app.main import app
from app.models import Patient, SessionStatus
from app.models.audit import AuditAction
from app.models.note import Note
from app.models.session import TherapySession, Transcript
from app.repositories import InMemoryChartProposalRepository
from app.routes.scheduling import get_scheduling_service
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services import AuditService, get_audit_service
from app.services.note_service import NoteService
from app.services.session_service import SessionService

if TYPE_CHECKING:
    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )

TODAY_START = "2026-06-15T00:00:00+00:00"
TODAY_END = "2026-06-16T00:00:00+00:00"
WEEK_START = "2026-06-16T00:00:00+00:00"
WEEK_END = "2026-06-22T00:00:00+00:00"

P1 = "patient-with-appt"
P2 = "patient-no-appt"


def _appt(user_id: str, patient_id: str, start: str, *, status: AppointmentStatus) -> Appointment:
    now = datetime.now(UTC)
    start_dt = datetime.fromisoformat(start)
    return Appointment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        title="Session",
        start_at=start_dt,
        end_at=start_dt,
        duration_minutes=50,
        status=status,
        session_type="individual",
        created_at=now,
        updated_at=now,
    )


def _session(user_id: str, patient_id: str, n: int, status: SessionStatus) -> TherapySession:
    return TherapySession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        session_date=datetime(2026, 6, 1, 10, n % 60, tzinfo=UTC),
        session_number=n,
        status=status,
        transcript=Transcript(format="txt", content="x"),
        created_at=datetime.now(UTC),
    )


def _proposal(patient_id: str, field_key: str, decision: Decision = "pending") -> ChartProposal:
    return ChartProposal(
        id=str(uuid.uuid4()),
        note_id=str(uuid.uuid4()),
        patient_id=patient_id,
        field_key=field_key,
        item_key="",
        proposed_text="Works at the library.",
        what_changed="New job",
        evidence=(),
        origin="note",
        created_at=datetime.now(UTC),
        decision=decision,
    )


@pytest.fixture
def audit_spy() -> MagicMock:
    return MagicMock(spec=AuditService)


@pytest.fixture
def chart_proposals() -> InMemoryChartProposalRepository:
    return InMemoryChartProposalRepository()


@pytest.fixture
def seeded_client(
    client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    audit_spy: MagicMock,
    chart_proposals: InMemoryChartProposalRepository,
):
    """The shared client plus a seeded calendar + audit spy for the dashboard."""
    # Two patients: P1 has today's appointment, P2 has none. P2 must never be
    # fetched by the summary — that's the blind-patient-list regression guard.
    now = datetime.now(UTC)
    mock_repo.create(
        Patient(
            id=P1,
            first_name="Ada",
            last_name="Early",
            created_at=now,
            updated_at=now,
            last_session_date=datetime(2026, 6, 8, tzinfo=UTC),
        ),
        mock_user_id,
    )
    mock_repo.create(
        Patient(id=P2, first_name="Bea", last_name="Absent", created_at=now, updated_at=now),
        mock_user_id,
    )

    appt_repo = InMemoryAppointmentRepository()
    today = "2026-06-15T14:00:00+00:00"
    week = "2026-06-17T14:00:00+00:00"
    week_cancelled = "2026-06-18T14:00:00+00:00"
    appt_repo.create(_appt(mock_user_id, P1, today, status=AppointmentStatus.CONFIRMED))
    appt_repo.create(_appt(mock_user_id, P1, week, status=AppointmentStatus.CONFIRMED))
    appt_repo.create(_appt(mock_user_id, P1, week_cancelled, status=AppointmentStatus.CANCELLED))

    # 7 awaiting-review (only 5 should come back inline; total must read 7),
    # plus 2 queued + 1 processing (transcription in flight).
    n = 0
    for _ in range(7):
        n += 1
        mock_session_repo.create(_session(mock_user_id, P1, n, SessionStatus.PENDING_REVIEW))
    for status in (SessionStatus.QUEUED, SessionStatus.QUEUED, SessionStatus.PROCESSING):
        n += 1
        mock_session_repo.create(_session(mock_user_id, P1, n, status))

    # 3 unsigned, session-attached notes (notes awaiting signature).
    for i in range(3):
        mock_notes_repo.add(
            Note(
                id=str(uuid.uuid4()),
                patient_id=P1,
                session_id=f"sess-{i}",
                note_type="soap",
                content={"subjective": "S"},
                finalized_at=None,
                created_at=now,
                updated_at=now,
            ),
            mock_user_id,
        )

    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo)
    app.dependency_overrides[get_audit_service] = lambda: audit_spy
    app.dependency_overrides[get_chart_proposal_repository] = lambda: chart_proposals
    # The `client` fixture clears all overrides (including these) on teardown.
    return client


def _get_summary(client) -> dict:
    resp = client.get(
        "/api/dashboard/summary",
        params={
            "today_start": TODAY_START,
            "today_end": TODAY_END,
            "week_start": WEEK_START,
            "week_end": WEEK_END,
        },
    )
    assert resp.status_code == 200, resp.text
    data: dict = resp.json()
    return data


def test_counts_reflect_full_set_not_a_page(seeded_client) -> None:
    body = _get_summary(seeded_client)
    # Full count even though only the inline rows are returned.
    assert body["awaiting_review_total"] == 7
    assert len(body["awaiting_review"]) == 5
    assert body["transcription_pending_count"] == 3
    assert body["notes_pending_count"] == 3
    # Only the confirmed rest-of-week appointment counts (cancelled excluded).
    assert body["week_confirmed_count"] == 1


def test_today_appointments_and_last_visit_only_for_those_patients(seeded_client) -> None:
    body = _get_summary(seeded_client)
    assert len(body["today_appointments"]) == 1
    assert body["today_appointments"][0]["patient_id"] == P1
    # last_visit map is keyed to today's appointment patients only — the
    # patient with no appointment (P2) is never fetched.
    assert set(body["last_visit_by_patient"]) == {P1}
    assert body["last_visit_by_patient"][P1] is not None


def test_audits_only_disclosed_sessions_and_no_patient_views(
    seeded_client,
    audit_spy: MagicMock,
) -> None:
    _get_summary(seeded_client)
    # Exactly the 5 awaiting-review rows shown are audited as session views.
    assert audit_spy.log_session_action.call_count == 5
    for call in audit_spy.log_session_action.call_args_list:
        assert call.args[0] == AuditAction.SESSION_VIEWED
    # The blind patient-list read is gone — no patient_viewed rows.
    audit_spy.log_patient_action.assert_not_called()


def test_today_appointment_carries_its_linked_session_status(
    seeded_client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> None:
    """A started visit reads as its session, not as the still-confirmed appointment."""
    session = _session(mock_user_id, P1, 99, SessionStatus.PENDING_REVIEW)
    mock_session_repo.create(session)
    started = _appt(
        mock_user_id, P1, "2026-06-15T16:00:00+00:00", status=AppointmentStatus.CONFIRMED
    )
    started.session_id = session.id
    appt_repo = InMemoryAppointmentRepository()
    not_started = _appt(
        mock_user_id, P1, "2026-06-15T14:00:00+00:00", status=AppointmentStatus.CONFIRMED
    )
    appt_repo.create(not_started)
    appt_repo.create(started)
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo)

    rows = {a["id"]: a for a in _get_summary(seeded_client)["today_appointments"]}

    assert rows[started.id]["status"] == "confirmed"
    assert rows[started.id]["session_status"] == "pending_review"
    assert rows[not_started.id]["session_status"] is None


def test_an_imported_note_is_never_counted_as_awaiting_signature(
    seeded_client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    """A note imported from another system was signed there; it is not on the to-sign list."""
    sessions = SessionService(
        mock_session_repo, mock_repo, MagicMock(), NoteService(mock_notes_repo)
    )
    imported, _, _ = sessions.import_session(
        P1,
        mock_user_id,
        session_date=datetime(2026, 5, 1, tzinfo=UTC),
        source_text="Chief complaint: sleep.",
        note_content={"subjective": {}},
    )

    body = _get_summary(seeded_client)

    assert body["awaiting_review_total"] == 7
    assert body["notes_pending_count"] == 3
    assert imported.id not in {row["session_id"] for row in body["awaiting_review"]}


def test_chart_updates_waiting_are_counted_for_todays_clients_and_in_total(
    seeded_client, chart_proposals: InMemoryChartProposalRepository, mock_user_id: str
) -> None:
    """Today's client carries its pending count; the total counts clients, not updates."""
    elsewhere = "patient-of-another-clinician"
    chart_proposals.withhold(elsewhere, mock_user_id)
    chart_proposals.add(
        [
            _proposal(P1, "work_school"),
            _proposal(P1, "supports"),
            # Decided already: no longer waiting.
            _proposal(P1, "living_situation", decision="accepted"),
            # P2 has no appointment today, so only the total sees it.
            _proposal(P2, "work_school"),
            # No grant on this one: never counted.
            _proposal(elsewhere, "work_school"),
        ]
    )

    body = _get_summary(seeded_client)

    assert body["pending_chart_updates_by_patient"] == {P1: 2}
    assert body["pending_chart_updates_total"] == 2


def test_no_chart_updates_waiting_leaves_the_rows_bare(seeded_client) -> None:
    body = _get_summary(seeded_client)

    assert body["pending_chart_updates_by_patient"] == {}
    assert body["pending_chart_updates_total"] == 0


def test_deciding_the_last_update_takes_the_client_off_the_count(
    seeded_client, chart_proposals: InMemoryChartProposalRepository, mock_user_id: str
) -> None:
    waiting = _proposal(P1, "work_school")
    chart_proposals.add([waiting])
    assert _get_summary(seeded_client)["pending_chart_updates_by_patient"] == {P1: 1}

    chart_proposals.decide(waiting.id, "accepted", None, mock_user_id, datetime.now(UTC))

    body = _get_summary(seeded_client)
    assert body["pending_chart_updates_by_patient"] == {}
    assert body["pending_chart_updates_total"] == 0
