# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for AuditReviewService — composes signals on top of the audit repo."""

from datetime import UTC, datetime, timedelta

import pytest
from app.models import Patient, User
from app.models.audit import AuditAction, AuditLogEntry, ResourceType
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.session import InMemoryTherapySessionRepository
from app.repositories.user import InMemoryUserRepository
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.audit_review_service import (
    BULK_DELETE_THRESHOLD,
    MIN_APPOINTMENTS_FOR_CARETEAM_CHECK,
    AuditReviewService,
    _extract_surname,
    _percentile,
)


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def patient_repo() -> InMemoryPatientRepository:
    return InMemoryPatientRepository()


@pytest.fixture
def user_repo() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def appointment_repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def session_repo() -> InMemoryTherapySessionRepository:
    return InMemoryTherapySessionRepository()


@pytest.fixture
def service(
    audit_repo: InMemoryAuditRepository,
    patient_repo: InMemoryPatientRepository,
    user_repo: InMemoryUserRepository,
    appointment_repo: InMemoryAppointmentRepository,
    session_repo: InMemoryTherapySessionRepository,
) -> AuditReviewService:
    return AuditReviewService(
        audit_repo=audit_repo,
        patient_repo=patient_repo,
        user_repo=user_repo,
        appointment_repo=appointment_repo,
        session_repo=session_repo,
    )


def _seed_seasoned_user(audit_repo: InMemoryAuditRepository, user_id: str) -> None:
    """Give a user >= 7d of audit history so novelty checks engage."""
    audit_repo.append(
        AuditLogEntry(
            user_id=user_id,
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="seed",
            timestamp=_iso(datetime.now(UTC) - timedelta(days=30)),
        )
    )


def _seed_min_appointments(
    appt_repo: InMemoryAppointmentRepository, user_id: str, count: int
) -> None:
    now = datetime.now(UTC)
    for i in range(count):
        appt_repo.create(
            Appointment(
                id=f"appt-warmup-{i}",
                user_id=user_id,
                patient_id=f"other-patient-{i}",
                title="warmup",
                start_at=now - timedelta(days=30 + i),
                end_at=now - timedelta(days=30 + i) + timedelta(hours=1),
                duration_minutes=60,
                status="completed",
                session_type="individual",
            )
        )


# ---------- #1 same-last-name ----------


