# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route tests for GET /api/billing/unbilled-sessions.

The queue's whole point is that "unbilled" is derived, not stored — these
tests exercise exactly the states that derivation has to get right: nothing
finalized yet, a session with no charge at all, one whose only charge
succeeded (must drop out), and one whose only charge failed (must stay, so
it can be retried).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.main import app
from app.models import Patient
from app.models.note import Note
from app.models.session import TherapySession, Transcript
from app.repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_patient_payment_repository,
)
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.appointment_type import InMemoryAppointmentTypeRepository

if TYPE_CHECKING:
    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )

PATIENT_ID = "patient-1"


class _FakePayments:
    """Just enough of PatientPaymentRepository for the queue's read."""

    def __init__(self, succeeded_for: set[str] | None = None) -> None:
        self._succeeded_for = succeeded_for or set()

    def succeeded_appointment_ids(self, appointment_ids: list[str]) -> set[str]:
        return {a for a in appointment_ids if a in self._succeeded_for}


def _note(session_id: str, *, finalized: bool) -> Note:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=PATIENT_ID,
        session_id=session_id,
        note_type="soap",
        finalized_at=now if finalized else None,
        created_at=now,
        updated_at=now,
    )


def _session(session_id: str, session_date: datetime, *, user_id: str) -> TherapySession:
    return TherapySession(
        id=session_id,
        user_id=user_id,
        patient_id=PATIENT_ID,
        session_date=session_date,
        session_number=1,
        status="completed",
        transcript=Transcript(format="txt", content="x"),
        created_at=session_date,
    )


def _appointment(appointment_id: str, session_id: str, *, user_id: str) -> Appointment:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    return Appointment(
        id=appointment_id,
        user_id=user_id,
        patient_id=PATIENT_ID,
        title="Session",
        start_at=now,
        end_at=now,
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_appointment_repository, None)
    app.dependency_overrides.pop(get_appointment_type_repository, None)
    app.dependency_overrides.pop(get_patient_payment_repository, None)


def _wire(
    *,
    appt_repo: InMemoryAppointmentRepository | None = None,
    type_repo: InMemoryAppointmentTypeRepository | None = None,
    payments: _FakePayments | None = None,
) -> None:
    app.dependency_overrides[get_appointment_repository] = lambda: (
        appt_repo or InMemoryAppointmentRepository()
    )
    app.dependency_overrides[get_appointment_type_repository] = lambda: (
        type_repo or InMemoryAppointmentTypeRepository()
    )
    app.dependency_overrides[get_patient_payment_repository] = lambda: payments or _FakePayments()


def _seed_patient(
    mock_repo: InMemoryPatientRepository, mock_user_id: str, *, rate_cents: int
) -> None:
    now = datetime.now(UTC)
    mock_repo.create(
        Patient(
            id=PATIENT_ID,
            first_name="Ada",
            last_name="Early",
            created_at=now,
            updated_at=now,
            rate_cents=rate_cents,
        ),
        mock_user_id,
    )


def test_empty_queue_when_nothing_finalized(client) -> None:
    _wire()
    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


def test_populated_queue_shows_client_date_and_resolved_amount(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    session_date = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)
    mock_session_repo.create(_session("sess-1", session_date, user_id=mock_user_id))
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    _wire()

    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["session_id"] == "sess-1"
    assert items[0]["patient_id"] == PATIENT_ID
    assert items[0]["patient_name"] == "Ada Early"
    assert items[0]["amount_cents"] == 15000


def test_unfinalized_note_is_not_in_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=False), mock_user_id)
    _wire()

    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.json()["items"] == []


def test_succeeded_charge_drops_the_session_from_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    _wire(appt_repo=appt_repo, payments=_FakePayments(succeeded_for={"appt-1"}))

    resp = client.get("/api/billing/unbilled-sessions")
    assert resp.json()["items"] == []


def test_failed_charge_keeps_the_session_in_the_queue(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=15000)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(_appointment("appt-1", "sess-1", user_id=mock_user_id))
    # A failed charge exists but is not a *succeeded* one — the fake payments
    # repo (like the real one) only ever reports succeeded appointment ids,
    # so a failed-only appointment simply never appears in that set.
    _wire(appt_repo=appt_repo, payments=_FakePayments(succeeded_for=set()))

    resp = client.get("/api/billing/unbilled-sessions")
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["session_id"] == "sess-1"


def test_amount_falls_back_to_appointment_type_default(
    client,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    _seed_patient(mock_repo, mock_user_id, rate_cents=None)
    mock_session_repo.create(
        _session("sess-1", datetime(2026, 6, 10, tzinfo=UTC), user_id=mock_user_id)
    )
    mock_notes_repo.add(_note("sess-1", finalized=True), mock_user_id)
    appt = _appointment("appt-1", "sess-1", user_id=mock_user_id)
    appt.appointment_type_id = "type-1"
    appt_repo = InMemoryAppointmentRepository()
    appt_repo.create(appt)
    type_repo = InMemoryAppointmentTypeRepository()
    type_repo.create(
        AppointmentType(
            id="type-1", user_id=mock_user_id, name="Individual", default_fee_cents=12000
        )
    )
    _wire(appt_repo=appt_repo, type_repo=type_repo)

    resp = client.get("/api/billing/unbilled-sessions")
    items = resp.json()["items"]
    assert items[0]["amount_cents"] == 12000
