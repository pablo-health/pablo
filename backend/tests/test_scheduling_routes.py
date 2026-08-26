# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route-level tests for scheduling endpoints (FastAPI dependency wiring)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.models import Patient, SessionStatus
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
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services import get_audit_service
from app.services.availability_parse_service import AvailabilityRuleParseService
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion
from fastapi import HTTPException, status

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
    write path's actual validation (or lack of it) instead of asserting
    on canned mock returns.
    """
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo)
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
# app/scheduling_engine/services/scheduling.py) only checks patient_id,
# start_at/end_at presence, and duration_minutes — it never consults
# AvailabilityEngine or AppointmentRepository.list_overlapping. The three
# "ignores_*" tests below pin down that gap as it exists today so a follow-up
# that wires in real conflict checking has a baseline to flip.


def test_create_appointment_happy_path(write_client: TestClient) -> None:
    """A well-formed request creates and returns the appointment."""
    response = write_client.post("/api/appointments", json=_create_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["patient_id"] == "patient-1"
    assert body["duration_minutes"] == 50
    assert body["status"] == "confirmed"


def test_create_appointment_ignores_blocked_day_rule(write_client: TestClient) -> None:
    """KNOWN HOLE: create_appointment never calls AvailabilityEngine, so a
    hard-enforcement rule blocking this day of week has no effect on the
    write path. Follow-up: route creation through
    AvailabilityEngine.check_conflicts (or an equivalent check) and reject
    hard conflicts before persisting.
    """
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

    assert response.status_code == 201, response.text


def test_create_appointment_ignores_overlap(write_client: TestClient) -> None:
    """KNOWN HOLE: create_appointment never calls
    AppointmentRepository.list_overlapping, so a second appointment on the
    same calendar slot is accepted instead of rejected. Follow-up: check
    for overlap before persisting and surface a 409/400 on collision.
    """
    first = write_client.post("/api/appointments", json=_create_payload())
    assert first.status_code == 201, first.text

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


def test_update_appointment_ignores_overlap(write_client: TestClient) -> None:
    """KNOWN HOLE: update_appointment (like create_appointment) never checks
    AppointmentRepository.list_overlapping, so rescheduling one appointment
    on top of another is accepted instead of rejected. Follow-up: apply the
    same overlap check on the update path, excluding the appointment being
    moved.
    """
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

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start_at"] == "2026-04-15T14:00:00Z"


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
                },
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 1,
                    "start": "14:00",
                    "end": "16:00",
                    "human_summary": "Tuesdays 2-4.",
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
                },
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 1,
                    "start": "14:00",
                    "end": "16:00",
                    "human_summary": "Tuesdays 2-4.",
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
