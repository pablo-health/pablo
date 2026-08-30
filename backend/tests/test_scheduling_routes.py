# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route-level tests for scheduling endpoints (FastAPI dependency wiring)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.calendar_providers.capabilities import CalendarCapability, CalendarWriteTarget
from app.calendar_providers.oauth_state import OAuthStateError
from app.main import app
from app.models import Patient, SessionStatus, UserPreferences
from app.models.session import TherapySession, Transcript
from app.notes import get_note_type_authorizer
from app.routes.scheduling import (
    _get_session_service,
    get_availability_rule_parse_service,
    get_availability_rule_repository,
    get_google_calendar_service,
    get_scheduling_service,
)
from app.routes.scheduling import (
    get_patient_repository as get_scheduling_patient_repository,
)
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.models.availability import AvailabilityRule, RuleType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services import get_audit_service
from app.services.availability_parse_service import AvailabilityRuleParseService
from app.services.google_calendar_service import GoogleCalendarService
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion
from fastapi import HTTPException, status

if TYPE_CHECKING:
    from app.repositories import InMemoryUserRepository
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


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "patient_id": "patient-1",
        "title": "Weekly check-in",
        "start_at": "2026-04-15T14:00:00Z",
        "end_at": "2026-04-15T14:50:00Z",
        "duration_minutes": 50,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def appt_repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def rule_repo() -> InMemoryAvailabilityRuleRepository:
    return InMemoryAvailabilityRuleRepository()


@pytest.fixture
def write_client(
    client: TestClient,
    appt_repo: InMemoryAppointmentRepository,
    rule_repo: InMemoryAvailabilityRuleRepository,
) -> TestClient:
    """A ``client`` wired to the real SchedulingService over in-memory repos,
    rather than a MagicMock. The tests using this fixture exercise the
    write path's actual validation, including availability-rule enforcement,
    instead of asserting on canned mock returns.
    """
    engine = AvailabilityEngine(rule_repo, appt_repo)
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo, engine)
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    return client


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


