# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for patient export API endpoint."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_baa_acceptance
from app.models import AuditAction, DocumentCategory, PatientDocument, User
from app.models.audit import ResourceType
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
    register_exception_handlers(app)
    app.include_router(router)

    # Mock patient repo so export route's repo dependency doesn't hit the database
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
        "patient-123",
        "user-456",
        "json",
        include_transcripts=False,
        include_psychotherapy_notes=False,
    )


def test_export_patient_json_serializes_datetimes(client, mock_export_service):
    """The real export service returns datetime objects straight off the
    models — the response must encode them, not 500. The other tests mock
    with pre-stringified dates, which is exactly how this went unseen."""
    mock_export_service.get_patient_export_data.return_value = {
        "patient": {
            "id": "patient-123",
            "first_name": "John",
            "last_name": "Doe",
            "created_at": datetime(2026, 1, 5, 9, 30, tzinfo=UTC),
            "updated_at": datetime(2026, 2, 1, 14, 0, tzinfo=UTC),
        },
        "sessions": [{"id": "session-1", "session_date": datetime(2026, 3, 2, 10, 0, tzinfo=UTC)}],
        "exported_at": datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
        "export_format": "json",
    }

    response = client.get("/api/patients/patient-123/export?format=json")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["patient"]["created_at"] == "2026-01-05T09:30:00+00:00"
    assert data["sessions"][0]["session_date"] == "2026-03-02T10:00:00+00:00"


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
        "patient-123",
        "user-456",
        "json",
        include_transcripts=False,
        include_psychotherapy_notes=False,
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
        "patient-123",
        "user-456",
        "pdf",
        include_transcripts=False,
        include_psychotherapy_notes=False,
    )


def test_export_patient_zip_is_a_download_and_audited_as_zip(mock_export_service, mock_user):
    archive = b"PK\x03\x04 fake archive"
    mock_export_service.get_patient_export_data.return_value = {
        "content": archive,
        "content_type": "application/zip",
        "filename": "patient_patient-123_export_2024-01-15.zip",
    }
    audit = Mock()
    client = _audited_client(mock_export_service, mock_user, audit)

    response = client.get("/api/patients/patient-123/export?format=zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="patient_patient-123_export_2024-01-15.zip"'
    )
    assert response.content == archive
    assert audit.log_patient_action.call_args.kwargs["changes"]["export_format"] == "zip"


def test_export_patient_not_found(client, mock_export_service):
    """Test export returns 400 when patient not found."""
    mock_export_service.get_patient_export_data.side_effect = ValueError(
        "Patient patient-999 not found"
    )

    response = client.get("/api/patients/patient-999/export?format=json")

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_REQUEST"
    # Error message should be generic, not leaking internal details
    assert "Invalid export request" in data["error"]["message"]


def test_export_unsupported_format(client, mock_export_service):
    """Test export returns 400 for unsupported format."""
    mock_export_service.get_patient_export_data.side_effect = ValueError(
        "Unsupported export format: xml"
    )

    response = client.get("/api/patients/patient-123/export?format=xml")

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_REQUEST"
    # Error message should be generic, not leaking internal details
    assert "Invalid export request" in data["error"]["message"]


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
        "patient-123",
        "user-456",
        "json",
        include_transcripts=False,
        include_psychotherapy_notes=False,
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


def _audited_client(mock_export_service, mock_user, audit):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    mock_repo = Mock()
    mock_repo.get.return_value = Mock(id="patient-123", first_name="John", last_name="Doe")
    app.dependency_overrides[get_export_service] = lambda: mock_export_service
    app.dependency_overrides[require_baa_acceptance] = lambda: mock_user
    app.dependency_overrides[get_patient_repository] = lambda: mock_repo
    app.dependency_overrides[get_audit_service] = lambda: audit
    return TestClient(app)


