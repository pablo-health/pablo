# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for AuditReviewService — composes signals on top of the audit repo."""

from datetime import UTC, datetime, timedelta

import app.services.audit_review_service as audit_review_service_mod
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
    def test_fires_when_surnames_match(self, service, audit_repo, patient_repo, user_repo) -> None:
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
                first_name="Robert",
                last_name="Smith",  # matches user's surname
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            "u1",
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
                first_name="Robert",
                last_name="Jones",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            "u1",
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
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
        # Patient created 30 days ago (past intake suppression window)
        patient_created_ts = datetime.now(UTC) - timedelta(days=30)
        patient_repo.create(
            Patient(
                id="p1",
                first_name="X",
                last_name="Y",
                created_at=patient_created_ts,
                updated_at=patient_created_ts,
            ),
            "u1",
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
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
        # Patient created today (inside intake suppression window)
        now = datetime.now(UTC)
        patient_repo.create(
            Patient(
                id="p1",
                first_name="X",
                last_name="Y",
                created_at=now,
                updated_at=now,
            ),
            "u1",
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
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
        created = datetime.now(UTC) - timedelta(days=30)
        patient_repo.create(
            Patient(
                id="p1",
                first_name="X",
                last_name="Y",
                created_at=created,
                updated_at=created,
            ),
            "u1",
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
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
        created = datetime.now(UTC) - timedelta(days=30)
        patient_repo.create(
            Patient(
                id="p1",
                first_name="X",
                last_name="Y",
                created_at=created,
                updated_at=created,
            ),
            "u1",
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
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
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
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
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
    def test_fires_when_today_exceeds_p95(self, service, audit_repo, user_repo) -> None:
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
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

    def test_suppressed_during_user_warmup(self, service, audit_repo, user_repo) -> None:
        """User whose first activity is < MIN_BASELINE_DAYS_FOR_EXPORT_RATE
        ago must not trigger an export-rate alert — not enough history."""
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
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


# ---------- internal-actor annotation ----------


class TestInternalActorAnnotation:
    def test_entries_flagged_for_listed_user(self, service, audit_repo, user_repo) -> None:
        user_repo.update(
            User(id="bot", email="bot@e.com", name="Bot", created_at=datetime.now(UTC))
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="bot",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload(internal_actor_user_ids={"bot"})
        assert payload.entries[0]["is_internal_actor"] is True

    def test_entries_not_flagged_for_unlisted_user(self, service, audit_repo, user_repo) -> None:
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
            )
        )
        payload = service.compute_payload(internal_actor_user_ids={"bot"})
        assert payload.entries[0]["is_internal_actor"] is False

    def test_default_marks_every_actor_external(self, service, audit_repo, user_repo) -> None:
        user_repo.update(User(id="u1", email="u@e.com", name="U", created_at=datetime.now(UTC)))
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
        assert payload.entries[0]["is_internal_actor"] is False

    def test_aggregates_flagged_for_listed_user(self, service, audit_repo, user_repo) -> None:
        user_repo.update(
            User(id="bot", email="bot@e.com", name="Bot", created_at=datetime.now(UTC))
        )
        for i in range(BULK_DELETE_THRESHOLD + 1):
            audit_repo.append(
                AuditLogEntry(
                    user_id="bot",
                    action=AuditAction.PATIENT_DELETED.value,
                    resource_type=ResourceType.PATIENT.value,
                    resource_id=f"p{i}",
                    patient_id=f"p{i}",
                )
            )
        payload = service.compute_payload(internal_actor_user_ids={"bot"})
        alerts = [a for a in payload.user_aggregates if a["alert"] == "bulk_delete"]
        assert len(alerts) == 1
        assert alerts[0]["is_internal_actor"] is True


# A wide window so fixed-date rows below are always in-range; baseline stays
# thin (single row) so novelty self-suppresses and doesn't add noise.
_WIDE_WINDOW = 24 * 3650


def _ts(hour: int) -> str:
    return _iso(datetime(2026, 6, 1, hour, 0, tzinfo=UTC))


def _recent(hour: int) -> str:
    """A timestamp ~2 days old at a fixed UTC ``hour``. Recent enough that the
    user stays under the 7-day baseline (novelty self-suppresses), while the
    hour is controlled so off-hours is deterministic regardless of wall clock.
    Pair with a window of a few days so the row stays in range."""
    base = (datetime.now(UTC) - timedelta(days=2)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return _iso(base)


_FEW_DAYS = 24 * 5


class TestUnauthorizedAccess:
    def test_flagged_when_no_live_grant(self, service, audit_repo, patient_repo) -> None:
        # Patient created/granted to the owner u_owner; u_snoop reads it.
        patient_repo.create(
            Patient(
                id="p1",
                first_name="A",
                last_name="B",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            "u_owner",
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u_snoop",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(window_hours=_WIDE_WINDOW)
        assert payload.entries[0]["is_unauthorized_access"] is True

    def test_not_flagged_when_grant_present(self, service, audit_repo, patient_repo) -> None:
        patient_repo.create(
            Patient(
                id="p1",
                first_name="A",
                last_name="B",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            "u1",
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(window_hours=_WIDE_WINDOW)
        assert payload.entries[0]["is_unauthorized_access"] is False

    def test_internal_actor_is_exempt(self, service, audit_repo) -> None:
        # No grant anywhere, but the actor is a registered internal identity.
        audit_repo.append(
            AuditLogEntry(
                user_id="bot",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(
            window_hours=_WIDE_WINDOW, internal_actor_user_ids={"bot"}
        )
        assert payload.entries[0]["is_unauthorized_access"] is False


class TestForeignActor:
    def test_alert_when_user_not_in_roster(self, service, audit_repo) -> None:
        audit_repo.append(
            AuditLogEntry(
                user_id="outsider",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(window_hours=_WIDE_WINDOW, authorized_user_ids={"member"})
        foreign = [a for a in payload.user_aggregates if a["alert"] == "foreign_actor"]
        assert len(foreign) == 1
        assert foreign[0]["user_id"] == "outsider"

    def test_no_alert_for_roster_member(self, service, audit_repo) -> None:
        audit_repo.append(
            AuditLogEntry(
                user_id="member",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(window_hours=_WIDE_WINDOW, authorized_user_ids={"member"})
        assert not [a for a in payload.user_aggregates if a["alert"] == "foreign_actor"]

    def test_skipped_when_no_roster(self, service, audit_repo) -> None:
        # None roster (self-hosted single-tenant) → never guesses.
        audit_repo.append(
            AuditLogEntry(
                user_id="whoever",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(window_hours=_WIDE_WINDOW, authorized_user_ids=None)
        assert not [a for a in payload.user_aggregates if a["alert"] == "foreign_actor"]

    def test_internal_actor_not_foreign(self, service, audit_repo) -> None:
        audit_repo.append(
            AuditLogEntry(
                user_id="bot",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(
            window_hours=_WIDE_WINDOW,
            authorized_user_ids={"member"},
            internal_actor_user_ids={"bot"},
        )
        assert not [a for a in payload.user_aggregates if a["alert"] == "foreign_actor"]


class TestDeterministicGate:
    def test_routine_window_needs_no_model(self, service, audit_repo, patient_repo) -> None:
        # Grant-backed, normal-hours, ordinary read by an in-roster user.
        patient_repo.create(
            Patient(
                id="p1",
                first_name="A",
                last_name="B",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            "u1",
        )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_recent(18),
            )
        )
        payload = service.compute_payload(window_hours=_FEW_DAYS, authorized_user_ids={"u1"})
        assert payload.summary["needs_model_review"] is False
        assert payload.summary["total_entries"] == 1

    def test_unauthorized_access_trips_gate(self, service, audit_repo) -> None:
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p1",
                patient_id="p1",
                timestamp=_ts(18),
            )
        )
        payload = service.compute_payload(window_hours=_WIDE_WINDOW, authorized_user_ids={"u1"})
        # No grant → unauthorized → window must reach the model.
        assert payload.summary["needs_model_review"] is True

    def test_off_hours_detection(self, service) -> None:
        assert service._is_off_hours({"timestamp": _ts(8)}) is True  # in 6-11 UTC
        assert service._is_off_hours({"timestamp": _ts(18)}) is False

    def test_curation_caps_and_summarizes(
        self, service, audit_repo, patient_repo, monkeypatch
    ) -> None:
        monkeypatch.setattr(audit_review_service_mod, "MAX_MODEL_ENTRIES", 3)
        # 5 routine grant-backed reads + 1 unauthorized (flagged).
        patient_repo.create(
            Patient(
                id="p1",
                first_name="A",
                last_name="B",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            "u1",
        )
        for i in range(5):
            audit_repo.append(
                AuditLogEntry(
                    user_id="u1",
                    action=AuditAction.PATIENT_VIEWED.value,
                    resource_type=ResourceType.PATIENT.value,
                    resource_id="p1",
                    patient_id="p1",
                    timestamp=_recent(12 + (i % 4)),
                ),
            )
        audit_repo.append(
            AuditLogEntry(
                user_id="u1",
                action=AuditAction.PATIENT_VIEWED.value,
                resource_type=ResourceType.PATIENT.value,
                resource_id="p_other",
                patient_id="p_other",
                timestamp=_recent(18),
            ),
        )
        payload = service.compute_payload(window_hours=_FEW_DAYS, authorized_user_ids={"u1"})
        # Full volume reported, payload capped, omitted tail accounted for.
        assert payload.summary["total_entries"] == 6
        assert payload.summary["entries_sent"] <= 3
        assert payload.summary["entries_omitted"] >= 1
        # The flagged (unauthorized) row on p_other survives curation.
        assert any(
            e.get("patient_id") == "p_other" and e["is_unauthorized_access"]
            for e in payload.entries
        )
