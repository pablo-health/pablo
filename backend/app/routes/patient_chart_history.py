# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart-history API: a client's history fields, as the chart records them.

* ``GET    /api/patients/{patient_id}/chart-history`` — every field, recorded
  or not, with the values each held before.
* ``PUT    /api/patients/{patient_id}/chart-history/{key}`` — record a value,
  from the chart or from a note (``source_note_id``).
* ``DELETE /api/patients/{patient_id}/chart-history/{key}`` — remove a value
  that was never true. The field's history keeps it.

Every route loads the patient first, which is the access check (an
inaccessible patient is a 404), and audits by field key only, never text.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import NotFoundError
from ..auth.service import require_baa_acceptance
from ..chart_history.dependencies import get_chart_history_service
from ..chart_history.schemas import (
    ChartHistoryResponse,
    HistoryFieldResponse,
    SetHistoryFieldRequest,
    history_response,
)
from ..chart_history.service import (
    ChartHistoryService,
    HistoryFieldEmptyError,
    UnknownHistoryFieldError,
)
from ..models import AuditAction, Patient, User
from ..repositories import PatientRepository  # noqa: TC001 — FastAPI resolves at runtime
from ..services import AuditService, get_audit_service
from .patients import get_patient_repository

router = APIRouter(prefix="/api/patients", tags=["chart-history"])


def _patient_or_404(repo: PatientRepository, patient_id: str, user: User) -> Patient:
    patient = repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})
    return patient


def _history_change(change: str, key: str) -> dict[str, object]:
    return {"changed_fields": ["chart_history"], "chart_history": change, "field_key": key}


@router.get("/{patient_id}/chart-history", response_model=ChartHistoryResponse)
def get_chart_history(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ChartHistoryService = Depends(get_chart_history_service),
    audit: AuditService = Depends(get_audit_service),
) -> ChartHistoryResponse:
    patient = _patient_or_404(patients, patient_id, user)
    response = history_response(service.entries(patient.id), service.revisions(patient.id))
    audit.log_patient_action(
        AuditAction.PATIENT_VIEWED,
        user,
        http_request,
        patient,
        changes={"viewed": "chart_history"},
    )
    return response


@router.put("/{patient_id}/chart-history/{key}", response_model=HistoryFieldResponse)
def set_chart_history_field(
    patient_id: str,
    key: str,
    body: SetHistoryFieldRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ChartHistoryService = Depends(get_chart_history_service),
    audit: AuditService = Depends(get_audit_service),
) -> HistoryFieldResponse:
    patient = _patient_or_404(patients, patient_id, user)
    try:
        service.set(patient.id, key, user.id, body.text, body.source_note_id)
    except UnknownHistoryFieldError as exc:
        raise NotFoundError("No such history field", {"key": key}) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_history_change("set", key),
    )
    return _field(service, patient.id, key)


@router.delete(
    "/{patient_id}/chart-history/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_chart_history_field(
    patient_id: str,
    key: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ChartHistoryService = Depends(get_chart_history_service),
    audit: AuditService = Depends(get_audit_service),
) -> None:
    patient = _patient_or_404(patients, patient_id, user)
    try:
        service.remove(patient.id, key, user.id)
    except UnknownHistoryFieldError as exc:
        raise NotFoundError("No such history field", {"key": key}) from exc
    except HistoryFieldEmptyError as exc:
        raise NotFoundError("Nothing is recorded in that field", {"key": key}) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_history_change("removed", key),
    )


def _field(service: ChartHistoryService, patient_id: str, key: str) -> HistoryFieldResponse:
    """The field as a read would return it, its source note's date included."""
    response = history_response(service.entries(patient_id), service.revisions(patient_id))
    return next(f for g in response.groups for f in g.fields if f.key == key)
