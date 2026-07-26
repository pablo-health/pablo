# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route-level tests for scheduling endpoints (FastAPI dependency wiring)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from app.main import app
from app.models import Patient, SessionStatus
from app.models.session import TherapySession, Transcript
from app.notes import get_note_type_authorizer
from app.routes.scheduling import _get_session_service, get_scheduling_service
from app.scheduling_engine.models.appointment import Appointment
from app.services import get_audit_service

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _appointment(appt_id: str = "appt-1") -> Any:
    appt = MagicMock()
    appt.id = appt_id
    appt.user_id = "test-user-123"
    appt.patient_id = "patient-1"
    appt.start_at = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
    appt.end_at = datetime(2026, 4, 15, 14, 50, tzinfo=UTC)
    appt.duration_minutes = 50
    appt.session_type = "individual"
    appt.video_link = None
    appt.video_platform = None
    appt.notes = None
    appt.session_id = None
    return appt


def _real_appointment(status: str = "confirmed", appt_id: str = "appt-1") -> Appointment:
    return Appointment(
        id=appt_id,
        user_id="test-user-123",
        patient_id="patient-1",
        title="Weekly check-in",
        start_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        end_at=datetime(2026, 4, 15, 14, 50, tzinfo=UTC),
        duration_minutes=50,
        status=status,
        session_type="individual",
    )


def _session() -> TherapySession:
    return TherapySession(
        id="session-1",
        user_id="test-user-123",
        patient_id="patient-1",
        session_date=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        session_number=1,
        status=SessionStatus.SCHEDULED,
        transcript=Transcript(format="txt", content=""),
        created_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        scheduled_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        duration_minutes=50,
        session_type="individual",
        source="companion",
    )


def _patient() -> Patient:
    return Patient(
        id="patient-1",
        first_name="Jane",
        last_name="Smith",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        session_count=0,
    )


def _wire_scheduling_overrides(*, scheduling_svc: MagicMock, session_svc: MagicMock) -> None:
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc
    app.dependency_overrides[_get_session_service] = lambda: session_svc


def test_start_session_default_authorizer_allows_explicit_note_type(
    client: TestClient,
) -> None:
    """OSS default authorizer returns True → explicit note_type='soap' returns 201."""
    scheduling_svc = MagicMock()
    scheduling_svc.get_appointment.return_value = _appointment()
    session_svc = MagicMock()
    session_svc.schedule_session.return_value = (_session(), _patient())

    _wire_scheduling_overrides(scheduling_svc=scheduling_svc, session_svc=session_svc)

    response = client.post(
        "/api/appointments/appt-1/start-session",
        json={"note_type": "soap"},
    )

    assert response.status_code == 201, response.text
    session_svc.schedule_session.assert_called_once()
    scheduling_svc.update_appointment.assert_called_once()


def test_start_session_overridden_authorizer_returns_403(client: TestClient) -> None:
    """Overlay override → is_allowed=False on requested note_type returns 403."""
    scheduling_svc = MagicMock()
    scheduling_svc.get_appointment.return_value = _appointment()
    session_svc = MagicMock()
    session_svc.schedule_session.return_value = (_session(), _patient())

    _wire_scheduling_overrides(scheduling_svc=scheduling_svc, session_svc=session_svc)

    denying_authorizer = MagicMock()
    denying_authorizer.is_allowed.return_value = False
    app.dependency_overrides[get_note_type_authorizer] = lambda: denying_authorizer

    response = client.post(
        "/api/appointments/appt-1/start-session",
        json={"note_type": "dap"},
    )

    assert response.status_code == 403, response.text
    assert "dap" in response.json()["detail"]
    session_svc.schedule_session.assert_not_called()
    denying_authorizer.is_allowed.assert_called_once()


def test_patch_appointment_valid_status_updates_and_audits(client: TestClient) -> None:
    """PATCH with a valid status updates the appointment and audits status as a changed field."""
    scheduling_svc = MagicMock()
    scheduling_svc.update_appointment.return_value = _real_appointment(status="completed")
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc

    audit = MagicMock()
    app.dependency_overrides[get_audit_service] = lambda: audit

    response = client.patch("/api/appointments/appt-1", json={"status": "completed"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"

    scheduling_svc.update_appointment.assert_called_once()
    _, kwargs = scheduling_svc.update_appointment.call_args
    assert kwargs["status"] == "completed"

    audit.log_appointment_action.assert_called_once()
    _, audit_kwargs = audit.log_appointment_action.call_args
    assert "status" in audit_kwargs["changes"]["changed_fields"]


def test_patch_appointment_invalid_status_rejected(client: TestClient) -> None:
    """PATCH with an out-of-enum status is rejected before reaching the service."""
    scheduling_svc = MagicMock()
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc

    response = client.patch("/api/appointments/appt-1", json={"status": "rescheduled"})

    assert response.status_code == 422, response.text
    scheduling_svc.update_appointment.assert_not_called()


def test_list_appointments_rejects_malformed_range(client: TestClient) -> None:
    """Garbage start/end query params are rejected at the request layer (422),
    never reaching the service to surface as an unhandled 500."""
    scheduling_svc = MagicMock()
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc

    response = client.get("/api/appointments", params={"start": "garbage", "end": "nope"})

    assert response.status_code == 422, response.text
    scheduling_svc.list_appointments.assert_not_called()


def test_list_appointments_accepts_iso_range(client: TestClient) -> None:
    """A valid ISO 8601 range reaches the service and returns 200."""
    scheduling_svc = MagicMock()
    scheduling_svc.list_appointments.return_value = [_real_appointment()]
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc

    response = client.get(
        "/api/appointments",
        params={"start": "2026-04-15T00:00:00Z", "end": "2026-04-16T00:00:00Z"},
    )

    assert response.status_code == 200, response.text
    scheduling_svc.list_appointments.assert_called_once()
