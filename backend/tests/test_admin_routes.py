# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Admin API endpoints."""

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from app.auth.service import require_admin
from app.auth.service import require_admin as require_admin_func
from app.main import app
from app.models import User
from app.services import AuditService, get_audit_service
from app.settings import Settings
from fastapi import HTTPException, status
from fastapi.testclient import TestClient


@pytest.fixture
def admin_user() -> User:
    """Create a test admin user."""
    return User(
        id="admin-user-123",
        email="admin@example.com",
        name="Admin User",
        created_at="2024-01-01T00:00:00Z",
        baa_accepted_at="2024-01-01T00:00:00Z",
        baa_version="2024-01-01",
        is_admin=True,
    )


@pytest.fixture
def non_admin_user() -> User:
    """Create a test non-admin user."""
    return User(
        id="user-123",
        email="user@example.com",
        name="Regular User",
        created_at="2024-01-01T00:00:00Z",
        baa_accepted_at="2024-01-01T00:00:00Z",
        baa_version="2024-01-01",
        is_admin=False,
    )


@pytest.fixture
def mock_audit_service() -> AuditService:
    """Create a mock audit service that doesn't write to Firestore."""
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.set = MagicMock()
    return AuditService(mock_db)


@pytest.fixture
def mock_firestore_session() -> dict[str, Any]:
    """Create a mock Firestore session document."""
    return {
        "id": "session-123",
        "user_id": "user-123",
        "patient_id": "patient-123",
        "session_date": "2024-01-15T14:30:00Z",
        "session_number": 1,
        "status": "finalized",
        "quality_rating": 2,
        "export_status": "pending_review",
        "export_queued_at": "2024-01-15T15:00:00Z",
        "finalized_at": "2024-01-15T14:45:00Z",
        "redacted_transcript": "Patient <PERSON_1> discussed anxiety.",
        "redacted_soap_note": {
            "subjective": "<PERSON_1> reports anxiety.",
            "objective": "Patient appeared calm.",
            "assessment": "Anxiety improving.",
            "plan": "Continue therapy.",
        },
        "transcript": {"format": "txt", "content": "Original transcript"},
        "created_at": "2024-01-15T14:30:00Z",
    }


class TestRequireAdmin:
    """Test require_admin() dependency."""

    def test_bypasses_in_development_mode(self, non_admin_user: User) -> None:
        """Test that require_admin bypasses check in development mode."""
        # Development mode is set in conftest.py via ENVIRONMENT=development
        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value = Settings(environment="development")
            result = require_admin_func(non_admin_user)
            assert result == non_admin_user  # Non-admin user allowed in dev

    def test_enforces_in_production_mode_admin_user(self, admin_user: User) -> None:
        """Test that admin users pass check in production mode."""
        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value = Settings(environment="production")
            result = require_admin_func(admin_user)
            assert result == admin_user

    def test_enforces_in_production_mode_non_admin_user(self, non_admin_user: User) -> None:
        """Test that non-admin users are blocked in production mode."""
        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value = Settings(environment="production")
            with pytest.raises(HTTPException) as exc_info:
                require_admin_func(non_admin_user)

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            detail = exc_info.value.detail
            assert isinstance(detail, dict)
            assert detail["error"]["code"] == "ADMIN_REQUIRED"


