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
        return MedicationService._build_response(row)
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
    meds = [MedicationService._build_response(r) for r in rows]
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
        return MedicationService._build_response(row)
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
