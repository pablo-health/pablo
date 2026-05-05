# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient repository implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
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

    @abstractmethod
    def list_recently_deleted(
        self,
        user_id: str,
        *,
        window_days: int = 30,
    ) -> list[tuple[Patient, datetime]]:
        """List soft-deleted patients still inside the undo window.

        Returns ``(patient, deleted_at)`` pairs for patients whose
        ``deleted_at`` is non-NULL and within the last ``window_days``.
        Used by the ``include_deleted=recent`` listing path that powers
        the "Recently deleted" UI tab (THERAPY-yg2). After
        ``window_days`` rows remain on disk until the day-30 hard-purge
        cron (THERAPY-cgy) physically removes them, but they no longer
        appear in this listing. ``deleted_at`` is returned out-of-band
        because it does not live on the ``Patient`` dataclass — only
        the soft-delete-aware paths need it.
        """
        pass

    @abstractmethod
    def restore(self, patient_id: str, user_id: str, *, window_days: int = 30) -> Patient | None:
        """Undo a soft-delete by clearing ``deleted_at``.

        Returns the restored ``Patient`` on success, ``None`` if the
        patient is not soft-deleted, not owned by ``user_id``, or its
        ``deleted_at`` is already past the ``window_days`` cutoff (in
        which case the row is awaiting hard-purge and must not be
        revived).

        Cascade order mirrors ``delete()``: clears ``deleted_at`` on
        the patient and on its therapy sessions / notes that were
        cascaded by the original soft-delete. Session numbers are
        preserved — ``get_session_number_for_patient`` deliberately
        ignores ``deleted_at`` so numbering stays stable across the
        delete/restore cycle (THERAPY-nyb).
        """
        pass

    @abstractmethod
    def close_chart(
        self, patient_id: str, user_id: str, closure_reason: str | None
    ) -> Patient | None:
        """Close a patient's chart (THERAPY-hek).

        Sets ``chart_closed_at = NOW()`` and ``chart_closure_reason`` on
        the patient row. Orthogonal to soft-delete: chart closure does
        NOT advance the day-30 hard-purge clock and does NOT hide the
        row from list/get reads. Returns the updated patient, or
        ``None`` if not found / not owned / soft-deleted.
        """
        pass

    @abstractmethod
    def reopen_chart(self, patient_id: str, user_id: str) -> Patient | None:
        """Reopen a previously-closed chart (THERAPY-hek).

        Clears ``chart_closed_at`` and ``chart_closure_reason``. Returns
        the updated patient, or ``None`` if not found / not owned /
        soft-deleted.
        """
        pass


class InMemoryPatientRepository(PatientRepository):
    """In-memory implementation of PatientRepository for testing and development."""

    def __init__(self, session_repo: TherapySessionRepository | None = None) -> None:
        self._patients: dict[str, Patient] = {}
        # THERAPY-yg2: track soft-delete timestamps in a parallel map so
        # the in-memory repo (used in API tests) can model the same
        # tombstone-then-purge lifecycle as PostgresPatientRepository
        # without adding a deleted_at field to the Patient dataclass.
        self._deleted_at: dict[str, datetime] = {}
        self._session_repo = session_repo

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        """Get patient by ID, ensuring it belongs to the user.

        Hides soft-deleted rows from user-facing reads, matching the
        Postgres repo's behavior (THERAPY-nyb).
        """
        patient = self._patients.get(patient_id)
        if patient and patient.user_id == user_id and patient_id not in self._deleted_at:
            return patient
        return None

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        """Get multiple patients by IDs, ensuring they belong to the user."""
        return {
            p.id: p
            for p in self._patients.values()
            if p.id in patient_ids and p.user_id == user_id and p.id not in self._deleted_at
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
        patients = [
            p
            for p in self._patients.values()
            if p.user_id == user_id and p.id not in self._deleted_at
        ]

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
        """Soft-delete a patient and cascade to sessions. Returns True if deleted.

        Mirrors ``PostgresPatientRepository.delete``: stamps the in-memory
        soft-delete map and physically removes therapy sessions for this
        patient (the in-memory session repo doesn't track ``deleted_at``).
        Restoration is patient-only — see ``restore`` — sessions are
        not preserved across the in-memory delete/restore cycle in this
        repo. Production behavior is covered by the Postgres repo's
        cascade tests.
        """
        patient = self.get(patient_id, user_id)
        if not patient:
            return False

        # Cascade: delete associated therapy sessions. The in-memory
        # session repo does not track soft-delete state, so this matches
        # the legacy hard-delete semantics for tests; the Postgres repo
        # is the source of truth for the soft-delete cascade contract.
        if self._session_repo is not None and isinstance(
            self._session_repo, InMemoryTherapySessionRepository
        ):
            session_ids = [
                sid for sid, s in self._session_repo._sessions.items() if s.patient_id == patient_id
            ]
            for sid in session_ids:
                del self._session_repo._sessions[sid]

        self._deleted_at[patient_id] = utc_now()
        return True

    def list_recently_deleted(
        self,
        user_id: str,
        *,
        window_days: int = 30,
    ) -> list[tuple[Patient, datetime]]:
        """List soft-deleted patients still inside the undo window."""
        cutoff = utc_now() - timedelta(days=window_days)
        rows = [
            (self._patients[pid], stamp)
            for pid, stamp in self._deleted_at.items()
            if pid in self._patients and self._patients[pid].user_id == user_id and stamp > cutoff
        ]
        rows.sort(key=lambda pair: (pair[0].last_name_lower, pair[0].first_name_lower))
        return rows

    def restore(self, patient_id: str, user_id: str, *, window_days: int = 30) -> Patient | None:
        """Undo a soft-delete if still inside the undo window."""
        patient = self._patients.get(patient_id)
        if patient is None or patient.user_id != user_id:
            return None
        stamp = self._deleted_at.get(patient_id)
        if stamp is None:
            return None
        cutoff = utc_now() - timedelta(days=window_days)
        if stamp <= cutoff:
            return None
        del self._deleted_at[patient_id]
        return patient

    def close_chart(
        self, patient_id: str, user_id: str, closure_reason: str | None
    ) -> Patient | None:
        """Close a chart by stamping ``chart_closed_at`` (THERAPY-hek)."""
        patient = self.get(patient_id, user_id)
        if patient is None:
            return None
        patient.chart_closed_at = utc_now()
        patient.chart_closure_reason = closure_reason
        patient.updated_at = patient.chart_closed_at
        self._patients[patient.id] = patient
        return patient

    def reopen_chart(self, patient_id: str, user_id: str) -> Patient | None:
        """Reopen a previously-closed chart (THERAPY-hek)."""
        patient = self.get(patient_id, user_id)
        if patient is None:
            return None
        patient.chart_closed_at = None
        patient.chart_closure_reason = None
        patient.updated_at = utc_now()
        self._patients[patient.id] = patient
        return patient
