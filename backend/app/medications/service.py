# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Business logic for the medications API.

Orchestrates validation, persistence (via the repository), and read-path
filtering (soft-delete exclusion, status filter, active-first ordering).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .repository import PatientMedicationAccessDeniedError
from .schemas import MedicationResponse

if TYPE_CHECKING:
    from .repository import MedicationRepository
    from .schemas import CreateMedicationRequest, UpdateMedicationRequest


class MedicationNotFoundError(LookupError):
    """Raised when a medication row cannot be found or is inaccessible."""


class PatientMedicationAccessError(LookupError):
    """Raised when the caller has no grant for the requested patient.

    Surfaced as "not found" (HTTP 404) so the API can't be used as a
    patient-existence oracle — mirrors the notes / outcome_measures pattern.
    """


class MedicationService:
    """Read/write operations for patient medications."""

    def __init__(self, repo: MedicationRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_response(row: dict[str, object]) -> MedicationResponse:
        """Build an API response from a repository row dict."""
        return MedicationResponse(
            id=str(row["id"]),
            patient_id=str(row["patient_id"]),
            drug_name=str(row["drug_name"]),
            dose=str(row["dose"]),
            status=str(row["status"]),
            started_at=row.get("started_at"),  # type: ignore[arg-type]
            stopped_at=row.get("stopped_at"),  # type: ignore[arg-type]
            notes=str(row["notes"]) if row.get("notes") is not None else None,
            created_by=str(row["created_by"]),
            created_at=row["created_at"],  # type: ignore[arg-type]
            updated_at=row["updated_at"],  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create(
        self,
        patient_id: str,
        user_id: str,
        req: CreateMedicationRequest,
    ) -> dict[str, object]:
        """Create and persist a new medication record.

        Returns the saved row dict.

        Raises :class:`PatientMedicationAccessError` when the caller has no
        access grant for the patient — surfaced as 404 at the route layer.
        """
        now = utc_now()
        row: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "patient_id": patient_id,
            "drug_name": req.drug_name,
            "dose": req.dose,
            "status": req.status,
            "started_at": req.started_at,
            "stopped_at": None,
            "notes": req.notes,
            "created_by": user_id,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        try:
            return self._repo.create(row, user_id)
        except PatientMedicationAccessDeniedError as exc:
            raise PatientMedicationAccessError(patient_id) from exc

    def update(
        self,
        med_id: str,
        user_id: str,
        req: UpdateMedicationRequest,
    ) -> dict[str, object]:
        """Apply a partial update to a medication record.

        Automatically sets ``stopped_at`` to today when ``status`` changes
        to ``"discontinued"`` and no explicit ``stopped_at`` is given.

        Raises :class:`MedicationNotFoundError` when not found or inaccessible.
        """
        existing = self._repo.get(med_id, user_id)
        if existing is None or existing.get("deleted_at") is not None:
            raise MedicationNotFoundError(med_id)

        if req.drug_name is not None:
            existing["drug_name"] = req.drug_name
        if req.dose is not None:
            existing["dose"] = req.dose
        if req.status is not None:
            old_status = existing.get("status")
            existing["status"] = req.status
            # Auto-set stopped_at when transitioning to discontinued
            if req.status == "discontinued" and old_status != "discontinued":
                auto = req.stopped_at if req.stopped_at is not None else date.today()
                existing["stopped_at"] = auto
        if req.started_at is not None:
            existing["started_at"] = req.started_at
        if req.stopped_at is not None:
            existing["stopped_at"] = req.stopped_at
        if req.notes is not None:
            existing["notes"] = req.notes

        existing["updated_at"] = utc_now()

        try:
            return self._repo.update(existing, user_id)
        except PatientMedicationAccessDeniedError as exc:
            raise MedicationNotFoundError(med_id) from exc

    def soft_delete(self, med_id: str, user_id: str) -> None:
        """Soft-delete a medication record (sets ``deleted_at``).

        Raises :class:`MedicationNotFoundError` if not found or inaccessible.
        """
        existing = self._repo.get(med_id, user_id)
        if existing is None or existing.get("deleted_at") is not None:
            raise MedicationNotFoundError(med_id)
        try:
            self._repo.soft_delete(med_id, user_id)
        except PatientMedicationAccessDeniedError as exc:
            raise MedicationNotFoundError(med_id) from exc

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_by_patient(
        self,
        patient_id: str,
        user_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        """List live medications for a patient.

        Returns active records first, then discontinued/on_hold, each group
        ordered by ``started_at`` descending (nulls last).  Respects the
        optional ``status`` filter.  Returns an empty list when the caller
        has no access grant for the patient.
        """
        return self._repo.list_by_patient(patient_id, user_id, status=status)
