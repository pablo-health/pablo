"""Integration tests for FirestorePatientRepository.

Tests actual Firestore behavior using the emulator.
Run with: FIRESTORE_EMULATOR_HOST=localhost:8080 pytest tests_integration/
"""

from typing import Any

import pytest
from app.models import Patient
from app.repositories import FirestorePatientRepository
from app.utcnow import utc_now_iso


@pytest.fixture
def repository(clean_firestore: Any) -> FirestorePatientRepository:
    """Create a FirestorePatientRepository instance."""
    return FirestorePatientRepository(clean_firestore)


@pytest.fixture
def sample_patient(test_user_id: str) -> Patient:
    """Create a sample patient for testing."""
    return Patient(
        id="patient-123",
        user_id=test_user_id,
        first_name="John",
        last_name="Doe",
        first_name_lower="john",
        last_name_lower="doe",
        date_of_birth="1980-05-15T00:00:00Z",
        diagnosis="Anxiety disorder",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )


# ============================================================================
# Priority 1: CRUD Happy Path Tests
# ============================================================================


def test_create_patient(repository: FirestorePatientRepository, sample_patient: Patient) -> None:
    """Test creating a patient stores it in Firestore."""
    result = repository.create(sample_patient)

    assert result.id == sample_patient.id
    assert result.first_name == "John"
    assert result.last_name == "Doe"

    # Verify it's actually in Firestore
    retrieved = repository.get(sample_patient.id, sample_patient.user_id)
    assert retrieved is not None
    assert retrieved.id == sample_patient.id
    assert retrieved.first_name == "John"


def test_get_patient(repository: FirestorePatientRepository, sample_patient: Patient) -> None:
    """Test retrieving a patient by ID."""
    repository.create(sample_patient)

    result = repository.get(sample_patient.id, sample_patient.user_id)

    assert result is not None
    assert result.id == sample_patient.id
    assert result.first_name == "John"
    assert result.last_name == "Doe"
    assert result.user_id == sample_patient.user_id


def test_get_patient_not_found(repository: FirestorePatientRepository, test_user_id: str) -> None:
    """Test getting non-existent patient returns None."""
    result = repository.get("nonexistent-id", test_user_id)
    assert result is None


def test_list_by_user_empty(repository: FirestorePatientRepository, test_user_id: str) -> None:
    """Test listing patients for user with no patients returns empty list."""
    result, total = repository.list_by_user(test_user_id)
    assert result == []
    assert total == 0


