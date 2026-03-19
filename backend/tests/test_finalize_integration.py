# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration tests for session finalization with eval export queue."""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from app.main import app
from app.models import TherapySession, Transcript
from app.models.session import ExportStatus, SOAPNote
from app.repositories import InMemoryTherapySessionRepository
from app.routes.sessions import get_eval_export_service
from app.services.eval_export_service import EvalExportService, QueueDecision
from fastapi import status
from fastapi.testclient import TestClient


@pytest.fixture
def mock_eval_export_service() -> Mock:
    """Create a mock eval export service."""
    return Mock(spec=EvalExportService)


@pytest.fixture
def finalize_client(
    client: TestClient,
    mock_eval_export_service: Mock,
) -> Generator[TestClient, None, None]:
    """Create client with mocked eval export service."""
    app.dependency_overrides[get_eval_export_service] = lambda: mock_eval_export_service
    yield client
    # Cleanup
    if get_eval_export_service in app.dependency_overrides:
        del app.dependency_overrides[get_eval_export_service]


@pytest.fixture
def sample_session(
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> TherapySession:
    """Create a sample session in pending_review status."""
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=mock_user_id,
        patient_id="patient-123",
        session_date="2026-01-15T10:00:00Z",
        session_number=1,
        status="pending_review",
        transcript=Transcript(
            format="txt", content="Patient John Doe discussed anxiety and stress."
        ),
        created_at=datetime.now(UTC).isoformat(),
        soap_note=SOAPNote.from_dict(
            {
                "subjective": "Patient John Doe reports feeling anxious.",
                "objective": "Patient appears nervous and fidgety.",
                "assessment": "Generalized anxiety disorder.",
                "plan": "Continue weekly therapy sessions.",
            }
        ),
    )
    mock_session_repo.create(session)
    return session


def test_low_rated_session_automatically_queued(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that low-rated session (rating=1) is automatically queued for export."""
    # Mock the service to say "yes, queue this"
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=True, reason="Low rating (1 ≤ 2)"
    )

    # Mock the queue_session_for_export to simulate successful redaction
    def mock_queue(session):
        session.export_status = ExportStatus.PENDING_REVIEW.value
        session.export_queued_at = datetime.now(UTC).isoformat()
        session.redacted_transcript = "Patient <PERSON_1> discussed anxiety and stress."
        session.naturalized_transcript = "Patient Jane Smith discussed anxiety and stress."
        session.redacted_soap_note = SOAPNote.from_dict(
            {
                "subjective": "Patient <PERSON_1> reports feeling anxious.",
                "objective": "Patient appears nervous and fidgety.",
                "assessment": "Generalized anxiety disorder.",
                "plan": "Continue weekly therapy sessions.",
            }
        )
        session.naturalized_soap_note = SOAPNote.from_dict(
            {
                "subjective": "Patient Jane Smith reports feeling anxious.",
                "objective": "Patient appears nervous and fidgety.",
                "assessment": "Generalized anxiety disorder.",
                "plan": "Continue weekly therapy sessions.",
            }
        )
        return session

    mock_eval_export_service.queue_session_for_export.side_effect = mock_queue

    # Finalize the session with low rating (with required feedback)
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 1, "quality_rating_reason": "Poor quality"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Verify the session was finalized
    assert data["status"] == "finalized"
    assert data["quality_rating"] == 1

    # Verify export queue logic was called
    mock_eval_export_service.should_queue_for_export.assert_called_once_with(1)
    mock_eval_export_service.queue_session_for_export.assert_called_once()

    # Verify export fields are populated
    assert data["export_status"] == ExportStatus.PENDING_REVIEW.value
    assert data["export_queued_at"] is not None
    assert data["redacted_transcript"] is not None
    assert "<PERSON_1>" in data["redacted_transcript"]
    assert data["naturalized_transcript"] is not None
    assert "Jane Smith" in data["naturalized_transcript"]


def test_mid_rated_session_not_queued(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that mid-rated session (rating=3) is not queued for export."""
    # Mock the service to say "no, don't queue this"
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="Mid-range rating (3)"
    )

    # Finalize the session with mid rating (with required feedback)
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 3, "quality_rating_reason": "Mid-range quality"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Verify the session was finalized
    assert data["status"] == "finalized"
    assert data["quality_rating"] == 3

    # Verify decision was made
    mock_eval_export_service.should_queue_for_export.assert_called_once_with(3)

    # Verify queue_session_for_export was NOT called
    mock_eval_export_service.queue_session_for_export.assert_not_called()

    # Verify export fields are NOT populated (default values)
    assert data["export_status"] == "not_queued"
    assert data["export_queued_at"] is None
    assert data["redacted_transcript"] is None
    assert data["naturalized_transcript"] is None


