# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for ExportService."""

from datetime import datetime
from unittest.mock import Mock

import pytest
from app.models import Patient, TherapySession, Transcript
from app.models.session import SOAPNote
from app.services import ExportService


@pytest.fixture
def mock_patient():
    """Create a mock patient."""
    return Patient(
        id="patient-123",
        user_id="user-456",
        first_name="John",
        last_name="Doe",
        date_of_birth="1980-01-15",
        diagnosis="Generalized Anxiety Disorder",
        session_count=2,
        last_session_date=datetime.fromisoformat("2024-01-15T00:00:00+00:00"),
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        updated_at=datetime.fromisoformat("2024-01-15T00:00:00+00:00"),
    )


@pytest.fixture
def mock_sessions():
    """Create mock therapy sessions."""
    return [
        TherapySession(
            id="session-1",
            user_id="user-456",
            patient_id="patient-123",
            session_date=datetime.fromisoformat("2024-01-15T00:00:00+00:00"),
            session_number=2,
            status="finalized",
            transcript=Transcript(format="txt", content="Patient discussed anxiety."),
            soap_note=SOAPNote.from_dict(
                {
                    "subjective": "Patient reports feeling anxious.",
                    "objective": "Patient appeared nervous.",
                    "assessment": "Anxiety symptoms present.",
                    "plan": "Continue therapy.",
                }
            ),
            soap_note_edited=None,
            quality_rating=4,
            created_at=datetime.fromisoformat("2024-01-15T10:00:00+00:00"),
            processing_started_at=datetime.fromisoformat("2024-01-15T10:01:00+00:00"),
            processing_completed_at=datetime.fromisoformat("2024-01-15T10:05:00+00:00"),
            finalized_at=datetime.fromisoformat("2024-01-15T10:10:00+00:00"),
        ),
        TherapySession(
            id="session-2",
            user_id="user-456",
            patient_id="patient-123",
            session_date=datetime.fromisoformat("2024-01-08T00:00:00+00:00"),
            session_number=1,
            status="finalized",
            transcript=Transcript(format="txt", content="Initial intake session."),
            soap_note=SOAPNote.from_dict(
                {
                    "subjective": "Patient seeking help for anxiety.",
                    "objective": "Patient calm during intake.",
                    "assessment": "Initial assessment complete.",
                    "plan": "Schedule regular sessions.",
                }
            ),
            soap_note_edited=SOAPNote.from_dict(
                {
                    "subjective": "Patient seeking help for anxiety (edited).",
                    "objective": "Patient calm during intake (edited).",
                    "assessment": "Initial assessment complete (edited).",
                    "plan": "Schedule regular sessions (edited).",
                }
            ),
            quality_rating=5,
            created_at=datetime.fromisoformat("2024-01-08T10:00:00+00:00"),
            processing_started_at=datetime.fromisoformat("2024-01-08T10:01:00+00:00"),
            processing_completed_at=datetime.fromisoformat("2024-01-08T10:05:00+00:00"),
            finalized_at=datetime.fromisoformat("2024-01-08T10:10:00+00:00"),
        ),
    ]


@pytest.fixture
def mock_patient_repo(mock_patient):
    """Create a mock patient repository."""
    repo = Mock()
    repo.get.return_value = mock_patient
    return repo


@pytest.fixture
def mock_session_repo(mock_sessions):
    """Create a mock session repository."""
    repo = Mock()
    repo.list_by_patient.return_value = mock_sessions
    return repo


@pytest.fixture
def export_service(mock_patient_repo, mock_session_repo):
    """Create an ExportService instance."""
    return ExportService(mock_patient_repo, mock_session_repo)


