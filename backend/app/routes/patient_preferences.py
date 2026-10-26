# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-preference API: how to work with a client, on the client's record.

* ``GET  /api/patients/{patient_id}/preferences`` — active preferences;
  ``?include_retired=true`` adds the retired ones.
* ``POST /api/patients/{patient_id}/preferences`` — add one. A kind that allows
  one active row per client, and has one, is refused with
  ``409 PREFERENCE_ALREADY_SET`` and the id of that row, to be edited instead.
* ``PATCH /api/patients/{patient_id}/preferences/{preference_id}`` — restate it.
* ``POST /api/patients/{patient_id}/preferences/{preference_id}/retire`` — retire
  it. The row is kept; there is no delete.

A write that does not fit its kind is ``422 INVALID_PREFERENCE`` with the reason.
Every route loads the patient first, which is the access check (an
inaccessible patient is a 404), and audits by id and kind only, never the
words or the value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import require_baa_acceptance
from ..models import AuditAction, User
from ..preferences.dependencies import get_preference_service
from ..preferences.kinds import InvalidPreferenceError
from ..preferences.schemas import (
    AddPreferenceRequest,
    PreferenceListResponse,
    PreferenceResponse,
    UpdatePreferenceRequest,
)
from ..preferences.service import (
    DuplicatePreferenceError,
    PreferenceNotFoundError,
    PreferenceService,
)
from ..services import AuditService, get_audit_service
from ._deps import patient_not_found, visible_patient
from .patients import get_patient_repository

if TYPE_CHECKING:
    from ..repositories import PatientRepository

router = APIRouter(prefix="/api/patients", tags=["preferences"])


def _invalid(exc: InvalidPreferenceError) -> UnprocessableEntityError:
    return UnprocessableEntityError(str(exc), code="INVALID_PREFERENCE")


def _not_found(preference_id: str) -> NotFoundError:
    return NotFoundError("Preference not found", {"preference_id": preference_id})


def _change(change: str, preference_id: str, kind: str, fields: list[str]) -> dict[str, object]:
    """Audit detail for a write: what happened, to which row and kind, by name only."""
    return {
        "changed_fields": ["preferences"],
        "preferences": change,
        "preference_id": preference_id,
        "preference_kind": kind,
        "preference_fields": fields,
    }


@router.get("/{patient_id}/preferences", response_model=PreferenceListResponse)
def list_preferences(
    patient_id: str,
    http_request: Request,
    include_retired: bool = False,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: PreferenceService = Depends(get_preference_service),
    audit: AuditService = Depends(get_audit_service),
) -> PreferenceListResponse:
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    rows = service.preferences(patient.id, include_retired=include_retired)
    audit.log_patient_action(
        AuditAction.PATIENT_VIEWED,
        user,
        http_request,
        patient,
        changes={"viewed": "preferences", "count": len(rows)},
    )
    return PreferenceListResponse(
        data=[PreferenceResponse.from_preference(p) for p in rows], total=len(rows)
    )


@router.post(
    "/{patient_id}/preferences",
    status_code=status.HTTP_201_CREATED,
    response_model=PreferenceResponse,
)
def add_preference(
    patient_id: str,
    body: AddPreferenceRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: PreferenceService = Depends(get_preference_service),
    audit: AuditService = Depends(get_audit_service),
) -> PreferenceResponse:
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    try:
        preference = service.add(patient.id, user.id, body)
    except InvalidPreferenceError as exc:
        raise _invalid(exc) from exc
    except DuplicatePreferenceError as exc:
        raise ConflictError(
            "There is one already. Edit it instead.",
            {"preference_id": exc.existing.id},
            code="PREFERENCE_ALREADY_SET",
        ) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_change("added", preference.id, preference.kind, sorted(body.model_fields_set)),
    )
    return PreferenceResponse.from_preference(preference)


@router.patch("/{patient_id}/preferences/{preference_id}", response_model=PreferenceResponse)
def update_preference(
    patient_id: str,
    preference_id: str,
    body: UpdatePreferenceRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: PreferenceService = Depends(get_preference_service),
    audit: AuditService = Depends(get_audit_service),
) -> PreferenceResponse:
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    try:
        preference = service.update(patient.id, preference_id, body)
    except PreferenceNotFoundError as exc:
        raise _not_found(preference_id) from exc
    except InvalidPreferenceError as exc:
        raise _invalid(exc) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_change("updated", preference.id, preference.kind, sorted(body.model_fields_set)),
    )
    return PreferenceResponse.from_preference(preference)


@router.post("/{patient_id}/preferences/{preference_id}/retire", response_model=PreferenceResponse)
def retire_preference(
    patient_id: str,
    preference_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: PreferenceService = Depends(get_preference_service),
    audit: AuditService = Depends(get_audit_service),
) -> PreferenceResponse:
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    try:
        preference = service.retire(patient.id, preference_id, user.id)
    except PreferenceNotFoundError as exc:
        raise _not_found(preference_id) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_change("retired", preference.id, preference.kind, ["retired_at"]),
    )
    return PreferenceResponse.from_preference(preference)