def test_list_appointments_carries_patient_name(
    client: TestClient, mock_repo: Any, mock_user_id: str
) -> None:
    """The list payload resolves each patient's display name server-side.

    The calendar labels events from this field; before it existed the client
    joined against its own patient list, whose first page is all it fetches,
    so patients sorted past the page size rendered with no name.
    """
    mock_repo.create(_patient(), mock_user_id)
    scheduling_svc = MagicMock()
    scheduling_svc.list_appointments.return_value = [_real_appointment()]
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc
    app.dependency_overrides[get_scheduling_patient_repository] = lambda: mock_repo

    response = client.get(
        "/api/appointments",
        params={"start": "2026-04-15T00:00:00Z", "end": "2026-04-16T00:00:00Z"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["patient_name"] == "Jane Smith"


def test_list_appointments_name_null_without_grant(client: TestClient) -> None:
    """An appointment whose patient the caller has no live grant for still
    lists, but with a null name — the lookup must not leak or error."""
    scheduling_svc = MagicMock()
    scheduling_svc.list_appointments.return_value = [_real_appointment()]
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc
    # Repo deliberately NOT seeded with the patient — no live grant.

    response = client.get(
        "/api/appointments",
        params={"start": "2026-04-15T00:00:00Z", "end": "2026-04-16T00:00:00Z"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["patient_name"] is None


def test_list_appointments_audits_each_row(client: TestClient) -> None:
    """Reading the list is a per-record identifier read (the payload carries
    patient names), so each returned appointment writes an audit row."""
    scheduling_svc = MagicMock()
    scheduling_svc.list_appointments.return_value = [
        _real_appointment(appt_id="appt-1"),
        _real_appointment(appt_id="appt-2"),
    ]
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc
    audit = MagicMock()
    app.dependency_overrides[get_audit_service] = lambda: audit

    response = client.get(
        "/api/appointments",
        params={"start": "2026-04-15T00:00:00Z", "end": "2026-04-16T00:00:00Z"},
    )

    assert response.status_code == 200, response.text
    assert audit.log_appointment_action.call_count == 2
    audited_ids = {call.args[3] for call in audit.log_appointment_action.call_args_list}
    assert audited_ids == {"appt-1", "appt-2"}


def test_create_appointment_carries_patient_name(
    client: TestClient, mock_repo: Any, mock_user_id: str
) -> None:
    """The create response carries the name too, so the calendar can label
    the event straight from the mutation result."""
    mock_repo.create(_patient(), mock_user_id)
    scheduling_svc = MagicMock()
    scheduling_svc.create_appointment.return_value = _real_appointment()
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc
    app.dependency_overrides[get_scheduling_patient_repository] = lambda: mock_repo

    response = client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    assert response.json()["patient_name"] == "Jane Smith"


def test_check_conflicts_permissive_when_unconfigured(client: TestClient) -> None:
    """A practice with no availability rules gets neither conflicts nor a
    hard block — booking must stay permissive until rules are set up."""
    app.dependency_overrides[get_availability_rule_repository] = InMemoryAvailabilityRuleRepository

    response = client.post(
        "/api/availability/check",
        json={"start_at": "2026-04-15T14:00:00Z", "end_at": "2026-04-15T14:50:00Z"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conflicts"] == []
    assert body["has_hard_conflicts"] is False
    assert body["configured"] is False


def test_free_slots_resolves_default_duration_from_session_defaults(
    client: TestClient, rule_repo: InMemoryAvailabilityRuleRepository
) -> None:
    """Without a duration query param, the resolved default comes from the
    user's session_defaults rule and is echoed back in duration_minutes."""
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    rule_repo.create(
        AvailabilityRule(
            id="rule-1",
            user_id="test-user-123",
            rule_type=RuleType.WORKING_HOURS,
            enforcement="hard",
            params={"day_of_week": 2, "start": "09:00", "end": "17:00"},
        )
    )
    rule_repo.create(
        AvailabilityRule(
            id="rule-2",
            user_id="test-user-123",
            rule_type=RuleType.SESSION_DEFAULTS,
            enforcement="soft",
            params={"duration_minutes": 60},
        )
    )

    response = client.get("/api/availability/slots", params={"date": "2026-04-15"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duration_minutes"] == 60

    explicit_response = client.get(
        "/api/availability/slots", params={"date": "2026-04-15", "duration": 30}
    )
    assert explicit_response.json()["duration_minutes"] == 30


# --- Owner-timezone framing: rules evaluate in the clinician's own zone ---


def _wednesday_working_hours_rule() -> AvailabilityRule:
    return AvailabilityRule(
        id="rule-1",
        user_id="test-user-123",
        rule_type=RuleType.WORKING_HOURS,
        enforcement="hard",
        params={"day_of_week": 2, "start": "09:00", "end": "17:00"},  # 2026-08-26 is a Wednesday
    )


def test_free_slots_frames_working_hours_in_owner_timezone(
    client: TestClient,
    rule_repo: InMemoryAvailabilityRuleRepository,
    mock_user_repo: InMemoryUserRepository,
) -> None:
    """The route, not the engine default, sets the frame: a 9-5 rule for a
    New York clinician opens at 13:00Z, but the same rule for a UTC
    clinician opens at 09:00Z."""
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    rule_repo.create(_wednesday_working_hours_rule())
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="America/New_York"))

    response = client.get("/api/availability/slots", params={"date": "2026-08-26", "duration": 50})
    assert response.status_code == 200, response.text
    assert response.json()["slots"][0]["start"] == "2026-08-26T13:00:00Z"

    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="UTC"))

    utc_response = client.get(
        "/api/availability/slots", params={"date": "2026-08-26", "duration": 50}
    )
    assert utc_response.status_code == 200, utc_response.text
    assert utc_response.json()["slots"][0]["start"] == "2026-08-26T09:00:00Z"


def test_free_slots_invalid_timezone_preference_falls_back_to_utc(
    client: TestClient,
    rule_repo: InMemoryAvailabilityRuleRepository,
    mock_user_repo: InMemoryUserRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A preference string ZoneInfo rejects must not 4xx/5xx the request —
    it falls back to UTC framing, with exactly one warning logged that
    never echoes the raw (user-controlled) preference string."""
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    rule_repo.create(_wednesday_working_hours_rule())
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="Not/AZone"))

    with caplog.at_level(logging.WARNING):
        response = client.get(
            "/api/availability/slots", params={"date": "2026-08-26", "duration": 50}
        )

    assert response.status_code == 200, response.text
    assert response.json()["slots"][0]["start"] == "2026-08-26T09:00:00Z"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Not/AZone" not in warnings[0].getMessage()


def test_create_appointment_succeeds_with_no_availability_rules(client: TestClient) -> None:
    """Booking isn't gated on availability configuration — a practice that
    hasn't set up any rules yet can still be scheduled into."""
    app.dependency_overrides[get_availability_rule_repository] = InMemoryAvailabilityRuleRepository
    scheduling_svc = MagicMock()
    scheduling_svc.create_appointment.return_value = _real_appointment()
    app.dependency_overrides[get_scheduling_service] = lambda: scheduling_svc

    response = client.post(
        "/api/appointments",
        json={
            "patient_id": "patient-1",
            "title": "Weekly check-in",
            "start_at": "2026-04-15T14:00:00Z",
            "end_at": "2026-04-15T14:50:00Z",
            "duration_minutes": 50,
        },
    )

    assert response.status_code == 201, response.text


# --- Write-path characterization: real SchedulingService, in-memory repos ---
#
# The tests below run the real SchedulingService instead of a MagicMock so
# they exercise its actual validation. That service (see
# app/scheduling_engine/services/scheduling.py) rejects a colliding time via
# AppointmentRepository.list_overlapping and refuses hard-enforcement
# availability-rule conflicts via AvailabilityEngine.check_conflicts, but
# still doesn't check that end_at is after start_at. The
# "test_create_appointment_accepts_end_before_start" test below pins that
# remaining gap down as it exists today so a follow-up has a baseline to flip.


def test_create_appointment_happy_path(write_client: TestClient) -> None:
    """A well-formed request creates and returns the appointment."""
    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["patient_id"] == "patient-1"
    assert body["duration_minutes"] == 50
    assert body["status"] == "confirmed"


def test_create_appointment_rejects_blocked_day_rule(write_client: TestClient) -> None:
    """A hard-enforcement rule blocking this day of week refuses the booking
    with 422 (not the 409 collision status), naming the violated rule."""
    rule_response = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "block_day_of_week",
            "enforcement": "hard",
            "params": {"day_of_week": 2},  # 2026-04-15 is a Wednesday
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 422, response.text
    assert "blocked" in response.json()["error"]["message"].lower()


def test_create_appointment_returns_soft_rule_warnings(write_client: TestClient) -> None:
    """A soft-enforcement rule violation doesn't block the booking, but its
    warning message rides along on the created appointment rather than
    silently vanishing."""
    rule_response = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "block_day_of_week",
            "enforcement": "soft",
            "params": {"day_of_week": 2},  # 2026-04-15 is a Wednesday
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["warnings"]) == 1
    assert "blocked" in body["warnings"][0].lower()


def test_create_appointment_clean_slate_has_no_warnings(write_client: TestClient) -> None:
    """A booking that violates nothing still succeeds, with no warnings."""
    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    assert response.json()["warnings"] == []


def test_create_appointment_malformed_rule_does_not_500(write_client: TestClient) -> None:
    """A rule with params missing an expected key must not 500 the booking
    path — it's treated as non-blocking rather than crashing the check."""
    rule_response = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "working_hours",
            "enforcement": "hard",
            "params": {"day_of_week": 2},  # missing "start"/"end"
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text


def test_update_appointment_rejects_blocked_day_rule_on_reschedule(
    write_client: TestClient,
) -> None:
    """PATCH is gated the same as create when a time field moves onto a
    hard-blocked day."""
    created = write_client.post("/api/appointments", json=_create_payload())
    assert created.status_code == 201, created.text
    appt_id = created.json()["id"]

    rule_response = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "block_day_of_week",
            "enforcement": "hard",
            "params": {"day_of_week": 3},  # 2026-04-16 is a Thursday
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.patch(
        f"/api/appointments/{appt_id}",
        json={"start_at": "2026-04-16T14:00:00Z", "end_at": "2026-04-16T14:50:00Z"},
    )

    assert response.status_code == 422, response.text


def test_update_appointment_without_time_change_ignores_blocked_day_rule(
    write_client: TestClient,
) -> None:
    """Updating a non-time field never re-triggers rule enforcement — this is
    the same guard start-session relies on to link session_id unaffected."""
    created = write_client.post("/api/appointments", json=_create_payload())
    assert created.status_code == 201, created.text
    appt_id = created.json()["id"]

    rule_response = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "block_day_of_week",
            "enforcement": "hard",
            "params": {"day_of_week": 2},  # 2026-04-15 is a Wednesday — the existing slot
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.patch(f"/api/appointments/{appt_id}", json={"title": "Renamed"})

    assert response.status_code == 200, response.text


def test_create_appointment_evaluates_rules_in_owner_timezone(
    write_client: TestClient,
    mock_user_repo: InMemoryUserRepository,
) -> None:
    """A hard working-hours rule reads its 9-5 boundary off the clinician's
    own timezone preference, not the raw UTC instant on the wire: 15:00 EDT
    is inside it, 08:00 EDT is not, even though both are afternoon/morning
    UTC instants that don't obviously look like 9-5."""
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="America/New_York"))
    rule_response = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "working_hours",
            "enforcement": "hard",
            "params": {"day_of_week": 2, "start": "09:00", "end": "17:00"},
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    within_hours = write_client.post(
        "/api/appointments",
        json=_create_payload(start_at="2026-08-26T19:00:00Z", end_at="2026-08-26T19:50:00Z"),
    )
    assert within_hours.status_code == 201, within_hours.text

    outside_hours = write_client.post(
        "/api/appointments",
        json=_create_payload(
            patient_id="patient-2",
            start_at="2026-08-26T12:00:00Z",
            end_at="2026-08-26T12:50:00Z",
        ),
    )
    assert outside_hours.status_code == 422, outside_hours.text
    assert "working hours" in outside_hours.json()["error"]["message"].lower()


def test_update_appointment_reschedule_evaluates_rules_in_owner_timezone(
    write_client: TestClient,
    mock_user_repo: InMemoryUserRepository,
) -> None:
    """PATCH reschedule is gated by the same owner-timezone frame as create."""
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="America/New_York"))
    created = write_client.post(
        "/api/appointments",
        json=_create_payload(start_at="2026-08-26T19:00:00Z", end_at="2026-08-26T19:50:00Z"),
    )
    assert created.status_code == 201, created.text
    appt_id = created.json()["id"]

    rule_response = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "working_hours",
            "enforcement": "hard",
            "params": {"day_of_week": 2, "start": "09:00", "end": "17:00"},
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.patch(
        f"/api/appointments/{appt_id}",
        json={"start_at": "2026-08-26T12:00:00Z", "end_at": "2026-08-26T12:50:00Z"},
    )

    assert response.status_code == 422, response.text
    assert "working hours" in response.json()["error"]["message"].lower()


