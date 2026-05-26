# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for audit logging service."""

from collections.abc import Iterator
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from app.models import Patient, User
from app.models.audit import AUDIT_LOG_RETENTION_DAYS, AuditAction, AuditLogEntry, ResourceType
from app.models.session import SessionStatus, TherapySession, Transcript
from app.repositories.audit import InMemoryAuditRepository
from app.services import audit_cloud_logging
from app.services.audit_service import AuditService
from app.settings import get_settings


@pytest.fixture
def repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def audit_service(repo: InMemoryAuditRepository) -> AuditService:
    return AuditService(repo)


@pytest.fixture
def test_user() -> User:
    return User(
        id="user-123",
        email="test@example.com",
        name="Test User",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
    )


@pytest.fixture
def test_patient() -> Patient:
    return Patient(
        id="patient-456",
        first_name="John",
        last_name="Doe",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
    )


@pytest.fixture
def test_session(test_patient: Patient) -> TherapySession:
    return TherapySession(
        id="session-789",
        user_id="user-123",
        patient_id=test_patient.id,
        session_date=datetime.fromisoformat("2024-06-15T10:00:00+00:00"),
        session_number=1,
        status=SessionStatus.PENDING_REVIEW,
        transcript=Transcript(format="plaintext", content="Test transcript"),
        created_at=datetime.fromisoformat("2024-06-15T11:00:00+00:00"),
    )


@pytest.fixture
def mock_request() -> MagicMock:
    request = MagicMock()
    request.headers = {
        "User-Agent": "TestBrowser/1.0",
        "X-Forwarded-For": "192.168.1.100",
    }
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


class TestAuditLogEntry:
    """Tests for AuditLogEntry dataclass."""

    def test_auto_generates_id_and_timestamps(self) -> None:
        entry = AuditLogEntry(
            user_id="user-1",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
        )
        assert entry.id
        assert entry.timestamp
        assert entry.expires_at

    def test_expires_at_is_retention_days_from_timestamp(self) -> None:
        entry = AuditLogEntry(
            user_id="user-1",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
        )
        timestamp = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(entry.expires_at.replace("Z", "+00:00"))
        assert (expires - timestamp).days == AUDIT_LOG_RETENTION_DAYS

    def test_retention_meets_hipaa_6_year_minimum(self) -> None:
        """HIPAA § 164.316(b)(2)(i) requires 6-year minimum retention."""
        six_years_in_days = 365 * 6
        assert six_years_in_days <= AUDIT_LOG_RETENTION_DAYS

    def test_to_dict_excludes_none_values(self) -> None:
        entry = AuditLogEntry(
            user_id="user-1",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
        )
        data = entry.to_dict()
        assert "patient_id" not in data
        assert "session_id" not in data
        assert "ip_address" not in data
        assert "user_agent" not in data
        assert "changes" not in data

    def test_no_phi_fields_on_dataclass(self) -> None:
        """Dataclass must not expose PHI attributes (user_email, user_name, patient_name)."""
        entry = AuditLogEntry(user_id="u", action="a", resource_type="r", resource_id="1")
        for banned in ("user_email", "user_name", "patient_name"):
            assert not hasattr(entry, banned), f"PHI field {banned!r} still on AuditLogEntry"


class TestAuditService:
    """Tests for AuditService."""

    def test_log_patient_action_persists_entry(
        self,
        audit_service: AuditService,
        repo: InMemoryAuditRepository,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
    ) -> None:
        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_VIEWED,
            test_user,
            mock_request,
            test_patient,
        )
        assert len(repo.all()) == 1
        persisted = repo.all()[0]
        assert persisted.id == entry.id
        assert entry.user_id == test_user.id
        assert entry.action == AuditAction.PATIENT_VIEWED.value
        assert entry.resource_type == ResourceType.PATIENT.value
        assert entry.resource_id == test_patient.id
        assert entry.patient_id == test_patient.id
        assert entry.ip_address == "192.168.1.100"
        assert entry.user_agent == "TestBrowser/1.0"

    def test_log_patient_action_with_changed_fields(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
    ) -> None:
        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_UPDATED,
            test_user,
            mock_request,
            test_patient,
            changes={"changed_fields": ["first_name", "diagnosis"]},
        )
        assert entry.changes == {"changed_fields": ["first_name", "diagnosis"]}

    def test_rejects_phi_field_name_in_changes(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
    ) -> None:
        """Passing a PHI field name as a top-level `changes` key must raise."""
        with pytest.raises(ValueError, match="PHI field name"):
            audit_service.log_patient_action(
                AuditAction.PATIENT_UPDATED,
                test_user,
                mock_request,
                test_patient,
                changes={"first_name": {"old": "A", "new": "B"}},
            )

    def test_log_session_action_persists(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
        test_session: TherapySession,
        mock_request: MagicMock,
    ) -> None:
        entry = audit_service.log_session_action(
            AuditAction.SESSION_VIEWED,
            test_user,
            mock_request,
            test_session,
            test_patient,
        )
        assert entry.action == AuditAction.SESSION_VIEWED.value
        assert entry.resource_type == ResourceType.SESSION.value
        assert entry.resource_id == test_session.id
        assert entry.session_id == test_session.id
        assert entry.patient_id == test_patient.id

    def test_log_patient_list_writes_count(
        self,
        audit_service: AuditService,
        test_user: User,
        mock_request: MagicMock,
    ) -> None:
        entry = audit_service.log_patient_list(test_user, mock_request, 5)
        assert entry.action == AuditAction.PATIENT_LISTED.value
        assert entry.resource_id == "list"
        assert entry.changes == {"patient_count": 5}

    def test_log_session_list_writes_count(
        self,
        audit_service: AuditService,
        test_user: User,
        mock_request: MagicMock,
    ) -> None:
        entry = audit_service.log_session_list(test_user, mock_request, 3)
        assert entry.action == AuditAction.SESSION_LISTED.value
        assert entry.changes == {"session_count": 3}