def test_finalization_succeeds_even_if_redaction_fails(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that finalization succeeds even if PII redaction fails."""
    # Mock the service to say "yes, queue this"
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=True, reason="Low rating (1 ≤ 2)"
    )

    # Mock the queue_session_for_export to simulate redaction failure
    def mock_queue_with_error(session):
        # Simulate the error handling in the service
        session.export_status = ExportStatus.SKIPPED.value
        session.export_queued_at = datetime.now(UTC).isoformat()
        return session

    mock_eval_export_service.queue_session_for_export.side_effect = mock_queue_with_error

    # Finalize the session (with required feedback)
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 1, "quality_rating_reason": "Low quality"},
    )

    # Finalization should still succeed
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Verify the session was finalized
    assert data["status"] == "finalized"
    assert data["quality_rating"] == 1

    # Verify export status is SKIPPED (not PENDING_REVIEW)
    assert data["export_status"] == ExportStatus.SKIPPED.value
    assert data["export_queued_at"] is not None


def test_export_fields_populated_correctly(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that export fields are correctly populated in the response."""
    # Mock the service
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=True, reason="Low rating (1 ≤ 2)"
    )

    queued_time = datetime.now(UTC).isoformat()

    def mock_queue(session):
        session.export_status = ExportStatus.PENDING_REVIEW.value
        session.export_queued_at = queued_time
        session.redacted_transcript = "Redacted transcript"
        session.naturalized_transcript = "Naturalized transcript"
        session.redacted_soap_note = SOAPNote.from_dict(
            {
                "subjective": "Redacted S",
                "objective": "Redacted O",
                "assessment": "Redacted A",
                "plan": "Redacted P",
            }
        )
        session.naturalized_soap_note = SOAPNote.from_dict(
            {
                "subjective": "Natural S",
                "objective": "Natural O",
                "assessment": "Natural A",
                "plan": "Natural P",
            }
        )
        return session

    mock_eval_export_service.queue_session_for_export.side_effect = mock_queue

    # Finalize the session (with required feedback)
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 1, "quality_rating_reason": "Low quality"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Verify all export fields are present and correct
    assert data["export_status"] == ExportStatus.PENDING_REVIEW.value
    assert data["export_queued_at"] == queued_time
    assert data["export_reviewed_at"] is None
    assert data["export_reviewed_by"] is None
    assert data["exported_at"] is None

    # Verify redacted data is present
    assert data["redacted_transcript"] == "Redacted transcript"
    assert data["naturalized_transcript"] == "Naturalized transcript"

    assert data["redacted_soap_note"] is not None
    assert "Redacted S" in data["redacted_soap_note"]["subjective"]
    assert "Redacted O" in data["redacted_soap_note"]["objective"]
    assert "Redacted A" in data["redacted_soap_note"]["assessment"]
    assert "Redacted P" in data["redacted_soap_note"]["plan"]

    assert data["naturalized_soap_note"] is not None
    assert "Natural S" in data["naturalized_soap_note"]["subjective"]
    assert "Natural O" in data["naturalized_soap_note"]["objective"]
    assert "Natural A" in data["naturalized_soap_note"]["assessment"]
    assert "Natural P" in data["naturalized_soap_note"]["plan"]