def test_export_json_format(export_service, mock_patient_repo, mock_session_repo):
    """Test exporting patient data in JSON format."""
    result = export_service.get_patient_export_data("patient-123", "user-456", "json")

    # Verify repository calls
    mock_patient_repo.get.assert_called_once_with("patient-123", "user-456")
    mock_session_repo.list_by_patient.assert_called_once_with("patient-123", "user-456")

    # Verify result structure
    assert result["export_format"] == "json"
    assert "patient" in result
    assert "sessions" in result
    assert "exported_at" in result

    # Verify patient data
    patient_data = result["patient"]
    assert patient_data["id"] == "patient-123"
    assert patient_data["first_name"] == "John"
    assert patient_data["last_name"] == "Doe"

    # Verify sessions data
    sessions_data = result["sessions"]
    assert len(sessions_data) == 2
    assert sessions_data[0]["id"] == "session-1"
    assert sessions_data[0]["session_number"] == 2
    assert sessions_data[0]["was_edited"] is False
    assert sessions_data[1]["was_edited"] is True


def test_export_pdf_format(export_service, mock_patient_repo, mock_session_repo):
    """Test exporting patient data in PDF format."""
    result = export_service.get_patient_export_data("patient-123", "user-456", "pdf")

    # Verify repository calls
    mock_patient_repo.get.assert_called_once_with("patient-123", "user-456")
    mock_session_repo.list_by_patient.assert_called_once_with("patient-123", "user-456")

    # Verify result structure
    assert "content" in result
    assert "content_type" in result
    assert "filename" in result
    assert result["content_type"] == "application/pdf"
    assert result["filename"].startswith("patient_patient-123_export_")
    assert result["filename"].endswith(".pdf")

    # Verify PDF content is bytes
    assert isinstance(result["content"], bytes)
    assert len(result["content"]) > 0
    # PDF files start with %PDF
    assert result["content"].startswith(b"%PDF")


def test_export_patient_not_found(export_service, mock_patient_repo):
    """Test export fails when patient not found."""
    mock_patient_repo.get.return_value = None

    with pytest.raises(ValueError, match="Patient patient-999 not found"):
        export_service.get_patient_export_data("patient-999", "user-456", "json")


def test_export_unsupported_format(export_service):
    """Test export fails with unsupported format."""
    with pytest.raises(ValueError, match="Unsupported export format: xml"):
        export_service.get_patient_export_data("patient-123", "user-456", "xml")


def test_export_with_no_sessions(export_service, mock_patient_repo, mock_session_repo):
    """Test exporting patient with no sessions."""
    mock_session_repo.list_by_patient.return_value = []

    result = export_service.get_patient_export_data("patient-123", "user-456", "json")

    assert result["export_format"] == "json"
    assert len(result["sessions"]) == 0


def test_session_to_export_dict_includes_all_fields(export_service, mock_sessions):
    """Test that session export includes all relevant fields."""
    session_dict = export_service._session_to_export_dict(mock_sessions[0])

    # Verify all expected fields are present (excluding internal metadata)
    assert "id" in session_dict
    assert "session_date" in session_dict
    assert "session_number" in session_dict
    assert "status" in session_dict
    assert "transcript" in session_dict
    assert "soap_note" in session_dict
    assert "soap_note_edited" in session_dict
    assert "final_soap_note" in session_dict
    assert "was_edited" in session_dict
    assert "created_at" in session_dict
    assert "finalized_at" in session_dict

    # Verify internal metadata is NOT included (HIPAA compliance)
    assert "quality_rating" not in session_dict
    assert "processing_started_at" not in session_dict
    assert "processing_completed_at" not in session_dict

    # Verify transcript structure
    assert session_dict["transcript"]["format"] == "txt"
    assert session_dict["transcript"]["content"] == "Patient discussed anxiety."


def test_multi_tenant_security(export_service, mock_patient_repo, mock_session_repo):
    """Test that export enforces multi-tenant security."""
    export_service.get_patient_export_data("patient-123", "user-456", "json")

    # Verify user_id is passed to both repositories
    mock_patient_repo.get.assert_called_once_with("patient-123", "user-456")
    mock_session_repo.list_by_patient.assert_called_once_with("patient-123", "user-456")
