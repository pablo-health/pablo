# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Admin API endpoints."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.auth.service import require_admin as require_admin_func
from app.models import User
from app.services import AuditService
from app.settings import Settings
from fastapi import HTTPException, status


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



# Export queue tests are in test_admin_export_queue.py (SaaS-only, excluded from OSS)
