# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Comprehensive tests for Patient API endpoints."""

import time
from datetime import datetime
from typing import Any

from app.auth.service import get_current_user_id, require_baa_acceptance
from app.main import app
from app.models import TherapySession, User
from app.models.transcript import Transcript
from fastapi import status
from fastapi.testclient import TestClient

# ============================================================================
# Priority 1: CRUD Happy Path Tests
# ============================================================================


def test_create_patient_success(client: TestClient, sample_patient_data: dict[str, Any]) -> None:
    """Test creating a patient with valid data returns 201."""
    response = client.post("/api/patients", json=sample_patient_data)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["first_name"] == "John"
    assert data["last_name"] == "Doe"
    assert data["date_of_birth"] == "1980-05-15T00:00:00Z"
    assert data["diagnosis"] == "Anxiety disorder"
    assert "id" in data
    assert "user_id" in data
    assert data["session_count"] == 0
    assert "created_at" in data
    assert "updated_at" in data
    assert data["last_session_date"] is None
    # Ensure internal fields are excluded
    assert "first_name_lower" not in data
    assert "last_name_lower" not in data


def test_list_patients_empty(client: TestClient) -> None:
    """Test listing patients returns empty list for new user."""
    response = client.get("/api/patients")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20


def test_list_patients_with_data(client: TestClient, sample_patient_data: dict[str, Any]) -> None:
    """Test listing patients returns all user's patients."""
    # Create two patients
    client.post("/api/patients", json=sample_patient_data)
    client.post("/api/patients", json={"first_name": "Jane", "last_name": "Smith"})

    response = client.get("/api/patients")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 2
    assert data["total"] == 2
    assert data["data"][0]["first_name"] == "John"
    assert data["data"][1]["first_name"] == "Jane"


def test_get_patient_by_id_success(client: TestClient, sample_patient_data: dict[str, Any]) -> None:
    """Test getting patient by ID returns patient details."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Get patient
    response = client.get(f"/api/patients/{patient_id}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == patient_id
    assert data["first_name"] == "John"
    assert data["last_name"] == "Doe"


def test_update_patient_success(client: TestClient, sample_patient_data: dict[str, Any]) -> None:
    """Test updating patient fields."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]
    original_updated_at = create_response.json()["updated_at"]

    # Update patient
    update_data = {"first_name": "Jonathan", "diagnosis": "Generalized anxiety disorder"}
    response = client.patch(f"/api/patients/{patient_id}", json=update_data)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["first_name"] == "Jonathan"
    assert data["last_name"] == "Doe"  # Unchanged
    assert data["diagnosis"] == "Generalized anxiety disorder"
    assert data["updated_at"] != original_updated_at  # Should be updated