class TestExportQueueEndpoints:
    """Test admin export queue endpoints."""

    def test_list_export_queue_empty(
        self, admin_user: User, mock_audit_service: AuditService
    ) -> None:
        """Test listing export queue returns empty list when no sessions queued."""
        mock_firestore = Mock()
        mock_collection = Mock()
        mock_query = Mock()

        # Mock Firestore query chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.where.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.stream.return_value = []

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        with patch("app.routes.admin.get_firestore_client", return_value=mock_firestore):
            client = TestClient(app)
            response = client.get("/api/admin/export-queue")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["data"] == []
        assert data["total"] == 0

        # Cleanup
        app.dependency_overrides.clear()

    def test_list_export_queue_with_sessions(
        self,
        admin_user: User,
        mock_firestore_session: dict[str, Any],
        mock_audit_service: AuditService,
    ) -> None:
        """Test listing export queue returns sessions with pending_review status."""
        mock_firestore = Mock()
        mock_collection = Mock()
        mock_query = Mock()
        mock_doc = Mock()

        # Mock document
        mock_doc.to_dict.return_value = mock_firestore_session

        # Mock Firestore query chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.where.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.stream.return_value = [mock_doc]

        # Mock patient lookup
        with (
            patch("app.routes.admin.get_firestore_client", return_value=mock_firestore),
            patch("app.routes.admin.FirestorePatientRepository") as mock_patient_repo_class,
        ):
            mock_patient_repo = Mock()
            mock_patient = Mock()
            mock_patient.formal_name = "Doe, Jane"
            mock_patient_repo.get.return_value = mock_patient
            mock_patient_repo_class.return_value = mock_patient_repo

            # Override dependencies
            app.dependency_overrides[require_admin] = lambda: admin_user
            app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

            client = TestClient(app)
            response = client.get("/api/admin/export-queue")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 1
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "session-123"
        assert data["data"][0]["patient_name"] == "Doe, Jane"
        assert data["data"][0]["export_status"] == "pending_review"
        assert data["data"][0]["quality_rating"] == 2

        # Cleanup
        app.dependency_overrides.clear()

    def test_perform_export_action_approve(
        self,
        admin_user: User,
        mock_firestore_session: dict[str, Any],
        mock_audit_service: AuditService,
    ) -> None:
        """Test approving a session sets status to approved."""
        mock_firestore = Mock()
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc = Mock()

        # Mock document existence
        mock_doc.exists = True
        mock_doc.to_dict.return_value = mock_firestore_session

        # Mock Firestore document chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        with patch("app.routes.admin.get_firestore_client", return_value=mock_firestore):
            client = TestClient(app)
            response = client.post(
                "/api/admin/export-queue/session-123/action",
                json={"action": "approve"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["session_id"] == "session-123"
        assert data["export_status"] == "approved"
        assert "approved successfully" in data["message"]

        # Verify update was called
        mock_doc_ref.update.assert_called_once()
        update_data = mock_doc_ref.update.call_args[0][0]
        assert update_data["export_status"] == "approved"
        assert update_data["export_reviewed_by"] == admin_user.id
        assert "export_reviewed_at" in update_data

        # Cleanup
        app.dependency_overrides.clear()

    def test_perform_export_action_skip(
        self,
        admin_user: User,
        mock_firestore_session: dict[str, Any],
        mock_audit_service: AuditService,
    ) -> None:
        """Test skipping a session sets status to skipped."""
        mock_firestore = Mock()
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc = Mock()

        # Mock document existence
        mock_doc.exists = True
        mock_doc.to_dict.return_value = mock_firestore_session

        # Mock Firestore document chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        with patch("app.routes.admin.get_firestore_client", return_value=mock_firestore):
            client = TestClient(app)
            response = client.post(
                "/api/admin/export-queue/session-123/action",
                json={"action": "skip"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["export_status"] == "skipped"

        # Cleanup
        app.dependency_overrides.clear()

    def test_perform_export_action_flag(
        self,
        admin_user: User,
        mock_firestore_session: dict[str, Any],
        mock_audit_service: AuditService,
    ) -> None:
        """Test flagging a session sets status to skipped with reason."""
        mock_firestore = Mock()
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc = Mock()

        # Mock document existence
        mock_doc.exists = True
        mock_doc.to_dict.return_value = mock_firestore_session

        # Mock Firestore document chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        with patch("app.routes.admin.get_firestore_client", return_value=mock_firestore):
            client = TestClient(app)
            response = client.post(
                "/api/admin/export-queue/session-123/action",
                json={"action": "flag", "reason": "PII concern"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["export_status"] == "skipped"

        # Cleanup
        app.dependency_overrides.clear()

    def test_perform_export_action_invalid_action(
        self,
        admin_user: User,
        mock_firestore_session: dict[str, Any],
        mock_audit_service: AuditService,
    ) -> None:
        """Test invalid action returns 400 error."""
        mock_firestore = Mock()
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc = Mock()

        # Mock document existence
        mock_doc.exists = True
        mock_doc.to_dict.return_value = mock_firestore_session

        # Mock Firestore document chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        with patch("app.routes.admin.get_firestore_client", return_value=mock_firestore):
            client = TestClient(app)
            response = client.post(
                "/api/admin/export-queue/session-123/action",
                json={"action": "invalid"},
            )

        # Should fail at pydantic validation level
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # Cleanup
        app.dependency_overrides.clear()

    def test_perform_export_action_session_not_found(
        self, admin_user: User, mock_audit_service: AuditService
    ) -> None:
        """Test action on non-existent session returns 404."""
        mock_firestore = Mock()
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc = Mock()

        # Mock document does not exist
        mock_doc.exists = False

        # Mock Firestore document chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        with patch("app.routes.admin.get_firestore_client", return_value=mock_firestore):
            client = TestClient(app)
            response = client.post(
                "/api/admin/export-queue/nonexistent/action",
                json={"action": "approve"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Cleanup
        app.dependency_overrides.clear()

    def test_approve_exports_to_braintrust(
        self,
        admin_user: User,
        mock_firestore_session: dict[str, Any],
        mock_audit_service: AuditService,
    ) -> None:
        """Test that approving a session exports it to Braintrust."""
        # Add naturalized data to session
        mock_firestore_session["naturalized_transcript"] = "Patient Jane Smith discussed anxiety."
        mock_firestore_session["naturalized_soap_note"] = {
            "subjective": "Jane Smith reports anxiety.",
            "objective": "Patient appeared calm.",
            "assessment": "Anxiety improving.",
            "plan": "Continue therapy.",
        }

        mock_firestore = Mock()
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc = Mock()

        # Mock document existence
        mock_doc.exists = True
        mock_doc.to_dict.return_value = mock_firestore_session

        # Mock Firestore document chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        # Mock BraintrustExportService and settings
        mock_settings = Mock()
        mock_settings.multi_tenancy_enabled = False
        mock_settings.is_braintrust_enabled = True
        mock_settings.braintrust_api_key.get_secret_value.return_value = "fake-key"
        mock_settings.braintrust_project_name = "Test Project"

        with (
            patch("app.routes.admin.get_firestore_client", return_value=mock_firestore),
            patch("app.routes.admin.BraintrustExportService") as mock_bt_service_class,
            patch("app.routes.admin.get_settings", return_value=mock_settings),
        ):
            mock_bt_service = Mock()
            mock_bt_service.export_session.return_value = "braintrust-record-123"
            mock_bt_service_class.return_value = mock_bt_service

            client = TestClient(app)
            response = client.post(
                "/api/admin/export-queue/session-123/action",
                json={"action": "approve"},
            )

        assert response.status_code == status.HTTP_200_OK

        # Verify BraintrustExportService was called
        mock_bt_service.export_session.assert_called_once()
        call_args = mock_bt_service.export_session.call_args[0]
        exported_session = call_args[0]
        assert exported_session.id == "session-123"
        assert exported_session.naturalized_transcript == "Patient Jane Smith discussed anxiety."

        # Verify session was updated twice: once for approval, once for export
        assert mock_doc_ref.update.call_count == 2

        # First call: approval update
        first_update = mock_doc_ref.update.call_args_list[0][0][0]
        assert first_update["export_status"] == "approved"

        # Second call: export update
        second_update = mock_doc_ref.update.call_args_list[1][0][0]
        assert second_update["export_status"] == "exported"
        assert "exported_at" in second_update

        # Cleanup
        app.dependency_overrides.clear()

    def test_approve_handles_braintrust_export_failure(
        self,
        admin_user: User,
        mock_firestore_session: dict[str, Any],
        mock_audit_service: AuditService,
    ) -> None:
        """Test that Braintrust export failure doesn't block approval."""
        # Add naturalized data to session
        mock_firestore_session["naturalized_transcript"] = "Patient Jane Smith discussed anxiety."
        mock_firestore_session["naturalized_soap_note"] = {
            "subjective": "Jane Smith reports anxiety.",
            "objective": "Patient appeared calm.",
            "assessment": "Anxiety improving.",
            "plan": "Continue therapy.",
        }

        mock_firestore = Mock()
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc = Mock()

        # Mock document existence
        mock_doc.exists = True
        mock_doc.to_dict.return_value = mock_firestore_session

        # Mock Firestore document chain
        mock_firestore.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc

        # Override dependencies
        app.dependency_overrides[require_admin] = lambda: admin_user
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        # Mock BraintrustExportService to raise an exception
        with (
            patch("app.routes.admin.get_firestore_client", return_value=mock_firestore),
            patch("app.routes.admin.BraintrustExportService") as mock_bt_service_class,
        ):
            mock_bt_service = Mock()
            mock_bt_service.export_session.side_effect = Exception("Braintrust API error")
            mock_bt_service_class.return_value = mock_bt_service

            client = TestClient(app)
            response = client.post(
                "/api/admin/export-queue/session-123/action",
                json={"action": "approve"},
            )

        # Approval should still succeed
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["export_status"] == "approved"

        # Verify session was updated once (only approval, not export)
        assert mock_doc_ref.update.call_count == 1
        update_data = mock_doc_ref.update.call_args[0][0]
        assert update_data["export_status"] == "approved"

        # Cleanup
        app.dependency_overrides.clear()