@pytest.mark.parametrize(
    ("query", "transcripts", "psychotherapy"),
    [
        ("format=json", False, False),
        ("format=pdf&include_transcripts=true", True, False),
        ("format=json&include_transcripts=true&include_psychotherapy_notes=true", True, True),
    ],
)
def test_export_options_reach_service_and_audit_row(
    mock_export_service, mock_user, query, transcripts, psychotherapy
):
    """Both choices are forwarded to the service and recorded on the audit
    row next to the format, so the record shows what left the chart."""
    fmt = "pdf" if "format=pdf" in query else "json"
    if fmt == "pdf":
        mock_export_service.get_patient_export_data.return_value = {
            "content": b"%PDF-1.4",
            "content_type": "application/pdf",
            "filename": "export.pdf",
        }
    else:
        mock_export_service.get_patient_export_data.return_value = {"sessions": []}
    audit = Mock()
    client = _audited_client(mock_export_service, mock_user, audit)

    response = client.get(f"/api/patients/patient-123/export?{query}")

    assert response.status_code == 200, response.text
    mock_export_service.get_patient_export_data.assert_called_once_with(
        "patient-123",
        "user-456",
        fmt,
        include_transcripts=transcripts,
        include_psychotherapy_notes=psychotherapy,
    )
    audit.log_patient_action.assert_called_once()
    assert audit.log_patient_action.call_args.kwargs["changes"] == {
        "export_format": fmt,
        "include_transcripts": transcripts,
        "include_psychotherapy_notes": psychotherapy,
    }


def _upload(document_id: str, category: DocumentCategory) -> PatientDocument:
    return PatientDocument(
        id=document_id,
        patient_id="patient-123",
        user_id="user-456",
        filename=f"{document_id}.pdf",
        mime_type="application/pdf",
        gcs_path=f"default/{category.value}/{document_id}",
        size_bytes=10,
        created_at=datetime(2024, 1, 15, tzinfo=UTC),
        category=category,
    )


def test_each_file_carried_in_the_archive_is_audited_as_its_own_disclosure(
    mock_export_service, mock_user
):
    """An uploaded document is recorded under its category's download action,
    a restricted one apart, and each intake form under the form export action."""
    mock_export_service.get_patient_export_data.return_value = {
        "content": b"PK\x03\x04 fake archive",
        "content_type": "application/zip",
        "filename": "export.zip",
        "documents": [
            _upload("doc-chart", DocumentCategory.CHART),
            _upload("doc-restricted", DocumentCategory.PSYCHOTHERAPY_NOTES),
        ],
        "intake_assignment_ids": ["assignment-1"],
    }
    audit = Mock()
    client = _audited_client(mock_export_service, mock_user, audit)

    response = client.get(
        "/api/patients/patient-123/export?format=zip&include_psychotherapy_notes=true"
    )

    assert response.status_code == 200, response.text
    documents = [
        (c.args[0], c.kwargs["document_id"], c.kwargs["category"])
        for c in audit.log_patient_document_action.call_args_list
    ]
    assert documents == [
        (AuditAction.PATIENT_DOCUMENT_DOWNLOADED, "doc-chart", "chart"),
        (
            AuditAction.PATIENT_DOCUMENT_DOWNLOADED_RESTRICTED,
            "doc-restricted",
            "psychotherapy_notes",
        ),
    ]
    [form] = audit.log.call_args_list
    assert form.kwargs["action"] == AuditAction.INTAKE_PACKET_EXPORTED
    assert form.kwargs["resource_type"] == ResourceType.PATIENT_INTAKE_ASSIGNMENT
    assert form.kwargs["resource_id"] == "assignment-1"


@pytest.mark.parametrize("fmt", ["zip", "pdf"])
def test_each_message_thread_in_the_copy_is_audited_as_a_thread_export(
    mock_export_service, mock_user, fmt
):
    """A conversation carried in the PDF or the archive is recorded the way its
    own thread export is: one row per thread, a count and no words."""
    mock_export_service.get_patient_export_data.return_value = {
        "content": b"%PDF-1.4" if fmt == "pdf" else b"PK\x03\x04 fake archive",
        "content_type": "application/pdf" if fmt == "pdf" else "application/zip",
        "filename": f"export.{fmt}",
        "message_threads": [("thread-1", 3), ("thread-2", 1)],
    }
    audit = Mock()
    client = _audited_client(mock_export_service, mock_user, audit)

    response = client.get(f"/api/patients/patient-123/export?format={fmt}")

    assert response.status_code == 200, response.text
    threads = [
        (c.kwargs["action"], c.kwargs["resource_id"], c.kwargs["patient_id"], c.kwargs["changes"])
        for c in audit.log_patient_message_action.call_args_list
    ]
    exported = AuditAction.PATIENT_MESSAGE_THREAD_EXPORTED
    assert threads == [
        (exported, "thread-1", "patient-123", {"message_count": 3}),
        (exported, "thread-2", "patient-123", {"message_count": 1}),
    ]