def test_redacted_fields_populated_when_queued(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that redacted_transcript and redacted_soap_note are populated when queued."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=True, reason="Low rating (2 ≤ 2)"
    )

    def mock_queue(session):
        session.export_status = ExportStatus.PENDING_REVIEW.value
        session.export_queued_at = datetime.now(UTC).isoformat()
        # Simulate actual redaction with placeholders
        session.redacted_transcript = (
            "Patient <PERSON_1> discussed <CONDITION_1> and <CONDITION_2>."
        )
        session.naturalized_transcript = "Patient Jane Smith discussed anxiety and stress."
        session.redacted_soap_note = SOAPNote.from_dict(
            {
                "subjective": "Patient <PERSON_1> reports feeling anxious.",
                "objective": "Patient appears nervous.",
                "assessment": "<CONDITION_1>.",
                "plan": "Continue therapy with <PERSON_2>.",
            }
        )
        session.naturalized_soap_note = SOAPNote.from_dict(
            {
                "subjective": "Patient Jane Smith reports feeling anxious.",
                "objective": "Patient appears nervous.",
                "assessment": "Generalized anxiety disorder.",
                "plan": "Continue therapy with Dr. Johnson.",
            }
        )
        return session

    mock_eval_export_service.queue_session_for_export.side_effect = mock_queue

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 2, "quality_rating_reason": "Needs improvement"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Verify redacted transcript has placeholders
    assert "<PERSON_1>" in data["redacted_transcript"]
    assert "<CONDITION_1>" in data["redacted_transcript"]
    assert "Jane Smith" not in data["redacted_transcript"]

    # Verify naturalized transcript has fake names
    assert "Jane Smith" in data["naturalized_transcript"]
    assert "<PERSON_1>" not in data["naturalized_transcript"]

    # Verify redacted SOAP note has placeholders
    assert "<PERSON_1>" in data["redacted_soap_note"]["subjective"]
    assert "<CONDITION_1>" in data["redacted_soap_note"]["assessment"]
    assert "<PERSON_2>" in data["redacted_soap_note"]["plan"]
    assert "Jane Smith" not in data["redacted_soap_note"]["subjective"]

    # Verify naturalized SOAP note has fake names
    assert "Jane Smith" in data["naturalized_soap_note"]["subjective"]
    assert "Dr. Johnson" in data["naturalized_soap_note"]["plan"]
    assert "<PERSON_1>" not in data["naturalized_soap_note"]["subjective"]


def test_finalize_with_rating_reason(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test finalizing session with quality_rating_reason field."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="Mid-range rating (3)"
    )

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 3,
            "quality_rating_reason": "Assessment section was too vague",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["status"] == "finalized"
    assert data["quality_rating"] == 3
    assert data["quality_rating_reason"] == "Assessment section was too vague"
    assert data["quality_rating_sections"] is None


def test_finalize_with_rating_sections(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test finalizing session with quality_rating_sections field."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="Mid-range rating (3)"
    )

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 3,
            "quality_rating_sections": ["assessment", "plan"],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["status"] == "finalized"
    assert data["quality_rating"] == 3
    assert data["quality_rating_reason"] is None
    assert data["quality_rating_sections"] == ["assessment", "plan"]


def test_finalize_with_both_rating_feedback_fields(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test finalizing session with both reason and sections."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=True, reason="Low rating (2 ≤ 2)"
    )

    def mock_queue(session):
        session.export_status = ExportStatus.PENDING_REVIEW.value
        session.export_queued_at = datetime.now(UTC).isoformat()
        session.redacted_transcript = "Redacted content"
        return session

    mock_eval_export_service.queue_session_for_export.side_effect = mock_queue

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 2,
            "quality_rating_reason": "Assessment was too vague and plan lacked detail",
            "quality_rating_sections": ["assessment", "plan"],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["status"] == "finalized"
    assert data["quality_rating"] == 2
    assert data["quality_rating_reason"] == "Assessment was too vague and plan lacked detail"
    assert data["quality_rating_sections"] == ["assessment", "plan"]


def test_finalize_without_rating_feedback_fields(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test finalizing without reason/sections returns null for optional fields."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="High rating (5)"
    )

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 5},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["status"] == "finalized"
    assert data["quality_rating"] == 5
    assert data["quality_rating_reason"] is None
    assert data["quality_rating_sections"] is None