class TestCloudLoggingDualWrite:
    """Dual-write to Cloud Logging is best-effort and flag-gated.

    Postgres remains the canonical audit-of-record (§ 164.312(b)); Cloud
    Logging is the tamper-evident mirror that feeds a retention-locked
    GCS sink (§ 164.312(c)(2)). A failing mirror must not fail the
    request — losing the mirror copy is acceptable when the canonical
    row has already persisted, but a failed request would be a HIPAA
    audit gap on a successful PHI access.
    """

    @pytest.fixture(autouse=True)
    def _clear_settings_cache(self) -> Iterator[None]:
        # get_settings() is @lru_cache; clear so per-test env vars take effect.
        # Clear on teardown too: a flag-on test must not leave a cached
        # ``True`` behind once the suite default is off (conftest), or a
        # later file's first audited request would hit the real GCP client.
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_flag_off_skips_cloud_logging_import(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With the flag disabled, audit_cloud_logging is never imported.

        Critical for environments without google-cloud-logging installed
        (test/CI) or without GCP credentials (local dev). Verified by
        spying on the module attribute: if our code path doesn't import,
        the spy never runs.
        """
        monkeypatch.setenv("AUDIT_DUAL_WRITE_ENABLED", "false")

        called: list[object] = []

        def _spy(entry: AuditLogEntry) -> None:
            called.append(entry)

        monkeypatch.setattr(audit_cloud_logging, "write_to_cloud_logging", _spy)

        audit_service.log_patient_action(
            AuditAction.PATIENT_VIEWED, test_user, mock_request, test_patient
        )

        assert called == []

    def test_flag_on_invokes_cloud_logging_with_entry(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AUDIT_DUAL_WRITE_ENABLED", "true")

        captured: list[AuditLogEntry] = []

        def _spy(entry: AuditLogEntry) -> None:
            captured.append(entry)

        monkeypatch.setattr(audit_cloud_logging, "write_to_cloud_logging", _spy)

        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_VIEWED, test_user, mock_request, test_patient
        )

        assert len(captured) == 1
        assert captured[0].id == entry.id
        assert captured[0].action == AuditAction.PATIENT_VIEWED.value

    def test_cloud_logging_failure_does_not_fail_request(
        self,
        audit_service: AuditService,
        repo: InMemoryAuditRepository,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Cloud Logging hiccup must not propagate to the caller.

        The Postgres row has already persisted; a failing mirror does not
        constitute a HIPAA audit gap. The defense-in-depth try/except in
        ``_persist()`` wraps the call, so even a refactor that drops the
        inner swallow in ``write_to_cloud_logging`` cannot fail a request.
        """
        monkeypatch.setenv("AUDIT_DUAL_WRITE_ENABLED", "true")

        def _boom(entry: AuditLogEntry) -> None:
            raise RuntimeError("simulated Cloud Logging outage")

        monkeypatch.setattr(audit_cloud_logging, "write_to_cloud_logging", _boom)

        try:
            audit_service.log_patient_action(
                AuditAction.PATIENT_VIEWED, test_user, mock_request, test_patient
            )
        except RuntimeError:
            pytest.fail(
                "Cloud Logging failure leaked to caller — outer try/except "
                "in _persist() is missing or broken"
            )

        # Postgres row should still be there.
        assert len(repo.all()) == 1
        assert repo.all()[0].action == AuditAction.PATIENT_VIEWED.value


class TestCloudLoggingWriter:
    """Tests for the audit_cloud_logging module itself."""

    def test_write_swallows_gcp_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _BoomLogger:
            def log_struct(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("gcp down")

        # Override the lazy logger to avoid initializing a real GCP client.
        audit_cloud_logging._gcp_logger.cache_clear()

        def _fake_logger() -> _BoomLogger:
            return _BoomLogger()

        monkeypatch.setattr(audit_cloud_logging, "_gcp_logger", _fake_logger)

        entry = AuditLogEntry(
            user_id="u",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="p",
        )
        # Must not raise.
        audit_cloud_logging.write_to_cloud_logging(entry)

    def test_write_passes_dataclass_fields_to_log_struct(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        class _SpyLogger:
            def log_struct(self, payload: dict[str, object], **kwargs: object) -> None:
                captured.update(payload)
                captured["__severity__"] = kwargs.get("severity")

        audit_cloud_logging._gcp_logger.cache_clear()

        def _fake_logger() -> _SpyLogger:
            return _SpyLogger()

        monkeypatch.setattr(audit_cloud_logging, "_gcp_logger", _fake_logger)

        entry = AuditLogEntry(
            user_id="user-1",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
            patient_id="patient-1",
            ip_address="10.0.0.1",
            user_agent="Test/1.0",
        )
        audit_cloud_logging.write_to_cloud_logging(entry)

        assert captured["id"] == entry.id
        assert captured["user_id"] == "user-1"
        assert captured["action"] == AuditAction.PATIENT_VIEWED.value
        assert captured["resource_id"] == "patient-1"
        assert captured["patient_id"] == "patient-1"
        assert captured["ip_address"] == "10.0.0.1"
        assert captured["timestamp"] == entry.timestamp
        assert captured["expires_at"] == entry.expires_at
        assert captured["__severity__"] == "NOTICE"
