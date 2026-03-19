# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for audit logging service."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from app.models import Patient, User
from app.models.audit import AUDIT_LOG_RETENTION_DAYS, AuditAction, AuditLogEntry, ResourceType
from app.models.session import SessionStatus, TherapySession, Transcript
from app.services.audit_service import AuditService


@pytest.fixture
def mock_db() -> MagicMock:
    """Create mock Firestore client."""
    db = MagicMock()
    db.collection.return_value.document.return_value.set = MagicMock()
    return db


@pytest.fixture
def audit_service(mock_db: MagicMock) -> AuditService:
    """Create audit service with mock db."""
    return AuditService(mock_db)


@pytest.fixture
def test_user() -> User:
    """Create test user."""
    return User(
        id="user-123",
        email="test@example.com",
        name="Test User",
        created_at="2024-01-01T00:00:00Z",
    )


@pytest.fixture
def test_patient() -> Patient:
    """Create test patient."""
    return Patient(
        id="patient-456",
        user_id="user-123",
        first_name="John",
        last_name="Doe",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


@pytest.fixture
def test_session(test_patient: Patient) -> TherapySession:
    """Create test session."""
    return TherapySession(
        id="session-789",
        user_id="user-123",
        patient_id=test_patient.id,
        session_date="2024-06-15T10:00:00Z",
        session_number=1,
        status=SessionStatus.PENDING_REVIEW,
        transcript=Transcript(format="plaintext", content="Test transcript"),
        created_at="2024-06-15T11:00:00Z",
    )


@pytest.fixture
def mock_request() -> MagicMock:
    """Create mock FastAPI request."""
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
        """Entry should auto-generate id, timestamp, and expires_at."""
        entry = AuditLogEntry(
            user_id="user-1",
            user_email="test@test.com",
            user_name="Test",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
        )

        assert entry.id is not None
        assert len(entry.id) > 0
        assert entry.timestamp is not None
        assert entry.expires_at is not None

    def test_expires_at_is_180_days_from_timestamp(self) -> None:
        """Expires at should be 180 days after timestamp."""
        entry = AuditLogEntry(
            user_id="user-1",
            user_email="test@test.com",
            user_name="Test",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
        )

        timestamp = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(entry.expires_at.replace("Z", "+00:00"))
        delta = expires - timestamp

        assert delta.days == AUDIT_LOG_RETENTION_DAYS

    def test_to_dict_excludes_none_values(self) -> None:
        """to_dict should exclude None values."""
        entry = AuditLogEntry(
            user_id="user-1",
            user_email="test@test.com",
            user_name="Test",
            action=AuditAction.PATIENT_VIEWED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
        )

        data = entry.to_dict()

        assert "patient_id" not in data
        assert "patient_name" not in data
        assert "session_id" not in data
        assert "ip_address" not in data
        assert "user_agent" not in data
        assert "changes" not in data

    def test_to_dict_includes_set_values(self) -> None:
        """to_dict should include explicitly set values."""
        entry = AuditLogEntry(
            user_id="user-1",
            user_email="test@test.com",
            user_name="Test",
            action=AuditAction.PATIENT_UPDATED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="patient-1",
            patient_id="patient-1",
            patient_name="John Doe",
            ip_address="192.168.1.1",
            changes={"status": {"old": "active", "new": "inactive"}},
        )

        data = entry.to_dict()

        assert data["patient_id"] == "patient-1"
        assert data["patient_name"] == "John Doe"
        assert data["ip_address"] == "192.168.1.1"
        assert data["changes"] == {"status": {"old": "active", "new": "inactive"}}


class TestAuditService:
    """Tests for AuditService."""

    def test_log_patient_action_writes_to_firestore(
        self,
        audit_service: AuditService,
        mock_db: MagicMock,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
    ) -> None:
        """log_patient_action should write entry to Firestore."""
        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_VIEWED,
            test_user,
            mock_request,
            test_patient,
        )

        # Verify Firestore was called
        mock_db.collection.assert_called_with("audit_logs")
        mock_db.collection().document.assert_called_with(entry.id)
        mock_db.collection().document().set.assert_called_once()

        # Verify entry contents
        assert entry.user_id == test_user.id
        assert entry.user_email == test_user.email
        assert entry.user_name == test_user.name
        assert entry.action == AuditAction.PATIENT_VIEWED.value
        assert entry.resource_type == ResourceType.PATIENT.value
        assert entry.resource_id == test_patient.id
        assert entry.patient_id == test_patient.id
        assert entry.patient_name == test_patient.display_name
        assert entry.ip_address == "192.168.1.100"  # From X-Forwarded-For
        assert entry.user_agent == "TestBrowser/1.0"

    def test_log_patient_action_with_changes(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
    ) -> None:
        """log_patient_action should include changes when provided."""
        changes = {"status": {"old": "active", "new": "inactive"}}

        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_UPDATED,
            test_user,
            mock_request,
            test_patient,
            changes=changes,
        )

        assert entry.changes == changes

    def test_log_session_action_writes_to_firestore(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
        test_session: TherapySession,
        mock_request: MagicMock,
    ) -> None:
        """log_session_action should write entry to Firestore."""
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
        assert entry.patient_name == test_patient.display_name

    def test_log_patient_list_writes_count(
        self,
        audit_service: AuditService,
        test_user: User,
        mock_request: MagicMock,
    ) -> None:
        """log_patient_list should include patient count in changes."""
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
        """log_session_list should include session count in changes."""
        entry = audit_service.log_session_list(test_user, mock_request, 10)

        assert entry.action == AuditAction.SESSION_LISTED.value
        assert entry.resource_id == "list"
        assert entry.changes == {"session_count": 10}

    def test_log_admin_action(
        self,
        audit_service: AuditService,
        test_user: User,
        mock_request: MagicMock,
    ) -> None:
        """log_admin_action should log admin actions."""
        entry = audit_service.log_admin_action(
            AuditAction.EXPORT_ACTION_TAKEN,
            test_user,
            mock_request,
            resource_id="session-123",
            changes={"action": "approve", "new_status": "approved"},
        )

        assert entry.action == AuditAction.EXPORT_ACTION_TAKEN.value
        assert entry.resource_id == "session-123"
        assert entry.changes == {"action": "approve", "new_status": "approved"}

    def test_extracts_ip_from_x_forwarded_for(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
    ) -> None:
        """Should extract first IP from X-Forwarded-For header."""
        request = MagicMock()
        request.headers = {
            "X-Forwarded-For": "203.0.113.50, 70.41.3.18, 150.172.238.178",
            "User-Agent": "Test",
        }
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_VIEWED,
            test_user,
            request,
            test_patient,
        )

        assert entry.ip_address == "203.0.113.50"

    def test_falls_back_to_client_host(
        self,
        audit_service: AuditService,
        test_user: User,
        test_patient: Patient,
    ) -> None:
        """Should fall back to client.host when X-Forwarded-For missing."""
        request = MagicMock()
        request.headers = {"User-Agent": "Test"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_VIEWED,
            test_user,
            request,
            test_patient,
        )

        assert entry.ip_address == "10.0.0.1"

    def test_firestore_error_does_not_raise(
        self,
        audit_service: AuditService,
        mock_db: MagicMock,
        test_user: User,
        test_patient: Patient,
        mock_request: MagicMock,
    ) -> None:
        """Firestore errors should be logged but not raised."""
        mock_db.collection().document().set.side_effect = Exception("Firestore error")

        # Should not raise
        entry = audit_service.log_patient_action(
            AuditAction.PATIENT_VIEWED,
            test_user,
            mock_request,
            test_patient,
        )

        # Entry should still be returned
        assert entry is not None
        assert entry.user_id == test_user.id