def test_firestore_round_trip_with_rating_feedback(
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> None:
    """Test that rating feedback fields persist through Firestore serialization."""
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=mock_user_id,
        patient_id="patient-123",
        session_date="2026-01-15T10:00:00Z",
        session_number=1,
        status="finalized",
        transcript=Transcript(format="txt", content="Test content"),
        created_at=datetime.now(UTC).isoformat(),
        soap_note=SOAPNote.from_dict(
            {
                "subjective": "S",
                "objective": "O",
                "assessment": "A",
                "plan": "P",
            }
        ),
        quality_rating=2,
        quality_rating_reason="Assessment section needs improvement",
        quality_rating_sections=["assessment", "plan"],
        finalized_at=datetime.now(UTC).isoformat(),
    )

    # Serialize to dict (Firestore)
    session_dict = session.to_dict()
    assert session_dict["quality_rating"] == 2
    assert session_dict["quality_rating_reason"] == "Assessment section needs improvement"
    assert session_dict["quality_rating_sections"] == ["assessment", "plan"]

    # Deserialize from dict
    restored_session = TherapySession.from_dict(session_dict)
    assert restored_session.quality_rating == 2
    assert restored_session.quality_rating_reason == "Assessment section needs improvement"
    assert restored_session.quality_rating_sections == ["assessment", "plan"]


def test_finalize_with_invalid_soap_section(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that invalid SOAP section is rejected with 422."""
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 2,
            "quality_rating_sections": ["invalid_section"],
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_finalize_low_rating_without_feedback_rejected(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that low rating without feedback is rejected with 422."""
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 2},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = response.json()
    assert "feedback" in data["detail"]["error"]["message"].lower()


def test_finalize_low_rating_with_reason_accepted(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that low rating with reason is accepted."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="Mid-range rating"
    )

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 2,
            "quality_rating_reason": "Assessment section needs improvement",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["quality_rating"] == 2
    assert data["quality_rating_reason"] == "Assessment section needs improvement"


def test_finalize_low_rating_with_sections_accepted(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that low rating with sections is accepted."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="Mid-range rating"
    )

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 2,
            "quality_rating_sections": ["assessment", "plan"],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["quality_rating"] == 2
    assert data["quality_rating_sections"] == ["assessment", "plan"]


def test_finalize_low_rating_empty_reason_rejected(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that empty reason (whitespace) is rejected."""
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 2,
            "quality_rating_reason": "   ",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = response.json()
    assert "feedback" in data["detail"]["error"]["message"].lower()


def test_finalize_low_rating_empty_sections_rejected(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that empty sections list is rejected."""
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 2,
            "quality_rating_sections": [],
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = response.json()
    assert "feedback" in data["detail"]["error"]["message"].lower()


def test_finalize_high_rating_no_feedback_required(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that high rating (≥ threshold) doesn't require feedback."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="High rating"
    )

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 5},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["quality_rating"] == 5


def test_finalize_sections_stored_as_strings(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
    mock_session_repo: InMemoryTherapySessionRepository,
) -> None:
    """Test that valid sections are stored correctly as strings."""
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="Mid-range rating"
    )

    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={
            "quality_rating": 2,
            "quality_rating_sections": ["assessment", "plan"],
        },
    )

    assert response.status_code == status.HTTP_200_OK

    # Verify sections are stored as strings in the database
    stored_session = mock_session_repo.get(sample_session.id, sample_session.user_id)
    assert stored_session is not None
    assert stored_session.quality_rating_sections == ["assessment", "plan"]
    assert all(isinstance(s, str) for s in stored_session.quality_rating_sections)


def test_update_rating_low_without_feedback_rejected(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that updating to low rating without feedback is rejected."""
    # First finalize with high rating
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="High rating"
    )
    finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 5},
    )

    # Then try to update to low rating without feedback
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/rating",
        json={"quality_rating": 2},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = response.json()
    assert "feedback" in data["detail"]["error"]["message"].lower()


def test_update_rating_low_with_feedback_accepted(
    finalize_client: TestClient,
    sample_session: TherapySession,
    mock_eval_export_service: Mock,
) -> None:
    """Test that updating to low rating with feedback is accepted."""
    # First finalize with high rating
    mock_eval_export_service.should_queue_for_export.return_value = QueueDecision(
        should_queue=False, reason="High rating"
    )
    finalize_client.patch(
        f"/api/sessions/{sample_session.id}/finalize",
        json={"quality_rating": 5},
    )

    # Then update to low rating with feedback
    response = finalize_client.patch(
        f"/api/sessions/{sample_session.id}/rating",
        json={
            "quality_rating": 2,
            "quality_rating_reason": "Changed my mind about quality",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["quality_rating"] == 2
    assert data["quality_rating_reason"] == "Changed my mind about quality"