def test_delete_patient_success(client: TestClient, sample_patient_data: dict[str, Any]) -> None:
    """Test deleting a patient."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Delete patient
    response = client.delete(f"/api/patients/{patient_id}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "message" in data
    assert "deleted successfully" in data["message"]

    # Verify patient is gone
    get_response = client.get(f"/api/patients/{patient_id}")
    assert get_response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Priority 2: Multi-Tenant Isolation Tests
# ============================================================================


def test_get_patient_other_user_returns_404(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that User A cannot access User B's patient."""
    # Create patient as user1
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Try to access as user2
    user2 = User(
        id="user2",
        email="user2@example.com",
        name="Test User 2",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
    )
    app.dependency_overrides[get_current_user_id] = lambda: "user2"
    app.dependency_overrides[require_baa_acceptance] = lambda: user2
    response = client.get(f"/api/patients/{patient_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["error"]["code"] == "NOT_FOUND"


def test_list_patients_only_returns_own_patients(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that list only returns current user's patients."""
    # Create patient as user1
    client.post("/api/patients", json=sample_patient_data)

    # Create patient as user2
    user2 = User(
        id="user2",
        email="user2@example.com",
        name="Test User 2",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
    )
    app.dependency_overrides[get_current_user_id] = lambda: "user2"
    app.dependency_overrides[require_baa_acceptance] = lambda: user2
    client.post("/api/patients", json={"first_name": "Jane", "last_name": "Smith"})

    # List as user2 - should only see Jane
    response = client.get("/api/patients")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["first_name"] == "Jane"


def test_update_patient_other_user_returns_404(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that User A cannot update User B's patient."""
    # Create patient as user1
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Try to update as user2
    user2 = User(
        id="user2",
        email="user2@example.com",
        name="Test User 2",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
    )
    app.dependency_overrides[get_current_user_id] = lambda: "user2"
    app.dependency_overrides[require_baa_acceptance] = lambda: user2
    response = client.patch(f"/api/patients/{patient_id}", json={"first_name": "Hacker"})

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_delete_patient_other_user_returns_404(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that User A cannot delete User B's patient."""
    # Create patient as user1
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Try to delete as user2
    user2 = User(
        id="user2",
        email="user2@example.com",
        name="Test User 2",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
    )
    app.dependency_overrides[get_current_user_id] = lambda: "user2"
    app.dependency_overrides[require_baa_acceptance] = lambda: user2
    response = client.delete(f"/api/patients/{patient_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Priority 3: Validation & Error Handling Tests
# ============================================================================


def test_create_patient_missing_first_name(client: TestClient) -> None:
    """Test creating patient without first_name returns 422."""
    response = client.post("/api/patients", json={"last_name": "Doe"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_patient_missing_last_name(client: TestClient) -> None:
    """Test creating patient without last_name returns 422."""
    response = client.post("/api/patients", json={"first_name": "John"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_patient_invalid_date_format(client: TestClient) -> None:
    """Test creating patient with invalid date format returns 422."""
    response = client.post(
        "/api/patients",
        json={
            "first_name": "John",
            "last_name": "Doe",
            "date_of_birth": "invalid-date",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_update_patient_invalid_date_format(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test updating patient with invalid date format returns 422."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Try to update with invalid date
    response = client.patch(f"/api/patients/{patient_id}", json={"date_of_birth": "not-a-date"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_get_patient_not_found(client: TestClient) -> None:
    """Test getting non-existent patient returns 404 with error structure."""
    response = client.get("/api/patients/nonexistent-id")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "NOT_FOUND"
    assert data["error"]["message"] == "Patient not found"
    assert "patient_id" in data["error"]["details"]


def test_delete_patient_not_found(client: TestClient) -> None:
    """Test deleting non-existent patient returns 404."""
    response = client.delete("/api/patients/nonexistent-id")

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Priority 4: Search Functionality Tests
# ============================================================================


def test_list_patients_search_by_last_name(client: TestClient) -> None:
    """Test search by last name with prefix matching."""
    # Create patients with different last names
    client.post("/api/patients", json={"first_name": "John", "last_name": "Doe"})
    client.post("/api/patients", json={"first_name": "Jane", "last_name": "Smith"})
    client.post("/api/patients", json={"first_name": "Bob", "last_name": "Doe"})

    # Search for "Doe"
    response = client.get("/api/patients?search=Doe")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 2
    assert all(p["last_name"] == "Doe" for p in data["data"])


def test_list_patients_search_by_first_name(client: TestClient) -> None:
    """Test search by first name with search_by parameter."""
    # Create patients
    client.post("/api/patients", json={"first_name": "John", "last_name": "Doe"})
    client.post("/api/patients", json={"first_name": "Jane", "last_name": "Smith"})
    client.post("/api/patients", json={"first_name": "Bob", "last_name": "Johnson"})

    # Search by first name
    response = client.get("/api/patients?search=J&search_by=first_name")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 2
    assert all(p["first_name"].startswith("J") for p in data["data"])


def test_list_patients_search_case_insensitive(client: TestClient) -> None:
    """Test that search is case-insensitive."""
    # Create patient
    client.post("/api/patients", json={"first_name": "John", "last_name": "Doe"})

    # Search with lowercase
    response = client.get("/api/patients?search=doe")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["last_name"] == "Doe"


def test_list_patients_sorted_by_name(client: TestClient) -> None:
    """Test that patients are sorted by last_name, then first_name."""
    # Create patients in random order
    client.post("/api/patients", json={"first_name": "Charlie", "last_name": "Smith"})
    client.post("/api/patients", json={"first_name": "Alice", "last_name": "Doe"})
    client.post("/api/patients", json={"first_name": "Bob", "last_name": "Doe"})

    response = client.get("/api/patients")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 3
    # Should be sorted: Doe Alice, Doe Bob, Smith Charlie
    assert data["data"][0]["last_name"] == "Doe"
    assert data["data"][0]["first_name"] == "Alice"
    assert data["data"][1]["last_name"] == "Doe"
    assert data["data"][1]["first_name"] == "Bob"
    assert data["data"][2]["last_name"] == "Smith"
    assert data["data"][2]["first_name"] == "Charlie"


def test_list_patients_invalid_search_by_parameter(client: TestClient) -> None:
    """Test that invalid search_by parameter returns 422."""
    # Create a patient
    client.post("/api/patients", json={"first_name": "John", "last_name": "Doe"})

    # Try to search with invalid search_by parameter
    response = client.get("/api/patients?search=John&search_by=invalid_field")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_list_patients_valid_search_by_values(client: TestClient) -> None:
    """Test that both valid search_by values (first_name, last_name) work."""
    # Create patients
    client.post("/api/patients", json={"first_name": "John", "last_name": "Doe"})
    client.post("/api/patients", json={"first_name": "Jane", "last_name": "Smith"})

    # Test search_by=first_name
    response = client.get("/api/patients?search=John&search_by=first_name")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["first_name"] == "John"

    # Test search_by=last_name (explicit)
    response = client.get("/api/patients?search=Smith&search_by=last_name")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["last_name"] == "Smith"


# ============================================================================
# Priority 5: Data Integrity Tests
# ============================================================================


def test_create_patient_generates_id_and_timestamps(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that patient creation auto-generates ID and timestamps."""
    response = client.post("/api/patients", json=sample_patient_data)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert "id" in data
    assert len(data["id"]) > 0
    assert "created_at" in data
    assert "updated_at" in data
    assert data["created_at"] == data["updated_at"]  # Should be same on creation


def test_update_patient_updates_timestamp(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that updating a patient changes updated_at timestamp."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]
    original_updated_at = create_response.json()["updated_at"]

    # Wait a tiny bit and update
    time.sleep(0.01)
    response = client.patch(f"/api/patients/{patient_id}", json={"first_name": "Jonathan"})

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["updated_at"] != original_updated_at


def test_patient_response_excludes_internal_fields(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that API responses don't include internal *_lower fields."""
    response = client.post("/api/patients", json=sample_patient_data)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert "first_name_lower" not in data
    assert "last_name_lower" not in data


def test_delete_patient_returns_session_count(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test that delete response includes session count in message."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Delete
    response = client.delete(f"/api/patients/{patient_id}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "message" in data
    assert "0 session" in data["message"]  # New patient has 0 sessions


def test_delete_patient_cascades_to_sessions(
    client: TestClient,
    mock_repo: Any,
    mock_session_repo: Any,
    mock_user_id: str,
    sample_patient_data: dict[str, Any],
) -> None:
    """Test that deleting a patient also deletes all associated sessions."""
    # Create patient via API
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Add sessions directly to the session repo
    for i in range(3):
        mock_session_repo.create(
            TherapySession(
                id=f"session-{i}",
                user_id=mock_user_id,
                patient_id=patient_id,
                session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
                session_number=i + 1,
                status="finalized",
                transcript=Transcript(format="txt", content="test"),
                created_at=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
            )
        )

    # Verify sessions exist
    sessions = mock_session_repo.list_by_patient(patient_id, mock_user_id)
    assert len(sessions) == 3

    # Delete patient via API
    response = client.delete(f"/api/patients/{patient_id}")
    assert response.status_code == status.HTTP_200_OK

    # Verify sessions were cascade-deleted
    sessions = mock_session_repo.list_by_patient(patient_id, mock_user_id)
    assert len(sessions) == 0


def test_delete_patient_cascade_does_not_affect_other_patients_sessions(
    client: TestClient,
    mock_repo: Any,
    mock_session_repo: Any,
    mock_user_id: str,
    sample_patient_data: dict[str, Any],
) -> None:
    """Test that cascade delete only removes sessions for the deleted patient."""
    # Create two patients
    resp1 = client.post("/api/patients", json=sample_patient_data)
    patient1_id = resp1.json()["id"]

    other_data = {**sample_patient_data, "first_name": "Jane", "last_name": "Smith"}
    resp2 = client.post("/api/patients", json=other_data)
    patient2_id = resp2.json()["id"]

    # Add sessions for both patients
    mock_session_repo.create(
        TherapySession(
            id="session-p1",
            user_id=mock_user_id,
            patient_id=patient1_id,
            session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
            session_number=1,
            status="finalized",
            transcript=Transcript(format="txt", content="test"),
            created_at=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
        )
    )
    mock_session_repo.create(
        TherapySession(
            id="session-p2",
            user_id=mock_user_id,
            patient_id=patient2_id,
            session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
            session_number=1,
            status="finalized",
            transcript=Transcript(format="txt", content="test"),
            created_at=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
        )
    )

    # Delete patient 1
    client.delete(f"/api/patients/{patient1_id}")

    # Patient 1's sessions are gone
    assert len(mock_session_repo.list_by_patient(patient1_id, mock_user_id)) == 0

    # Patient 2's sessions remain
    assert len(mock_session_repo.list_by_patient(patient2_id, mock_user_id)) == 1


def test_create_patient_with_email_and_phone(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test creating a patient with email and phone fields."""
    response = client.post("/api/patients", json=sample_patient_data)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["email"] == sample_patient_data["email"]
    assert data["phone"] == sample_patient_data["phone"]
    assert data["status"] == sample_patient_data["status"]


def test_create_patient_invalid_email(client: TestClient) -> None:
    """Test that invalid email format is rejected."""
    patient_data = {
        "first_name": "John",
        "last_name": "Doe",
        "email": "invalid-email",  # Missing @ and domain
    }

    response = client.post("/api/patients", json=patient_data)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_patient_invalid_phone(client: TestClient) -> None:
    """Test that invalid phone number is rejected."""
    patient_data = {
        "first_name": "John",
        "last_name": "Doe",
        "phone": "123",  # Too short
    }

    response = client.post("/api/patients", json=patient_data)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_patient_invalid_status(client: TestClient) -> None:
    """Test that invalid status value is rejected."""
    patient_data = {
        "first_name": "John",
        "last_name": "Doe",
        "status": "invalid_status",  # Not in allowed values
    }

    response = client.post("/api/patients", json=patient_data)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_update_patient_status(client: TestClient, sample_patient_data: dict[str, Any]) -> None:
    """Test updating patient status."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Update status
    update_data = {"status": "inactive"}
    response = client.patch(f"/api/patients/{patient_id}", json=update_data)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "inactive"


def test_update_patient_email_and_phone(
    client: TestClient, sample_patient_data: dict[str, Any]
) -> None:
    """Test updating patient email and phone."""
    # Create patient
    create_response = client.post("/api/patients", json=sample_patient_data)
    patient_id = create_response.json()["id"]

    # Update email and phone
    update_data = {
        "email": "newemail@example.com",
        "phone": "(555) 987-6543",
    }
    response = client.patch(f"/api/patients/{patient_id}", json=update_data)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == update_data["email"]
    assert data["phone"] == update_data["phone"]