def test_list_by_user_with_patients(
    repository: FirestorePatientRepository, test_user_id: str
) -> None:
    """Test listing patients returns all user's patients."""
    # Create two patients
    patient1 = Patient(
        id="patient-1",
        user_id=test_user_id,
        first_name="Alice",
        last_name="Smith",
        first_name_lower="alice",
        last_name_lower="smith",
        date_of_birth="1985-03-20T00:00:00Z",
        diagnosis="Depression",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    patient2 = Patient(
        id="patient-2",
        user_id=test_user_id,
        first_name="Bob",
        last_name="Jones",
        first_name_lower="bob",
        last_name_lower="jones",
        date_of_birth="1990-07-10T00:00:00Z",
        diagnosis="PTSD",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )

    repository.create(patient1)
    repository.create(patient2)

    result, total = repository.list_by_user(test_user_id)

    assert len(result) == 2
    assert total == 2
    # Should be sorted by last name
    assert result[0].last_name == "Jones"
    assert result[1].last_name == "Smith"


def test_update_patient(repository: FirestorePatientRepository, sample_patient: Patient) -> None:
    """Test updating a patient."""
    repository.create(sample_patient)

    # Update the patient
    sample_patient.first_name = "Jane"
    sample_patient.diagnosis = "Updated diagnosis"
    result = repository.update(sample_patient)

    assert result.first_name == "Jane"
    assert result.diagnosis == "Updated diagnosis"
    assert result.updated_at > sample_patient.created_at

    # Verify it's updated in Firestore
    retrieved = repository.get(sample_patient.id, sample_patient.user_id)
    assert retrieved is not None
    assert retrieved.first_name == "Jane"
    assert retrieved.diagnosis == "Updated diagnosis"


def test_delete_patient(repository: FirestorePatientRepository, sample_patient: Patient) -> None:
    """Test deleting a patient."""
    repository.create(sample_patient)

    result = repository.delete(sample_patient.id, sample_patient.user_id)

    assert result is True

    # Verify it's gone from Firestore
    retrieved = repository.get(sample_patient.id, sample_patient.user_id)
    assert retrieved is None


def test_delete_patient_not_found(
    repository: FirestorePatientRepository, test_user_id: str
) -> None:
    """Test deleting non-existent patient returns False."""
    result = repository.delete("nonexistent-id", test_user_id)
    assert result is False


# ============================================================================
# Priority 2: Security - Multi-tenant Isolation
# ============================================================================


def test_get_patient_other_user_returns_none(
    repository: FirestorePatientRepository, sample_patient: Patient, test_user_id_2: str
) -> None:
    """Test that users cannot access other users' patients."""
    repository.create(sample_patient)

    # Try to get with different user_id
    result = repository.get(sample_patient.id, test_user_id_2)

    assert result is None


def test_list_by_user_only_returns_own_patients(
    repository: FirestorePatientRepository, test_user_id: str, test_user_id_2: str
) -> None:
    """Test that list_by_user only returns patients for that user."""
    # Create patients for two different users
    patient1 = Patient(
        id="patient-user1",
        user_id=test_user_id,
        first_name="User1",
        last_name="Patient",
        first_name_lower="user1",
        last_name_lower="patient",
        date_of_birth="1980-01-01T00:00:00Z",
        diagnosis="Test",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    patient2 = Patient(
        id="patient-user2",
        user_id=test_user_id_2,
        first_name="User2",
        last_name="Patient",
        first_name_lower="user2",
        last_name_lower="patient",
        date_of_birth="1980-01-01T00:00:00Z",
        diagnosis="Test",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )

    repository.create(patient1)
    repository.create(patient2)

    # Each user should only see their own patient
    user1_patients, user1_total = repository.list_by_user(test_user_id)
    user2_patients, user2_total = repository.list_by_user(test_user_id_2)

    assert len(user1_patients) == 1
    assert user1_total == 1
    assert len(user2_patients) == 1
    assert user2_total == 1
    assert user1_patients[0].id == "patient-user1"
    assert user2_patients[0].id == "patient-user2"


def test_delete_patient_other_user_returns_false(
    repository: FirestorePatientRepository, sample_patient: Patient, test_user_id_2: str
) -> None:
    """Test that users cannot delete other users' patients."""
    repository.create(sample_patient)

    # Try to delete with different user_id
    result = repository.delete(sample_patient.id, test_user_id_2)

    assert result is False

    # Verify patient still exists for correct user
    retrieved = repository.get(sample_patient.id, sample_patient.user_id)
    assert retrieved is not None


# ============================================================================
# Priority 3: Search Functionality
# ============================================================================


def test_search_by_last_name(repository: FirestorePatientRepository, test_user_id: str) -> None:
    """Test searching patients by last name prefix."""
    # Create patients with different last names
    patients = [
        Patient(
            id=f"patient-{i}",
            user_id=test_user_id,
            first_name="Test",
            last_name=last_name,
            first_name_lower="test",
            last_name_lower=last_name.lower(),
            date_of_birth="1980-01-01T00:00:00Z",
            diagnosis="Test",
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        for i, last_name in enumerate(["Smith", "Smithson", "Jones", "Johnson"])
    ]

    for patient in patients:
        repository.create(patient)

    # Search for "Smi" should return Smith and Smithson
    result, total = repository.list_by_user(test_user_id, search="Smi", search_by="last_name")

    assert len(result) == 2
    assert total == 2
    assert all(p.last_name.startswith("Smith") for p in result)


def test_search_by_first_name(repository: FirestorePatientRepository, test_user_id: str) -> None:
    """Test searching patients by first name prefix."""
    # Create patients with different first names
    patients = [
        Patient(
            id=f"patient-{i}",
            user_id=test_user_id,
            first_name=first_name,
            last_name="Test",
            first_name_lower=first_name.lower(),
            last_name_lower="test",
            date_of_birth="1980-01-01T00:00:00Z",
            diagnosis="Test",
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        for i, first_name in enumerate(["Alice", "Alison", "Bob", "Barbara"])
    ]

    for patient in patients:
        repository.create(patient)

    # Search for "Ali" should return Alice and Alison
    result, total = repository.list_by_user(test_user_id, search="Ali", search_by="first_name")

    assert len(result) == 2
    assert total == 2
    assert all(p.first_name.startswith("Ali") for p in result)


def test_search_case_insensitive(repository: FirestorePatientRepository, test_user_id: str) -> None:
    """Test that search is case-insensitive."""
    patient = Patient(
        id="patient-1",
        user_id=test_user_id,
        first_name="John",
        last_name="Smith",
        first_name_lower="john",
        last_name_lower="smith",
        date_of_birth="1980-01-01T00:00:00Z",
        diagnosis="Test",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    repository.create(patient)

    # Search with different cases should all work
    for search_term in ["smi", "SMI", "Smi", "sMi"]:
        result, _ = repository.list_by_user(test_user_id, search=search_term, search_by="last_name")
        assert len(result) == 1
        assert result[0].last_name == "Smith"


# ============================================================================
# Priority 4: Cascade Deletion
# ============================================================================


def test_delete_patient_cascades_to_sessions(
    clean_firestore: Any, repository: FirestorePatientRepository, test_user_id: str
) -> None:
    """Test that deleting a patient also deletes associated sessions."""
    patient = Patient(
        id="patient-1",
        user_id=test_user_id,
        first_name="John",
        last_name="Doe",
        first_name_lower="john",
        last_name_lower="doe",
        date_of_birth="1980-01-01T00:00:00Z",
        diagnosis="Test",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    repository.create(patient)

    # Create some sessions for this patient
    sessions_collection = clean_firestore.collection("sessions")
    for i in range(3):
        sessions_collection.add(
            {
                "patient_id": "patient-1",
                "user_id": test_user_id,
                "date": f"2024-01-0{i + 1}T10:00:00Z",
                "notes": f"Session {i + 1}",
            }
        )

    # Verify sessions exist
    sessions = list(sessions_collection.where("patient_id", "==", "patient-1").stream())
    assert len(sessions) == 3

    # Delete the patient
    result = repository.delete("patient-1", test_user_id)
    assert result is True

    # Verify sessions are deleted
    sessions = list(sessions_collection.where("patient_id", "==", "patient-1").stream())
    assert len(sessions) == 0


def test_update_regenerates_search_fields(
    repository: FirestorePatientRepository, sample_patient: Patient
) -> None:
    """Test that updating a patient regenerates lowercase search fields."""
    repository.create(sample_patient)

    # Update the name
    sample_patient.first_name = "JANE"
    sample_patient.last_name = "DOE-SMITH"
    repository.update(sample_patient)

    # Retrieve and verify search fields are lowercase
    retrieved = repository.get(sample_patient.id, sample_patient.user_id)
    assert retrieved is not None
    assert retrieved.first_name_lower == "jane"
    assert retrieved.last_name_lower == "doe-smith"

    # Verify search works with the new name
    result, _ = repository.list_by_user(
        sample_patient.user_id, search="jane", search_by="first_name"
    )
    assert len(result) == 1
    assert result[0].id == sample_patient.id