class TestSameLastNameFlag:
    def test_fires_when_surnames_match(
        self, service, audit_repo, patient_repo, user_repo
    ) -> None:
        user_repo.update(
            User(
                id="u1",
                email="jane@example.com",
                name="Jane Smith",
                created_at=datetime.now(UTC),
            )
        )
        patient_repo.create(
            Patient(
                id="p1",
                user_id="u1",
                first_name="Robert",
                last_name="Smith",  # matches user's surname
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload()
        assert payload.entries[0]["is_same_last_name"] is True

    def test_does_not_fire_when_surnames_differ(
        self, service, audit_repo, patient_repo, user_repo
    ) -> None:
        user_repo.update(
            User(
                id="u1",
                email="jane@example.com",
                name="Jane Smith",
                created_at=datetime.now(UTC),
            )
        )
        patient_repo.create(
            Patient(
                id="p1",
                user_id="u1",
                first_name="Robert",
                last_name="Jones",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload()
        assert payload.entries[0]["is_same_last_name"] is False

    def test_extract_surname_handles_edge_cases(self) -> None:
        assert _extract_surname("Jane Smith") == "smith"
        assert _extract_surname("Jane Elizabeth Smith") == "smith"
        assert _extract_surname("Smith") == "smith"
        assert _extract_surname("") is None
        assert _extract_surname(None) is None


# ---------- #7 no-treatment-relationship ----------


class TestNoTreatmentRelationshipFlag:
    def test_fires_for_established_patient_without_appointment(
        self, service, audit_repo, patient_repo, user_repo, appointment_repo
    ) -> None:
        """Seasoned user + established patient + no appointment → flagged."""
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        # Patient created 30 days ago (past intake suppression window)
        patient_created_ts = datetime.now(UTC) - timedelta(days=30)
        patient_repo.create(
            Patient(
                id="p1",
                user_id="u1",
                first_name="X",
                last_name="Y",
                created_at=patient_created_ts,
                updated_at=patient_created_ts,
            )
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_CREATED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_iso(patient_created_ts),
            )
        )
        _seed_min_appointments(appointment_repo, "u1", MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        _seed_seasoned_user(audit_repo, "u1")

        # The access itself — no appointment for this patient
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload()
        recent = [
            e
            for e in payload.entries
            if e["patient_id"] == "p1" and e["action"] == "patient_viewed"
        ]
        assert recent[-1]["is_no_treatment_relationship"] is True

    def test_suppressed_during_patient_intake_window(
        self, service, audit_repo, patient_repo, user_repo, appointment_repo
    ) -> None:
        """Access to a just-created patient must NOT fire — intake has no
        appointments yet by definition."""
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        # Patient created today (inside intake suppression window)
        now = datetime.now(UTC)
        patient_repo.create(
            Patient(
                id="p1",
                user_id="u1",
                first_name="X",
                last_name="Y",
                created_at=now,
                updated_at=now,
            )
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_CREATED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        _seed_min_appointments(appointment_repo, "u1", MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        _seed_seasoned_user(audit_repo, "u1")

        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload()
        recent = next(
            e
            for e in payload.entries
            if e["patient_id"] == "p1" and e["action"] == "patient_viewed"
        )
        assert recent["is_no_treatment_relationship"] is False

    def test_suppressed_when_appointment_exists_nearby(
        self, service, audit_repo, patient_repo, user_repo, appointment_repo
    ) -> None:
        """Access with a scheduled appointment nearby should NOT fire."""
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        created = datetime.now(UTC) - timedelta(days=30)
        patient_repo.create(
            Patient(
                id="p1",
                user_id="u1",
                first_name="X",
                last_name="Y",
                created_at=created,
                updated_at=created,
            )
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_CREATED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_iso(created),
            )
        )
        _seed_min_appointments(appointment_repo, "u1", MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        _seed_seasoned_user(audit_repo, "u1")
        # Appointment within ±7 days of now
        appointment_repo.create(
            Appointment(
                id="a-today",
                user_id="u1",
                patient_id="p1",
                title="session",
                start_at=datetime.now(UTC) + timedelta(hours=2),
                end_at=datetime.now(UTC) + timedelta(hours=3),
                duration_minutes=60,
                status="scheduled",
                session_type="individual",
            )
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload()
        recent = next(
            e
            for e in payload.entries
            if e["patient_id"] == "p1" and e["action"] == "patient_viewed"
        )
        assert recent["is_no_treatment_relationship"] is False

    def test_suppressed_during_system_warmup(
        self, service, audit_repo, patient_repo, user_repo, appointment_repo
    ) -> None:
        """If the user has < MIN_APPOINTMENTS_FOR_CARETEAM_CHECK total
        appointments, flag must NOT fire."""
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        created = datetime.now(UTC) - timedelta(days=30)
        patient_repo.create(
            Patient(
                id="p1",
                user_id="u1",
                first_name="X",
                last_name="Y",
                created_at=created,
                updated_at=created,
            )
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_CREATED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_iso(created),
            )
        )
        _seed_seasoned_user(audit_repo, "u1")
        # NOTE: no appointments seeded — system is cold
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload()
        recent = next(
            e
            for e in payload.entries
            if e["patient_id"] == "p1" and e["action"] == "patient_viewed"
        )
        assert recent["is_no_treatment_relationship"] is False


# ---------- #5 bulk delete ----------


class TestBulkDelete:
    def test_fires_above_threshold(self, service, audit_repo, user_repo) -> None:
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        for i in range(BULK_DELETE_THRESHOLD + 1):
            audit_repo.append(
                AuditLogEntry(
                    user_id="u1",
                    action=AuditAction.PATIENT_DELETED.value,
                    resource_type=ResourceType.PATIENT.value,
                    resource_id=f"p{i}",
                    patient_id=f"p{i}",
                )
            )
        payload = service.compute_payload()
        alerts = [a for a in payload.user_aggregates if a["alert"] == "bulk_delete"]
        assert len(alerts) == 1
        assert alerts[0]["user_id"] == "u1"
        assert alerts[0]["count"] == BULK_DELETE_THRESHOLD + 1

    def test_does_not_fire_below_threshold(self, service, audit_repo, user_repo) -> None:
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_DELETED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload()
        assert all(a["alert"] != "bulk_delete" for a in payload.user_aggregates)


# ---------- #4 export rate ----------


class TestExportRateAlert:
    def test_fires_when_today_exceeds_p95(
        self, service, audit_repo, user_repo
    ) -> None:
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        # Baseline: 20 days of 0-1 exports/day (P95 should be ~1)
        now = datetime.now(UTC)
        for d in range(2, 22):
            if d % 4 == 0:  # one export every 4 days
                audit_repo.append(
                    AuditLogEntry(
                        user_id="u1",
                        action=AuditAction.PATIENT_EXPORTED.value,
                        resource_type=ResourceType.PATIENT.value,
                        resource_id="p",
                        patient_id="p",
                        timestamp=_iso(now - timedelta(days=d)),
                    )
                )
        # Today: 10 exports — way above P95
        for _ in range(10):
            audit_repo.append(
                AuditLogEntry(
                    user_id="u1",
                    action=AuditAction.PATIENT_EXPORTED.value,
                    resource_type=ResourceType.PATIENT.value,
                    resource_id="p",
                    patient_id="p",
                )
            )
        payload = service.compute_payload()
        alerts = [a for a in payload.user_aggregates if a["alert"] == "high_export_rate"]
        assert len(alerts) == 1
        assert alerts[0]["count"] == 10

    def test_suppressed_during_user_warmup(
        self, service, audit_repo, user_repo
    ) -> None:
        """User whose first activity is < MIN_BASELINE_DAYS_FOR_EXPORT_RATE
        ago must not trigger an export-rate alert — not enough history."""
        user_repo.update(
            User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC))
        )
        now = datetime.now(UTC)
        # First activity 3 days ago — well inside warmup
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p",
                patient_id="p",
                timestamp=_iso(now - timedelta(days=3)),
            )
        )
        for _ in range(10):
            audit_repo.append(
                AuditLogEntry(
                    user_id="u1",
                    action=AuditAction.PATIENT_EXPORTED.value,
                    resource_type=ResourceType.PATIENT.value,
                    resource_id="p",
                    patient_id="p",
                )
            )
        payload = service.compute_payload()
        assert all(a["alert"] != "high_export_rate" for a in payload.user_aggregates)


# ---------- helpers ----------


class TestPercentile:
    def test_p95_of_empty_is_zero(self) -> None:
        assert _percentile([], 95) == 0.0

    def test_p95_of_single(self) -> None:
        assert _percentile([7], 95) == 7.0

    def test_p95_returns_upper_bound(self) -> None:
        values = [1, 1, 1, 2, 2, 2, 3, 3, 4, 10]
        result = _percentile(values, 95)
        assert result >= 4  # well above the median
