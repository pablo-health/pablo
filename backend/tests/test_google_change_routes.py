# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Routes that settle Google Calendar changes the sync left for the therapist."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from app.main import app
from app.repositories.audit import InMemoryAuditRepository
from app.routes.scheduling import get_google_change_follower
from app.scheduling_engine.models.appointment import (
    Appointment,
    AppointmentStatus,
    CancellationActor,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services import get_audit_service
from app.services.audit_service import AuditService
from app.services.google_calendar_follow import GoogleChangeFollower, GoogleSyncStatus
from app.services.google_calendar_service import PushedEvent
from app.utcnow import utc_now

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

USER_ID = "test-user-123"


def _appointment(appt_id: str, days: int, sync_status: str) -> Appointment:
    start = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return Appointment(
        id=appt_id,
        user_id=USER_ID,
        patient_id="patient-1",
        title="Session",
        start_at=start,
        end_at=start + timedelta(minutes=50),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
        google_event_id=f"evt-{appt_id}",
        google_sync_status=sync_status,
    )


class _Wired:
    def __init__(self) -> None:
        self.repo = InMemoryAppointmentRepository()
        self.repo.grant_access("patient-1", USER_ID)
        self.calendar = MagicMock()
        self.calendar.push_appointment_event.return_value = PushedEvent("evt-new", None)
        self.audit_repo = InMemoryAuditRepository()


@pytest.fixture
def wired(client: TestClient) -> _Wired:
    """Overrides on top of ``client``, whose teardown clears them."""
    w = _Wired()
    app.dependency_overrides[get_google_change_follower] = lambda: GoogleChangeFollower(
        w.repo, w.calendar
    )
    app.dependency_overrides[get_audit_service] = lambda: AuditService(w.audit_repo)
    return w


def test_undo_restores_a_session_removed_in_google(client: TestClient, wired: _Wired) -> None:
    appt = wired.repo.create(_appointment("a", 3, GoogleSyncStatus.REMOVED_IN_GOOGLE))
    appt.status = AppointmentStatus.CANCELLED
    appt.cancelled_by = CancellationActor.SYSTEM
    appt.late_cancellation = False

    response = client.post("/api/appointments/a/google-change", json={"resolution": "keep_pablo"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["google_event_id"] == "evt-new"
    assert body["google_sync_status"] == "synced"
    wired.calendar.push_appointment_event.assert_called_once()
    [entry] = wired.audit_repo.list_for_user(USER_ID)
    assert entry.action == "appointment_updated"
    assert entry.changes == {"google_change_resolution": "keep_pablo"}


def test_nothing_to_settle_is_a_conflict(client: TestClient, wired: _Wired) -> None:
    wired.repo.create(_appointment("a", 3, GoogleSyncStatus.SYNCED))

    response = client.post(
        "/api/appointments/a/google-change", json={"resolution": "accept_google"}
    )

    assert response.status_code == 409


def test_an_unknown_resolution_is_refused(client: TestClient, wired: _Wired) -> None:
    wired.repo.create(_appointment("a", 3, GoogleSyncStatus.EXTERNAL_CHANGE))

    response = client.post("/api/appointments/a/google-change", json={"resolution": "both"})

    assert response.status_code == 422


def test_held_removals_are_counted_and_settled_together(client: TestClient, wired: _Wired) -> None:
    for i in range(5):
        wired.repo.create(_appointment(f"s{i}", 1 + i, GoogleSyncStatus.MISSING_IN_GOOGLE))

    assert client.get("/api/google-calendar/held-removals").json() == {"count": 5}

    response = client.post(
        "/api/google-calendar/held-removals", json={"resolution": "accept_google"}
    )

    assert response.json() == {"count": 5}
    assert client.get("/api/google-calendar/held-removals").json() == {"count": 0}
    rows = wired.repo.list_by_range(USER_ID, utc_now(), utc_now() + timedelta(days=30))
    assert [r.status for r in rows] == [AppointmentStatus.CANCELLED] * 5
