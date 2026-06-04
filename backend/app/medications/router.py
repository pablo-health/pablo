# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Medications API routes.

Provides CRUD endpoints for patient medication records:

* ``medications_router`` — ``/api/patients/{patient_id}/medications`` (create, list).
* ``patient_medications_router`` — ``/api/patients/{patient_id}/medications/{medication_id}``
  (update, delete).

Patient access is enforced by :func:`require_baa_acceptance` (authentication
gate) plus a repository-layer check via ``has_patient_access`` — the same
two-layer pattern used by the notes and outcome_measures routers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status

from ..api_errors import NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..repositories import get_medication_repository as _repo_factory
from .schemas import (
    CreateMedicationRequest,
    MedicationListResponse,
    MedicationResponse,
    UpdateMedicationRequest,
)
from .service import MedicationNotFoundError, MedicationService, PatientMedicationAccessError

if TYPE_CHECKING:
    from ..models import User
    from .repository import MedicationRepository

logger = logging.getLogger(__name__)

medications_router = APIRouter(
    prefix="/api/patients",
    tags=["medications"],
)


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def get_medication_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> MedicationRepository:
    """Return a tenant-scoped medication repository."""
    return _repo_factory()  # type: ignore[no-any-return]


def get_medication_service(
    repo: MedicationRepository = Depends(get_medication_repository),
) -> MedicationService:
    return MedicationService(repo)


# ---------------------------------------------------------------------------
# /api/patients/{patient_id}/medications endpoints
# ---------------------------------------------------------------------------


@medications_router.post(
    "/{patient_id}/medications",
    status_code=status.HTTP_201_CREATED,
    response_model=MedicationResponse,
)
def create_medication(
    patient_id: str,
    request: CreateMedicationRequest,
    user: User = Depends(require_baa_acceptance),
    service: MedicationService = Depends(get_medication_service),
) -> MedicationResponse:
    """Add a medication record for a patient.

    Returns the created medication with all fields populated.  Access-denied
    writes surface as 404 (not 403) to avoid leaking patient existence.
    """
    try:
        row = service.create(patient_id, user.id, request)
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
    except PatientMedicationAccessError as exc:
        raise NotFoundError("Patient not found", {"patient_id": patient_id}) from exc


@medications_router.get(
    "/{patient_id}/medications",
    response_model=MedicationListResponse,
)
def list_medications(
    patient_id: str,
    status: str | None = Query(
        default=None,
        description="Filter by status (active, discontinued, on_hold)",
    ),
    user: User = Depends(require_baa_acceptance),
    service: MedicationService = Depends(get_medication_service),
) -> MedicationListResponse:
    """List medications for a patient.

    Returns active records first, then discontinued/on_hold, within each
    group ordered by ``started_at`` descending.  Use ``?status=active`` to
    filter to a specific status.  Returns an empty list when the caller has
    no access grant for the patient.
    """
    rows = service.list_by_patient(patient_id, user.id, status=status)
    meds = [
        MedicationResponse(
            id=str(r["id"]),
            patient_id=str(r["patient_id"]),
            drug_name=str(r["drug_name"]),
            dose=str(r["dose"]),
            status=str(r["status"]),
            started_at=r.get("started_at"),  # type: ignore[arg-type]
            stopped_at=r.get("stopped_at"),  # type: ignore[arg-type]
            notes=str(r["notes"]) if r.get("notes") is not None else None,
            created_by=str(r["created_by"]),
            created_at=r["created_at"],  # type: ignore[arg-type]
            updated_at=r["updated_at"],  # type: ignore[arg-type]
        )
        for r in rows
    ]
    return MedicationListResponse(data=meds, total=len(meds))


@medications_router.patch(
    "/{patient_id}/medications/{medication_id}",
    response_model=MedicationResponse,
)
def update_medication(
    patient_id: str,
    medication_id: str,
    request: UpdateMedicationRequest,
    user: User = Depends(require_baa_acceptance),
    service: MedicationService = Depends(get_medication_service),
) -> MedicationResponse:
    """Partially update a medication record.

    When ``status`` is changed to ``"discontinued"`` and ``stopped_at`` is
    not provided, today's date is set automatically.
    """
    try:
        row = service.update(medication_id, user.id, request)
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
    except MedicationNotFoundError as exc:
        raise NotFoundError("Medication not found", {"medication_id": medication_id}) from exc


@medications_router.delete(
    "/{patient_id}/medications/{medication_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_medication(
    patient_id: str,
    medication_id: str,
    user: User = Depends(require_baa_acceptance),
    service: MedicationService = Depends(get_medication_service),
) -> None:
    """Soft-delete a medication record (sets ``deleted_at``; preserves the row)."""
    try:
        service.soft_delete(medication_id, user.id)
    except MedicationNotFoundError as exc:
        raise NotFoundError("Medication not found", {"medication_id": medication_id}) from exc
