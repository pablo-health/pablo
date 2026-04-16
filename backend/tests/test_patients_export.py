# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for patient export API endpoint."""

from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest
from app.auth.service import require_baa_acceptance
from app.models import User
from app.routes.patients import get_export_service, get_patient_repository, router
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_export_service():
    """Create a mock export service."""
    return Mock()


@pytest.fixture
def mock_user():
    """Create a mock user."""
    return User(
        id="user-456",
        email="test@example.com",
        name="Test User",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
    )


@pytest.fixture
def client(mock_export_service, mock_user):
    """Create a test client with mocked dependencies."""
    app = FastAPI()
    app.include_router(router)

    # Mock patient repo so export route's repo dependency doesn't hit Firestore
    mock_repo = Mock()
    mock_repo.get.return_value = Mock(id="patient-123", first_name="John", last_name="Doe")

    mock_audit = AuditService(MagicMock())

    # Override all dependencies the export route needs
    app.dependency_overrides[get_export_service] = lambda: mock_export_service
    app.dependency_overrides[require_baa_acceptance] = lambda: mock_user
    app.dependency_overrides[get_patient_repository] = lambda: mock_repo
    app.dependency_overrides[get_audit_service] = lambda: mock_audit

    return TestClient(app)


def test_export_patient_json_success(client, mock_export_service):
    """Test successful JSON export."""
    mock_export_service.get_patient_export_data.return_value = {
        "patient": {
            "id": "patient-123",
            "first_name": "John",
            "last_name": "Doe",
        },
        "sessions": [],
        "exported_at": "2024-01-15T10:00:00Z",
        "export_format": "json",
    }

    response = client.get("/api/patients/patient-123/export?format=json")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    data = response.json()
    assert data["export_format"] == "json"
    assert data["patient"]["id"] == "patient-123"

    mock_export_service.get_patient_export_data.assert_called_once_with(
        "patient-123", "user-456", "json"
    )


def test_export_patient_json_default_format(client, mock_export_service):
    """Test JSON export is the default format."""
    mock_export_service.get_patient_export_data.return_value = {
        "patient": {"id": "patient-123"},
        "sessions": [],
        "exported_at": "2024-01-15T10:00:00Z",
        "export_format": "json",
    }

    response = client.get("/api/patients/patient-123/export")

    assert response.status_code == 200
    mock_export_service.get_patient_export_data.assert_called_once_with(
        "patient-123", "user-456", "json"
    )


def test_export_patient_pdf_success(client, mock_export_service):
    """Test successful PDF export."""
    pdf_content = b"%PDF-1.4 fake pdf content"
    mock_export_service.get_patient_export_data.return_value = {
        "content": pdf_content,
        "content_type": "application/pdf",
        "filename": "patient_patient-123_export_2024-01-15.pdf",
    }

    response = client.get("/api/patients/patient-123/export?format=pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert "patient_patient-123_export_2024-01-15.pdf" in response.headers["content-disposition"]
    assert response.content == pdf_content

    mock_export_service.get_patient_export_data.assert_called_once_with(
        "patient-123", "user-456", "pdf"
    )


def test_export_patient_not_found(client, mock_export_service):
    """Test export returns 400 when patient not found."""
    mock_export_service.get_patient_export_data.side_effect = ValueError(
        "Patient patient-999 not found"
    )

    response = client.get("/api/patients/patient-999/export?format=json")

    assert response.status_code == 400
    data = response.json()
    assert data["detail"]["error"]["code"] == "INVALID_REQUEST"
    # Error message should be generic, not leaking internal details
    assert "Invalid export request" in data["detail"]["error"]["message"]


def test_export_unsupported_format(client, mock_export_service):
    """Test export returns 400 for unsupported format."""
    mock_export_service.get_patient_export_data.side_effect = ValueError(
        "Unsupported export format: xml"
    )

    response = client.get("/api/patients/patient-123/export?format=xml")

    assert response.status_code == 400
    data = response.json()
    assert data["detail"]["error"]["code"] == "INVALID_REQUEST"
    # Error message should be generic, not leaking internal details
    assert "Invalid export request" in data["detail"]["error"]["message"]


def test_export_multi_tenant_security(client, mock_export_service):
    """Test that export uses the current user's ID."""
    mock_export_service.get_patient_export_data.return_value = {
        "patient": {"id": "patient-123"},
        "sessions": [],
        "exported_at": "2024-01-15T10:00:00Z",
        "export_format": "json",
    }

    client.get("/api/patients/patient-123/export?format=json")

    # Verify user_id from auth is passed to service
    mock_export_service.get_patient_export_data.assert_called_once_with(
        "patient-123", "user-456", "json"
    )


def test_export_with_sessions(client, mock_export_service):
    """Test export includes session data."""
    mock_export_service.get_patient_export_data.return_value = {
        "patient": {"id": "patient-123"},
        "sessions": [
            {
                "id": "session-1",
                "session_number": 1,
                "session_date": "2024-01-15",
                "transcript": {"format": "txt", "content": "Session content"},
            }
        ],
        "exported_at": "2024-01-15T10:00:00Z",
        "export_format": "json",
    }

    response = client.get("/api/patients/patient-123/export?format=json")

    assert response.status_code == 200
    data = response.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["id"] == "session-1"
