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
        user_id="test-user-123",
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
