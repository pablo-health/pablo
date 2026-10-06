# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``PUT /api/patients/{patient_id}/allergies``: record a client's allergies.

The whole record is replaced in one write — a list, "no known drug
allergies", or back to "not recorded" — so the three states can never be
half-set. Read back on the patient itself (``allergy_status``, ``allergies``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from ..api_errors import NotFoundError
from ..auth.service import require_baa_acceptance
from ..models import AuditAction, PatientResponse, User
from ..models.patient import UpdateAllergiesRequest  # noqa: TC001 — runtime annotation (body)
from ..services import AuditService, get_audit_service
from .patients import get_patient_repository

if TYPE_CHECKING:
    from ..repositories import PatientRepository

router = APIRouter(prefix="/api/patients", tags=["patients"])


@router.put("/{patient_id}/allergies", response_model=PatientResponse)
def update_allergies(
    patient_id: str,
    body: UpdateAllergiesRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientResponse:
    patient = repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    patient.allergy_status = body.status
    patient.allergies = [entry.model_dump(exclude_none=True) for entry in body.allergies]
    patient = repo.update(patient)
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes={"changed_fields": ["allergies"]},
    )
    return PatientResponse.from_patient(patient)
