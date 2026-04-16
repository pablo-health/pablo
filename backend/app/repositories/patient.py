# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient repository implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from ..models import Patient
from ..utcnow import utc_now
from .session import InMemoryTherapySessionRepository, TherapySessionRepository


class PatientRepository(ABC):
    """Abstract base class for patient data access."""

    @abstractmethod
    def get(self, patient_id: str, user_id: str) -> Patient | None:
        """Get patient by ID, ensuring it belongs to the user."""
        pass

    @abstractmethod
    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        """Get multiple patients by IDs, ensuring they belong to the user."""
        pass

    @abstractmethod
    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        search_by: str = "last_name",
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        """List patients for a user with pagination.

        Returns a tuple of (paginated_patients, total_count).
        """
        pass

    @abstractmethod
    def create(self, patient: Patient) -> Patient:
        """Create a new patient."""
        pass

    @abstractmethod
    def update(self, patient: Patient) -> Patient:
        """Update an existing patient."""
        pass

    @abstractmethod
    def delete(self, patient_id: str, user_id: str) -> bool:
        """Delete a patient and cascade to sessions. Returns True if deleted."""
        pass


class InMemoryPatientRepository(PatientRepository):
    """
    In-memory implementation of PatientRepository for testing and development.

    This provides a simple storage mechanism without requiring Firestore setup.
    """

    def __init__(self, session_repo: TherapySessionRepository | None = None) -> None:
        self._patients: dict[str, Patient] = {}
        self._session_repo = session_repo

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        """Get patient by ID, ensuring it belongs to the user."""
        patient = self._patients.get(patient_id)
        if patient and patient.user_id == user_id:
            return patient
        return None

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        """Get multiple patients by IDs, ensuring they belong to the user."""
        return {
            p.id: p for p in self._patients.values() if p.id in patient_ids and p.user_id == user_id
        }

    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        search_by: str = "last_name",
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        """List patients for a user with pagination."""
        patients = [p for p in self._patients.values() if p.user_id == user_id]

        if search:
            search_lower = search.lower()
            if search_by == "first_name":
                patients = [p for p in patients if p.first_name_lower.startswith(search_lower)]
            else:  # last_name (default, clinical standard)
                patients = [p for p in patients if p.last_name_lower.startswith(search_lower)]

        # Sort by last name, then first name (clinical standard)
        patients.sort(key=lambda p: (p.last_name_lower, p.first_name_lower))
        total = len(patients)
        offset = (page - 1) * page_size
        return patients[offset : offset + page_size], total

    def create(self, patient: Patient) -> Patient:
        """Create a new patient."""
        self._patients[patient.id] = patient
        return patient

    def update(self, patient: Patient) -> Patient:
        """Update an existing patient."""
        patient.updated_at = utc_now()
        # Regenerate search fields
        patient.first_name_lower = patient.first_name.lower()
        patient.last_name_lower = patient.last_name.lower()
        self._patients[patient.id] = patient
        return patient

    def delete(self, patient_id: str, user_id: str) -> bool:
        """Delete a patient and cascade to sessions. Returns True if deleted."""
        patient = self.get(patient_id, user_id)
        if not patient:
            return False

        # Cascade: delete associated therapy sessions
        if self._session_repo is not None and isinstance(
            self._session_repo, InMemoryTherapySessionRepository
        ):
            session_ids = [
                sid for sid, s in self._session_repo._sessions.items() if s.patient_id == patient_id
            ]
            for sid in session_ids:
                del self._session_repo._sessions[sid]

        del self._patients[patient_id]
        return True


class FirestorePatientRepository(PatientRepository):
    """Firestore implementation of PatientRepository."""

    def __init__(self, db: Any) -> None:
        """
        Initialize with Firestore client.

        Args:
            db: Firestore client instance from google.cloud.firestore
        """
        self.db = db
        self.collection = db.collection("patients")

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        """Get patient by ID, ensuring it belongs to the user."""
        doc = self.collection.document(patient_id).get()
        if doc.exists:
            patient = Patient.from_dict(doc.to_dict())
            # Ensure multi-tenant isolation
            if patient.user_id == user_id:
                return patient
        return None

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        """Get multiple patients by IDs, ensuring they belong to the user."""
        if not patient_ids:
            return {}

        # Firestore 'in' query supports up to 10 values
        # For larger sets, batch into chunks of 10
        patients = {}
        patient_id_list = list(patient_ids)

        for i in range(0, len(patient_id_list), 10):
            chunk = patient_id_list[i : i + 10]
            # Convert string IDs to document references for __name__ filter
            chunk_refs = [self.collection.document(patient_id) for patient_id in chunk]
            query = self.collection.where(filter=FieldFilter("user_id", "==", user_id)).where(
                filter=FieldFilter("__name__", "in", chunk_refs)
            )
            for doc in query.stream():
                patient = Patient.from_dict(doc.to_dict())
                patients[patient.id] = patient

        return patients

    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        search_by: str = "last_name",
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        """List patients for a user with pagination."""
        query = self.collection.where(filter=FieldFilter("user_id", "==", user_id))

        if search:
            search_lower = search.lower()
            search_field = "first_name_lower" if search_by == "first_name" else "last_name_lower"
            # Firestore prefix search using range query
            query = query.where(filter=FieldFilter(search_field, ">=", search_lower))
            query = query.where(filter=FieldFilter(search_field, "<", search_lower + "\uffff"))

        # Sort by last name, then first name (clinical standard)
        query = query.order_by("last_name_lower").order_by("first_name_lower")

        # Server-side count via Firestore aggregation
        count_result = query.count().get()
        total = count_result[0][0].value if count_result and count_result[0] else 0

        # Server-side pagination
        offset = (page - 1) * page_size
        paginated_query = query.offset(offset).limit(page_size)
        return [Patient.from_dict(doc.to_dict()) for doc in paginated_query.stream()], total

    def create(self, patient: Patient) -> Patient:
        """Create a new patient."""
        self.collection.document(patient.id).set(patient.to_dict())
        return patient

    def update(self, patient: Patient) -> Patient:
        """Update an existing patient."""
        patient.updated_at = utc_now()
        # Regenerate search fields
        patient.first_name_lower = patient.first_name.lower()
        patient.last_name_lower = patient.last_name.lower()
        self.collection.document(patient.id).set(patient.to_dict())
        return patient

    def delete(self, patient_id: str, user_id: str) -> bool:
        """Delete a patient and cascade to sessions. Returns True if deleted."""
        # First verify the patient belongs to this user
        patient = self.get(patient_id, user_id)
        if not patient:
            return False

        # Delete associated therapy sessions
        sessions = (
            self.db.collection("therapy_sessions")
            .where(filter=FieldFilter("patient_id", "==", patient_id))
            .stream()
        )
        for session in sessions:
            session.reference.delete()

        # Delete the patient
        self.collection.document(patient_id).delete()
        return True