def test_create_appointment_rejects_overlap(write_client: TestClient) -> None:
    """A second appointment on the same clinician's calendar at an
    overlapping time is rejected with 409, not double-booked."""
    first = write_client.post("/api/appointments", json=_create_payload())
    assert first.status_code == 201, first.text

    second = write_client.post("/api/appointments", json=_create_payload(patient_id="patient-2"))

    assert second.status_code == 409, second.text


def test_create_appointment_back_to_back_is_accepted(write_client: TestClient) -> None:
    """An appointment starting exactly when another ends is not a collision —
    half-open intervals mean back-to-back bookings stay legal."""
    first = write_client.post("/api/appointments", json=_create_payload())
    assert first.status_code == 201, first.text

    second = write_client.post(
        "/api/appointments",
        json=_create_payload(
            patient_id="patient-2",
            start_at="2026-04-15T14:50:00Z",
            end_at="2026-04-15T15:40:00Z",
        ),
    )

    assert second.status_code == 201, second.text


def test_create_appointment_rebooks_cancelled_slot(write_client: TestClient) -> None:
    """A cancelled appointment does not block rebooking its slot."""
    first = write_client.post("/api/appointments", json=_create_payload())
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    cancel_response = write_client.delete(f"/api/appointments/{first_id}")
    assert cancel_response.status_code == 200, cancel_response.text

    second = write_client.post("/api/appointments", json=_create_payload(patient_id="patient-2"))

    assert second.status_code == 201, second.text


