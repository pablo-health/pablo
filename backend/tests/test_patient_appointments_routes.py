# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient's own-calendar route, exercised as a patient.

The principal is synthesized here rather than authenticated: resolving a
real one needs a credential front door that is a separate piece of work,
and the principal's own guarantees (schema arming, the RLS GUC, two-patient
isolation at the database layer) are proven in the integration suite. What
is under test here is what the ROUTE does with a principal once it has one
— which id it trusts, and which columns it lets out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.auth.patient_context import AuthStrength, PatientContext, get_patient_context
from app.main import app
from app.repositories import get_appointment_repository
from app.repositories.audit import InMemoryAuditRepository
from app.route_introspection import iter_api_routes
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.audit_service import AuditService, get_audit_service
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.models.audit import AuditLogEntry

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_CLINICIAN = "33333333-3333-4333-8333-333333333333"


def _audit_entries(repo: InMemoryAuditRepository, patient_id: str) -> list[AuditLogEntry]:
    """What the audit actually recorded, read back the way a reader would.

    ``log_patient_principal_action`` writes the acting patient into
    ``user_id``, so the actor query finds a patient's own actions.
    """
    return sorted(repo.list_for_user(patient_id), key=lambda e: e.resource_id or "")


def _appointment(appt_id: str, patient_id: str, *, days: int) -> Appointment:
    start = datetime.now(UTC) + timedelta(days=days)
    return Appointment(
        id=appt_id,
        user_id=_CLINICIAN,
        patient_id=patient_id,
        title="Session",
        start_at=start,
        end_at=start + timedelta(minutes=50),
        duration_minutes=50,
        status="scheduled",
        session_type="therapy",
        video_link="https://example.test/room",
        notes="clinician's private note",
        recurrence_rule="weekly",
        recurring_appointment_id="series-1",
    )


@pytest.fixture
def repo() -> InMemoryAppointmentRepository:
    r = InMemoryAppointmentRepository()
    r.create(_appointment("appt-a1", _PATIENT_A, days=3))
    r.create(_appointment("appt-a2", _PATIENT_A, days=10))
    r.create(_appointment("appt-b1", _PATIENT_B, days=4))
    return r


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    """The real AuditService is used, with its storage swapped.

    A hand-written double would have to imitate what the service records —
    and would then pass whether or not the service actually records it. This
    way ``actor_type`` and the rest are the values production writes.
    """
    return InMemoryAuditRepository()


@pytest.fixture
def client(repo: InMemoryAppointmentRepository, audit_repo: InMemoryAuditRepository):
    def _as_patient_a() -> PatientContext:
        return PatientContext(
            patient_id=_PATIENT_A,
            practice_schema="practice_default",
            credential_kind="test",
            auth_strength=AuthStrength.STEPPED_UP,
        )

    app.dependency_overrides[get_patient_context] = _as_patient_a
    app.dependency_overrides[get_appointment_repository] = lambda: repo
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestOwnAppointmentsOnly:
    def test_returns_the_callers_own_appointments(self, client) -> None:
        """Visibility control — without it, the exclusion below proves nothing."""
        response = client.get("/api/patient/appointments")
        assert response.status_code == 200

        ids = [row["id"] for row in response.json()["data"]]
        assert ids == ["appt-a1", "appt-a2"]

    def test_another_patients_appointment_is_absent(self, client) -> None:
        response = client.get("/api/patient/appointments")
        body = response.text
        assert "appt-b1" not in body
        assert _PATIENT_B not in body

    def test_results_are_soonest_first(self, client) -> None:
        rows = client.get("/api/patient/appointments").json()["data"]
        starts = [r["start_at"] for r in rows]
        assert starts == sorted(starts)
        assert rows[0]["id"] == "appt-a1"

    def test_total_matches_the_rows_returned(self, client) -> None:
        body = client.get("/api/patient/appointments").json()
        assert body["total"] == len(body["data"]) == 2


