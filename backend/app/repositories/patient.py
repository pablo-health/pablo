# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient repository implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .session import InMemoryTherapySessionRepository, TherapySessionRepository

if TYPE_CHECKING:
    from ..models import Patient


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
    """In-memory implementation of PatientRepository for testing and development."""

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