class TestAuditAction:
    """Tests for AuditAction enum."""

    def test_all_actions_are_strings(self) -> None:
        """All audit actions should be string values."""
        for action in AuditAction:
            assert isinstance(action.value, str)

    def test_patient_actions_exist(self) -> None:
        """Should have all patient actions."""
        patient_actions = [
            AuditAction.PATIENT_CREATED,
            AuditAction.PATIENT_LISTED,
            AuditAction.PATIENT_VIEWED,
            AuditAction.PATIENT_UPDATED,
            AuditAction.PATIENT_DELETED,
            AuditAction.PATIENT_EXPORTED,
        ]
        for action in patient_actions:
            assert action.value.startswith("patient_")

    def test_session_actions_exist(self) -> None:
        """Should have all session actions."""
        session_actions = [
            AuditAction.SESSION_CREATED,
            AuditAction.SESSION_LISTED,
            AuditAction.SESSION_VIEWED,
            AuditAction.SESSION_FINALIZED,
            AuditAction.SESSION_RATING_UPDATED,
        ]
        for action in session_actions:
            assert action.value.startswith("session_")

    def test_admin_actions_exist(self) -> None:
        """Should have all admin actions."""
        admin_actions = [
            AuditAction.EXPORT_QUEUE_VIEWED,
            AuditAction.EXPORT_ACTION_TAKEN,
        ]
        for action in admin_actions:
            assert action.value.startswith("export_")