def test_create_appointment_accepts_end_before_start(write_client: TestClient) -> None:
    """KNOWN HOLE: create_appointment never checks that end_at is after
    start_at, so an inverted range is persisted as-is. Follow-up: reject
    end_at <= start_at with a 400 (InvalidAppointmentError).
    """
    response = write_client.post(
        "/api/appointments",
        json=_create_payload(
            start_at="2026-04-15T14:00:00Z",
            end_at="2026-04-15T13:00:00Z",
        ),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["end_at"] < body["start_at"]


def test_update_appointment_rejects_overlap(write_client: TestClient) -> None:
    """Rescheduling one appointment on top of a different appointment is
    rejected with 409 instead of silently double-booking the slot."""
    first = write_client.post("/api/appointments", json=_create_payload())
    assert first.status_code == 201, first.text

    second = write_client.post(
        "/api/appointments",
        json=_create_payload(
            patient_id="patient-2",
            start_at="2026-04-15T16:00:00Z",
            end_at="2026-04-15T16:50:00Z",
        ),
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]

    response = write_client.patch(
        f"/api/appointments/{second_id}",
        json={"start_at": "2026-04-15T14:00:00Z", "end_at": "2026-04-15T14:50:00Z"},
    )

    assert response.status_code == 409, response.text


def test_update_appointment_moving_onto_itself_succeeds(write_client: TestClient) -> None:
    """Moving an appointment a few minutes later only overlaps its own prior
    slot, which must not count as a collision against itself."""
    created = write_client.post("/api/appointments", json=_create_payload())
    assert created.status_code == 201, created.text
    appt_id = created.json()["id"]

    response = write_client.patch(
        f"/api/appointments/{appt_id}",
        json={"start_at": "2026-04-15T14:10:00Z", "end_at": "2026-04-15T15:00:00Z"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start_at"] == "2026-04-15T14:10:00Z"


# --- Visit billing codes ---


def test_create_appointment_leaves_billing_codes_unset(write_client: TestClient) -> None:
    """Booking a visit never infers or defaults a billing code."""
    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["service_code"] is None
    assert body["modifiers"] is None
    assert body["unit_count"] is None
    assert body["place_of_service"] is None
    assert body["diagnosis_codes"] is None


def test_patch_appointment_round_trips_billing_codes(write_client: TestClient) -> None:
    """Every visit-coding field survives a PATCH + GET round trip, and the
    diagnosis list keeps the order it was given in (first = primary)."""
    created = write_client.post("/api/appointments", json=_create_payload())
    appt_id = created.json()["id"]

    response = write_client.patch(
        f"/api/appointments/{appt_id}",
        json={
            "service_code": "90837",
            "modifiers": ["95", "GT"],
            "unit_count": 1,
            "place_of_service": "02",
            "diagnosis_codes": ["F41.1", "F32.9"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["service_code"] == "90837"
    assert body["modifiers"] == ["95", "GT"]
    assert body["unit_count"] == 1
    assert body["place_of_service"] == "02"
    assert body["diagnosis_codes"] == ["F41.1", "F32.9"]

    fetched = write_client.get(f"/api/appointments/{appt_id}")
    assert fetched.json()["diagnosis_codes"] == ["F41.1", "F32.9"]


def test_patch_appointment_rejects_unknown_icd10_code(write_client: TestClient) -> None:
    """A diagnosis code absent from the bundled ICD-10-CM catalog is
    rejected with a message naming the offending code."""
    created = write_client.post("/api/appointments", json=_create_payload())
    appt_id = created.json()["id"]

    response = write_client.patch(
        f"/api/appointments/{appt_id}",
        json={"diagnosis_codes": ["F41.1", "Z99.NOPE"]},
    )

    assert response.status_code == 422, response.text
    assert "Z99.NOPE" in response.text


def test_patch_appointment_rejects_unknown_place_of_service(write_client: TestClient) -> None:
    """Place of service is a closed enum — an unrecognized value never reaches storage."""
    created = write_client.post("/api/appointments", json=_create_payload())
    appt_id = created.json()["id"]

    response = write_client.patch(
        f"/api/appointments/{appt_id}",
        json={"place_of_service": "99"},
    )

    assert response.status_code == 422, response.text

    fetched = write_client.get(f"/api/appointments/{appt_id}")
    assert fetched.json()["place_of_service"] is None


def test_patch_appointment_rejects_more_than_four_modifiers(write_client: TestClient) -> None:
    """A visit may carry at most four modifiers."""
    created = write_client.post("/api/appointments", json=_create_payload())
    appt_id = created.json()["id"]

    response = write_client.patch(
        f"/api/appointments/{appt_id}",
        json={"modifiers": ["95", "GT", "59", "XE", "XP"]},
    )

    assert response.status_code == 422, response.text


def test_create_recurring_series_creates_all_occurrences(write_client: TestClient) -> None:
    """A recurring request fans out into one appointment per occurrence,
    sharing a recurring_appointment_id."""
    response = write_client.post(
        "/api/appointments/recurring",
        json={
            "patient_id": "patient-1",
            "title": "Weekly check-in",
            "start_at": "2026-04-15T14:00:00Z",
            "end_at": "2026-04-15T14:50:00Z",
            "duration_minutes": 50,
            "frequency": "weekly",
            "timezone": "UTC",
            "count": 4,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total"] == 4
    occurrences = body["data"]
    assert len(occurrences) == 4
    assert len({occ["id"] for occ in occurrences}) == 4
    master_id = occurrences[0]["id"]
    assert all(occ["recurring_appointment_id"] == master_id for occ in occurrences)
    assert [occ["recurrence_index"] for occ in occurrences] == [0, 1, 2, 3]
    starts = [occ["start_at"] for occ in occurrences]
    assert starts == sorted(starts)


def test_create_recurring_series_rejects_colliding_occurrence(
    write_client: TestClient,
    appt_repo: InMemoryAppointmentRepository,
) -> None:
    """One colliding occurrence fails the whole series rather than creating
    the non-colliding occurrences and skipping the bad one — a partially
    booked series is harder to reason about than a rejected request."""
    blocker = write_client.post(
        "/api/appointments",
        json=_create_payload(
            patient_id="patient-2",
            start_at="2026-04-29T14:00:00Z",
            end_at="2026-04-29T14:50:00Z",
        ),
    )
    assert blocker.status_code == 201, blocker.text

    response = write_client.post(
        "/api/appointments/recurring",
        json={
            "patient_id": "patient-1",
            "title": "Weekly check-in",
            "start_at": "2026-04-15T14:00:00Z",
            "end_at": "2026-04-15T14:50:00Z",
            "duration_minutes": 50,
            "frequency": "weekly",
            "timezone": "UTC",
            "count": 4,
        },
    )

    assert response.status_code == 409, response.text
    remaining = appt_repo.list_by_range(
        "test-user-123", "2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z"
    )
    assert len(remaining) == 1, "no occurrence from the rejected series should persist"


def _wednesday_working_hours_rule_request() -> dict[str, Any]:
    return {
        "rule_type": "working_hours",
        "enforcement": "hard",
        "params": {"day_of_week": 2, "start": "09:00", "end": "17:00"},
    }


def _weekly_series_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "patient_id": "patient-1",
        "title": "Weekly check-in",
        "start_at": "2026-08-26T19:00:00Z",
        "end_at": "2026-08-26T19:50:00Z",
        "duration_minutes": 50,
        "frequency": "weekly",
        "timezone": "America/New_York",
        "count": 4,
    }
    payload.update(overrides)
    return payload


def test_create_recurring_series_within_hours_in_owner_timezone_succeeds(
    write_client: TestClient,
    mock_user_repo: InMemoryUserRepository,
) -> None:
    """A weekly Wednesday 3pm EDT series clears a 9-5 rule read in the
    owner's own timezone, even though 3pm EDT is late afternoon UTC."""
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="America/New_York"))
    rule_response = write_client.post(
        "/api/availability/rules", json=_wednesday_working_hours_rule_request()
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.post("/api/appointments/recurring", json=_weekly_series_payload())

    assert response.status_code == 201, response.text
    assert response.json()["total"] == 4


def test_create_recurring_series_outside_hours_in_owner_timezone_refused(
    write_client: TestClient,
    mock_user_repo: InMemoryUserRepository,
) -> None:
    """The same series at 8am EDT is refused entirely — every occurrence is
    checked against the owner-timezone frame, not just the first."""
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="America/New_York"))
    rule_response = write_client.post(
        "/api/availability/rules", json=_wednesday_working_hours_rule_request()
    )
    assert rule_response.status_code == 201, rule_response.text

    response = write_client.post(
        "/api/appointments/recurring",
        json=_weekly_series_payload(start_at="2026-08-26T12:00:00Z", end_at="2026-08-26T12:50:00Z"),
    )

    assert response.status_code == 422, response.text
    assert "working hours" in response.json()["error"]["message"].lower()


# --- Google Calendar push-on-write ---
#
# write_client wires the real SchedulingService over an in-memory repo, so
# these exercise the actual persistence of google_event_id/google_sync_status,
# not a canned mock return.


def test_create_appointment_pushes_to_connected_google_calendar(
    write_client: TestClient,
) -> None:
    """A connected user's new appointment is pushed to Google and the
    returned event id + synced status are persisted."""
    gcal_service = MagicMock()
    gcal_service.push_appointment.return_value = "gcal-evt-1"
    app.dependency_overrides[get_google_calendar_service] = lambda: gcal_service

    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["google_event_id"] == "gcal-evt-1"
    assert body["google_sync_status"] == "synced"
    gcal_service.push_appointment.assert_called_once()


def test_update_appointment_pushes_stored_event_id(write_client: TestClient) -> None:
    """Updating an already-synced appointment pushes the update to Google
    carrying the previously stored event id (an update, not a create)."""
    gcal_service = MagicMock()
    gcal_service.push_appointment.return_value = "gcal-evt-1"
    app.dependency_overrides[get_google_calendar_service] = lambda: gcal_service

    created = write_client.post("/api/appointments", json=_create_payload())
    assert created.status_code == 201, created.text
    appt_id = created.json()["id"]
    assert created.json()["google_event_id"] == "gcal-evt-1"

    gcal_service.push_appointment.reset_mock()
    gcal_service.push_appointment.return_value = "gcal-evt-1"

    response = write_client.patch(f"/api/appointments/{appt_id}", json={"title": "Rescheduled"})

    assert response.status_code == 200, response.text
    gcal_service.push_appointment.assert_called_once()
    pushed_appt = gcal_service.push_appointment.call_args[0][1]
    assert pushed_appt.google_event_id == "gcal-evt-1"


def test_cancel_appointment_deletes_google_event(write_client: TestClient) -> None:
    """Cancelling an appointment with a linked Google event deletes it."""
    gcal_service = MagicMock()
    gcal_service.push_appointment.return_value = "gcal-evt-1"
    app.dependency_overrides[get_google_calendar_service] = lambda: gcal_service

    created = write_client.post("/api/appointments", json=_create_payload())
    assert created.status_code == 201, created.text
    appt_id = created.json()["id"]

    response = write_client.delete(f"/api/appointments/{appt_id}")

    assert response.status_code == 200, response.text
    gcal_service.delete_event.assert_called_once_with("test-user-123", "gcal-evt-1")


def test_create_appointment_google_failure_still_succeeds(write_client: TestClient) -> None:
    """A Google Calendar failure never fails the appointment write — the
    appointment is created with google_sync_status='error' instead."""
    gcal_service = MagicMock()
    gcal_service.push_appointment.side_effect = Exception("Google API down")
    app.dependency_overrides[get_google_calendar_service] = lambda: gcal_service

    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["google_sync_status"] == "error"


def test_create_appointment_not_connected_leaves_status_null(
    write_client: TestClient,
) -> None:
    """A user with no Google Calendar connection gets no event and no
    error — absence of sync isn't an error."""
    gcal_service = MagicMock()
    gcal_service.push_appointment.return_value = None
    app.dependency_overrides[get_google_calendar_service] = lambda: gcal_service

    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["google_event_id"] is None
    assert body["google_sync_status"] is None


# --- Natural-language availability rule parse ---


def _wire_parse_service(response: dict[str, Any]) -> None:
    gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data=response))
    app.dependency_overrides[get_availability_rule_parse_service] = lambda: (
        AvailabilityRuleParseService(llm_gateway=gateway)
    )


def test_parse_availability_rules_returns_proposals_creates_nothing(
    write_client: TestClient, rule_repo: InMemoryAvailabilityRuleRepository
) -> None:
    """The parse endpoint returns proposals but never writes a rule — rule
    creation still only happens through POST /api/availability/rules."""
    _wire_parse_service(
        {
            "proposals": [
                {
                    "rule_type": "block_day_of_week",
                    "enforcement": "hard",
                    "day_of_week": 4,
                    "human_summary": "No Fridays.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
    )

    response = write_client.post(
        "/api/availability/rules/parse", json={"text": "No appointments on Fridays"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["rule_type"] == "block_day_of_week"
    assert body["proposals"][0]["params"] == {"day_of_week": 4}
    assert body["could_not_parse"] is None
    assert rule_repo.list_by_user("test-user-123") == []


def test_parse_availability_rules_rate_limited(write_client: TestClient) -> None:
    """A limiter whose check() raises surfaces as 429 — proving the route is
    rate-limit-gated."""

    def raise_429(_key: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    with patch("app.routes.scheduling.get_availability_parse_limiter") as mock_limiter:
        mock_limiter.return_value.check.side_effect = raise_429

        response = write_client.post(
            "/api/availability/rules/parse", json={"text": "No appointments on Fridays"}
        )

    assert response.status_code == 429


def test_parse_exclusive_with_no_existing_rules_has_no_conflicts(
    write_client: TestClient,
) -> None:
    """'I only meet on...' with no other working_hours rules present yields
    exclusive=true and an empty existing_conflicting_rules list."""
    _wire_parse_service(
        {
            "proposals": [
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 0,
                    "start": "13:00",
                    "end": "15:00",
                    "human_summary": "Mondays 1-3.",
                    "confidence": 0.95,
                },
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 1,
                    "start": "14:00",
                    "end": "16:00",
                    "human_summary": "Tuesdays 2-4.",
                    "confidence": 0.95,
                },
            ],
            "could_not_parse": None,
            "exclusive": True,
        }
    )

    response = write_client.post(
        "/api/availability/rules/parse",
        json={"text": "I only meet on Mondays from 1-3 and Tuesdays 2-4"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["proposals"]) == 2
    assert body["exclusive"] is True
    assert body["existing_conflicting_rules"] == []


def test_parse_exclusive_surfaces_existing_conflicting_rule(
    write_client: TestClient, rule_repo: InMemoryAvailabilityRuleRepository
) -> None:
    """A pre-existing Wednesday working_hours rule shows up in
    existing_conflicting_rules when the parse is exclusive and doesn't
    mention Wednesday — and is never deleted or modified."""
    existing = write_client.post(
        "/api/availability/rules",
        json={
            "rule_type": "working_hours",
            "enforcement": "hard",
            "params": {"day_of_week": 2, "start": "10:00", "end": "12:00"},
        },
    )
    assert existing.status_code == 201, existing.text

    _wire_parse_service(
        {
            "proposals": [
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 0,
                    "start": "13:00",
                    "end": "15:00",
                    "human_summary": "Mondays 1-3.",
                    "confidence": 0.95,
                },
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 1,
                    "start": "14:00",
                    "end": "16:00",
                    "human_summary": "Tuesdays 2-4.",
                    "confidence": 0.95,
                },
            ],
            "could_not_parse": None,
            "exclusive": True,
        }
    )

    response = write_client.post(
        "/api/availability/rules/parse",
        json={"text": "I only meet on Mondays from 1-3 and Tuesdays 2-4"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["proposals"]) == 2
    assert body["exclusive"] is True
    assert len(body["existing_conflicting_rules"]) == 1
    assert body["existing_conflicting_rules"][0]["params"]["day_of_week"] == 2

    # No deletion/modification happened, and the two proposals weren't
    # created either — only the one explicit create call above landed.
    all_rules = rule_repo.list_by_user("test-user-123")
    assert len(all_rules) == 1
    assert all_rules[0].params["day_of_week"] == 2


def _date_intent_proposal(items: list[dict[str, Any]], *, range_: bool = False) -> dict[str, Any]:
    return {
        "rule_type": "block_date_range" if range_ else "block_specific_dates",
        "enforcement": "hard",
        "date_intent": {"items": items, "range": range_},
        "human_summary": "Blocked.",
        "confidence": 0.95,
    }


def test_parse_resolves_next_friday_using_owner_timezone_auckland(
    write_client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    """13:00 UTC is already Thursday evening in Auckland (UTC+12 in August),
    one calendar day ahead of the UTC date -- the reference date "next
    Friday" resolves against must come from the owner's own timezone."""
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="Pacific/Auckland"))
    _wire_parse_service(
        {
            "proposals": [_date_intent_proposal([{"day_of_week": 4, "modifier": "next"}])],
            "could_not_parse": None,
            "exclusive": False,
        }
    )

    with patch(
        "app.routes.scheduling._now",
        side_effect=lambda tz: datetime(2026, 8, 26, 13, 0, tzinfo=UTC).astimezone(tz),
    ):
        response = write_client.post(
            "/api/availability/rules/parse", json={"text": "Block next Friday"}
        )

    assert response.status_code == 200, response.text
    assert response.json()["proposals"][0]["params"] == {"dates": ["2026-09-04"]}


def test_parse_resolves_dates_using_owner_timezone_los_angeles(
    write_client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    """The same instant read from America/Los_Angeles (UTC-7 in August) is
    still 2026-08-26 -- "next Friday" and a bare "Thursday" resolve
    against that Wednesday reference."""
    mock_user_repo.save_preferences(
        "test-user-123", UserPreferences(timezone="America/Los_Angeles")
    )
    _wire_parse_service(
        {
            "proposals": [
                _date_intent_proposal([{"day_of_week": 4, "modifier": "next"}, {"day_of_week": 3}])
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
    )

    with patch(
        "app.routes.scheduling._now",
        side_effect=lambda tz: datetime(2026, 8, 26, 13, 0, tzinfo=UTC).astimezone(tz),
    ):
        response = write_client.post(
            "/api/availability/rules/parse",
            json={"text": "Block next Friday and this Thursday"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["proposals"][0]["params"] == {"dates": ["2026-09-04", "2026-08-27"]}


def test_parse_invalid_timezone_preference_falls_back_to_utc_without_500(
    write_client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="Not/AZone"))
    _wire_parse_service(
        {
            "proposals": [
                {
                    "rule_type": "block_day_of_week",
                    "enforcement": "hard",
                    "day_of_week": 4,
                    "human_summary": "No Fridays.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
    )

    response = write_client.post(
        "/api/availability/rules/parse", json={"text": "No appointments on Fridays"}
    )

    assert response.status_code == 200, response.text


def test_parse_reads_preferences_before_releasing_db_connection(
    write_client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    """The timezone preference read that determines the reference date
    must happen before the request-scoped DB connection is released for
    the LLM round trip, not after."""
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="UTC"))
    _wire_parse_service(
        {
            "proposals": [
                {
                    "rule_type": "block_day_of_week",
                    "enforcement": "hard",
                    "day_of_week": 4,
                    "human_summary": "No Fridays.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
    )

    events: list[str] = []
    original_get_preferences = mock_user_repo.get_preferences

    def spy_get_preferences(user_id: str) -> UserPreferences:
        events.append("get_preferences")
        return original_get_preferences(user_id)

    def spy_release() -> None:
        events.append("release_db_connection")

    with (
        patch.object(mock_user_repo, "get_preferences", side_effect=spy_get_preferences),
        patch("app.routes.scheduling.release_db_connection", side_effect=spy_release),
    ):
        response = write_client.post(
            "/api/availability/rules/parse", json={"text": "No appointments on Fridays"}
        )

    assert response.status_code == 200, response.text
    assert events == ["get_preferences", "release_db_connection"]


# --- Google Calendar connect: one grant per capability ---
#
# The wizard asks Google for exactly what the therapist selected. These
# assert the route turns that selection into a capability request; the
# capability -> scope mapping itself is covered in the provider seam tests.

_GCAL_REDIRECT = "http://localhost:3000/dashboard/settings/calendar"


def _capture_gcal_service() -> MagicMock:
    gcal_service = MagicMock()
    gcal_service.get_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth?x=1"
    app.dependency_overrides[get_google_calendar_service] = lambda: gcal_service
    return gcal_service


def test_connect_defaults_to_the_calendar_pablo_makes(client: TestClient) -> None:
    """The recommended choice writes only to a calendar Pablo owns."""
    gcal_service = _capture_gcal_service()

    response = client.get("/api/google-calendar/authorize", params={"redirect_uri": _GCAL_REDIRECT})

    assert response.status_code == 200, response.text
    kwargs = gcal_service.get_auth_url.call_args.kwargs
    assert kwargs["write_target"] is CalendarWriteTarget.APP_CALENDAR
    assert kwargs["capabilities"] == {CalendarCapability.PUSH, CalendarCapability.BUSY}


def test_connect_can_write_to_the_therapists_own_calendar(client: TestClient) -> None:
    gcal_service = _capture_gcal_service()

    response = client.get(
        "/api/google-calendar/authorize",
        params={"redirect_uri": _GCAL_REDIRECT, "write_target": "primary"},
    )

    assert response.status_code == 200, response.text
    assert gcal_service.get_auth_url.call_args.kwargs["write_target"] is CalendarWriteTarget.PRIMARY


def test_declining_busy_times_does_not_ask_for_them(client: TestClient) -> None:
    gcal_service = _capture_gcal_service()

    response = client.get(
        "/api/google-calendar/authorize",
        params={"redirect_uri": _GCAL_REDIRECT, "busy": "false"},
    )

    assert response.status_code == 200, response.text
    assert gcal_service.get_auth_url.call_args.kwargs["capabilities"] == {CalendarCapability.PUSH}


@pytest.mark.parametrize("write_target", ["app_calendar", "primary"])
@pytest.mark.parametrize("busy", ["true", "false"])
def test_connecting_never_asks_to_read_event_content(
    client: TestClient,
    write_target: str,
    busy: str,
) -> None:
    """Reading events belongs to importing a practice, which asks for it
    then. No combination of connect choices requests it."""
    gcal_service = _capture_gcal_service()

    client.get(
        "/api/google-calendar/authorize",
        params={"redirect_uri": _GCAL_REDIRECT, "write_target": write_target, "busy": busy},
    )

    capabilities = gcal_service.get_auth_url.call_args.kwargs["capabilities"]
    assert CalendarCapability.IMPORT not in capabilities


def test_unknown_write_target_is_rejected(client: TestClient) -> None:
    _capture_gcal_service()

    response = client.get(
        "/api/google-calendar/authorize",
        params={"redirect_uri": _GCAL_REDIRECT, "write_target": "somebody-elses"},
    )

    assert response.status_code == 400, response.text


def test_callback_binds_the_connection_to_the_chosen_calendar(client: TestClient) -> None:
    gcal_service = _capture_gcal_service()

    response = client.get(
        "/api/google-calendar/callback",
        params={
            "code": "auth-code",
            "redirect_uri": _GCAL_REDIRECT,
            "state": "signed-state",
            "write_target": "primary",
        },
    )

    assert response.status_code == 200, response.text
    kwargs = gcal_service.handle_callback.call_args.kwargs
    assert kwargs["state"] == "signed-state"
    assert kwargs["write_target"] is CalendarWriteTarget.PRIMARY
    assert CalendarCapability.IMPORT not in kwargs["capabilities"]


def test_an_incremental_capability_grant_asks_for_only_that_capability(
    client: TestClient,
) -> None:
    """The import wizard's "Look at my week" round trip lands here — it
    must ask for import alone, never the connect-time set."""
    gcal_service = _capture_gcal_service()
    gcal_service.get_sync_status.return_value = {"write_target": "primary"}

    response = client.get(
        "/api/google-calendar/callback",
        params={
            "code": "auth-code",
            "redirect_uri": _GCAL_REDIRECT,
            "state": "signed-state",
            "capability": "import",
        },
    )

    assert response.status_code == 200, response.text
    kwargs = gcal_service.handle_callback.call_args.kwargs
    assert list(kwargs["capabilities"]) == [CalendarCapability.IMPORT]


def test_an_incremental_grant_binds_to_the_existing_calendar_not_the_default(
    client: TestClient,
) -> None:
    """An incremental grant must never silently rebind PUSH to a different
    calendar mid-flow — the write target comes from the live connection,
    not the endpoint's connect-time default."""
    gcal_service = _capture_gcal_service()
    gcal_service.get_sync_status.return_value = {"write_target": "primary"}

    response = client.get(
        "/api/google-calendar/callback",
        params={
            "code": "auth-code",
            "redirect_uri": _GCAL_REDIRECT,
            "state": "signed-state",
            "capability": "import",
            # A default-shaped write_target arrives on the URL too (the
            # frontend doesn't know better on this round trip) — it must
            # be ignored in favor of what's actually connected.
            "write_target": "app_calendar",
        },
    )

    assert response.status_code == 200, response.text
    assert (
        gcal_service.handle_callback.call_args.kwargs["write_target"] is CalendarWriteTarget.PRIMARY
    )


def test_an_unsupported_incremental_capability_is_rejected(client: TestClient) -> None:
    gcal_service = _capture_gcal_service()

    response = client.get(
        "/api/google-calendar/callback",
        params={
            "code": "auth-code",
            "redirect_uri": _GCAL_REDIRECT,
            "state": "signed-state",
            "capability": "not-a-real-capability",
        },
    )

    assert response.status_code == 400, response.text
    gcal_service.handle_callback.assert_not_called()


def test_callback_without_state_is_refused(client: TestClient) -> None:
    """A code arriving with no state is never exchanged."""
    gcal_service = _capture_gcal_service()

    response = client.get(
        "/api/google-calendar/callback",
        params={"code": "auth-code", "redirect_uri": _GCAL_REDIRECT},
    )

    assert response.status_code == 400, response.text
    gcal_service.handle_callback.assert_not_called()


def test_callback_with_an_unusable_state_is_refused(client: TestClient) -> None:
    gcal_service = _capture_gcal_service()
    gcal_service.handle_callback.side_effect = OAuthStateError("state signature does not verify")

    response = client.get(
        "/api/google-calendar/callback",
        params={"code": "auth-code", "redirect_uri": _GCAL_REDIRECT, "state": "forged"},
    )

    assert response.status_code == 400, response.text


def test_consent_options_carry_each_choices_promise(client: TestClient) -> None:
    """The wizard renders its guarantees from these, so they must come from
    the provider's declarations and must not leak scope names into copy."""
    app.dependency_overrides[get_google_calendar_service] = lambda: GoogleCalendarService(
        MagicMock(),
        MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
    )

    response = client.get("/api/google-calendar/consent-options")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["default_write_target"] == "app_calendar"
    assert body["busy_default"] is True

    promises = {option["id"]: option["promise"] for option in body["write_targets"]}
    assert set(promises) == {"app_calendar", "primary"}
    # The calendar Pablo makes is unreachable by grant; the therapist's own
    # calendar is not, and its copy must not claim otherwise.
    assert "cannot reach further" in promises["app_calendar"]
    assert "cannot reach further" not in promises["primary"]
    assert "cannot reach further" in body["busy"]["promise"]

    assert "googleapis.com" not in response.text
    assert "calendar.readonly" not in response.text
