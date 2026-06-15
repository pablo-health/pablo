# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository batch reads/writes that replace per-row N+1 loops on hot paths.

Exercised against the in-memory repositories — the Postgres implementations
share the same contract and are covered by the integration suite.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.enums import SessionStatus
from app.models.session import TherapySession, Transcript
from app.models.user import UserPreferences
from app.repositories.session import InMemoryTherapySessionRepository
from app.repositories.user import InMemoryUserRepository
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _session(
    patient_id: str, user_id: str, when: datetime, status: SessionStatus
) -> TherapySession:
    return TherapySession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        session_date=when,
        session_number=1,
        status=status,
        transcript=Transcript(format="txt", content=""),
        created_at=_NOW,
        scheduled_at=when,
    )


def _appt(user_id: str, patient_id: str) -> Appointment:
    return Appointment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        title="Visit",
        start_at=_NOW,
        end_at=_NOW + timedelta(hours=1),
        duration_minutes=60,
        status="confirmed",
        session_type="individual",
    )


# --- get_next_session_date (session next-date without paging all sessions) ---


def test_get_next_session_date_returns_earliest_future_excluding_terminal():
    repo = InMemoryTherapySessionRepository()
    pid, uid = "patient-1", "user-1"
    earliest_future = _NOW + timedelta(days=3)
    repo.create(_session(pid, uid, earliest_future, SessionStatus.SCHEDULED))
    repo.create(_session(pid, uid, _NOW + timedelta(days=5), SessionStatus.SCHEDULED))
    # Sooner, but cancelled — must be excluded.
    repo.create(_session(pid, uid, _NOW + timedelta(days=1), SessionStatus.CANCELLED))
    # Past — must be ignored.
    repo.create(_session(pid, uid, _NOW - timedelta(days=2), SessionStatus.SCHEDULED))

    result = repo.get_next_session_date(
        pid,
        uid,
        after=_NOW,
        exclude_statuses={SessionStatus.CANCELLED, SessionStatus.FINALIZED, SessionStatus.FAILED},
    )

    assert result == earliest_future


def test_get_next_session_date_none_when_no_upcoming():
    repo = InMemoryTherapySessionRepository()
    pid, uid = "patient-1", "user-1"
    repo.create(_session(pid, uid, _NOW - timedelta(days=1), SessionStatus.SCHEDULED))
    assert repo.get_next_session_date(pid, uid, after=_NOW, exclude_statuses=set()) is None


def test_get_next_session_date_requires_patient_access():
    repo = InMemoryTherapySessionRepository()
    repo.create(_session("patient-1", "owner", _NOW + timedelta(days=1), SessionStatus.SCHEDULED))
    # A user with no grant on the patient sees nothing.
    assert (
        repo.get_next_session_date("patient-1", "intruder", after=_NOW, exclude_statuses=set())
        is None
    )


# --- get_preferences_many (one read for the whole dispatch batch) ---


def test_get_preferences_many_returns_saved_and_defaults():
    repo = InMemoryUserRepository()
    saved = UserPreferences()
    repo.save_preferences("u1", saved)

    result = repo.get_preferences_many(["u1", "u2"])

    assert set(result) == {"u1", "u2"}
    assert result["u1"] is saved
    assert isinstance(result["u2"], UserPreferences)  # default for the unsaved id


def test_get_preferences_many_empty():
    assert InMemoryUserRepository().get_preferences_many([]) == {}


# --- bulk_set_patient (one UPDATE instead of per-appointment) ---


def test_bulk_set_patient_links_only_listed_ids_and_counts():
    repo = InMemoryAppointmentRepository()
    a1, a2, a3 = _appt("u1", ""), _appt("u1", ""), _appt("u1", "")
    for a in (a1, a2, a3):
        repo.create(a)

    updated = repo.bulk_set_patient([a1.id, a2.id, "does-not-exist"], "patient-9")

    assert updated == 2
    assert repo._appointments[a1.id].patient_id == "patient-9"
    assert repo._appointments[a2.id].patient_id == "patient-9"
    assert repo._appointments[a3.id].patient_id == ""  # not in the id list — untouched
    assert repo._appointments[a1.id].updated_at == repo._appointments[a2.id].updated_at


def test_bulk_set_patient_empty_is_noop():
    assert InMemoryAppointmentRepository().bulk_set_patient([], "patient-9") == 0