class TestTheResponseWithholdsStaffColumns:
    """The allow-list is the control, so it is asserted on the wire.

    Row-level security cannot restrict columns, so a serializer that
    widened by default would leak clinician notes and visit coding to the
    patient with nothing else to catch it.
    """

    def test_clinician_and_billing_fields_never_appear(self, client) -> None:
        row = client.get("/api/patient/appointments").json()["data"][0]

        for withheld in (
            "user_id",
            "patient_id",
            "notes",
            "note_type",
            "session_id",
            "appointment_type_id",
            "service_code",
            "modifiers",
            "unit_count",
            "place_of_service",
            "diagnosis_codes",
            "confirmation_token_hash",
            "google_event_id",
            "google_calendar_id",
            "google_sync_status",
            "ical_uid",
            "ical_source",
            "ical_sync_status",
            "ehr_appointment_url",
            "pending_expires_at",
            "recurrence_index",
            "is_exception",
        ):
            assert withheld not in row, f"{withheld} reached a patient-facing response"

    def test_the_note_text_is_not_in_the_body_anywhere(self, client) -> None:
        """Belt and braces: the field is gone, and so is its content."""
        assert "clinician's private note" not in client.get("/api/patient/appointments").text

    def test_the_fields_a_patient_does_get(self, client) -> None:
        row = client.get("/api/patient/appointments").json()["data"][0]
        assert set(row) == {
            "id",
            "start_at",
            "end_at",
            "duration_minutes",
            "status",
            "session_type",
            "video_link",
            "video_platform",
            "recurrence_rule",
            "recurring_appointment_id",
        }


class TestAudit:
    def test_every_disclosed_appointment_is_audited(
        self,
        client,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        """Per row, not per request — 'who saw what' needs the what."""
        client.get("/api/patient/appointments")

        entries = _audit_entries(audit_repo, _PATIENT_A)
        assert [e.resource_id for e in entries] == ["appt-a1", "appt-a2"]
        assert {e.resource_type for e in entries} == {"appointment"}

    def test_the_audit_records_the_patient_as_the_actor(
        self,
        client,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        """The row must say a PATIENT did this, not a clinician.

        ``actor_type`` is what the audit policy splits on, so a read
        recorded as anything else would both misattribute the disclosure
        and, on a real database, be refused by the policy arm.
        """
        client.get("/api/patient/appointments")

        entries = _audit_entries(audit_repo, _PATIENT_A)
        assert entries, "no audit entry was written for a PHI read"
        assert all(e.actor_type == "patient" for e in entries)
        assert all(e.user_id == _PATIENT_A for e in entries)
        assert all(e.patient_id == _PATIENT_A for e in entries)

    def test_no_audit_entry_names_another_patient(
        self,
        client,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        client.get("/api/patient/appointments")
        assert not _audit_entries(audit_repo, _PATIENT_B)


class TestTheRouteTakesNoPatientIdFromTheCaller:
    def test_there_is_no_patient_id_path_parameter(self) -> None:
        """The IDOR surface this route deliberately does not have.

        A ``/api/patient/{patient_id}/appointments`` shape would invite an
        id the caller controls. This asserts the route shape itself, so a
        later refactor cannot quietly reintroduce one.
        """
        # Via the flattening helper, not ``app.routes`` directly: the route
        # tree is nested, so the flat list does not contain mounted paths.
        paths = [path for path, _ in iter_api_routes(app) if path.startswith("/api/patient/")]
        assert "/api/patient/appointments" in paths
        assert not [p for p in paths if "{patient_id}" in p]

    def test_a_query_string_patient_id_is_ignored(self, client) -> None:
        """Smuggling another patient's id changes nothing."""
        response = client.get(f"/api/patient/appointments?patient_id={_PATIENT_B}")
        ids = [row["id"] for row in response.json()["data"]]
        assert ids == ["appt-a1", "appt-a2"]
        assert "appt-b1" not in response.text
