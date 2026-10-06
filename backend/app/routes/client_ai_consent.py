# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's answer about AI-assisted notes, on the chart.

``GET /api/patients/{patient_id}/ai-consent`` returns the current answer and
the history behind it (:class:`AiConsentRecord`). ``POST`` to the same path
records a clinician's entry: the client agreed or declined, on a given day.

An answer applies to every session until the client gives a different one.
Recording a new one appends rather than editing, so the history survives.

A client the caller cannot see is 404, never 403, like every chart route.
Both directions are audited: the write names the decision, the read records
that the answer was looked at.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.service import require_baa_acceptance
from ..models.audit import AuditAction
from ..models.client_ai_consent import AiConsentRecord, RecordAiConsentRequest
from ..repositories import get_client_ai_consent_repository, get_patient_repository
from ..services import AuditService, get_audit_service
from ..services.client_ai_consent import (
    FutureAiConsentDateError,
    ai_consent_record,
    record_ai_consent,
)
from .patient_payments import _require_patient
from .scheduling import get_owner_timezone

if TYPE_CHECKING:
    from ..models import User
    from ..repositories.client_ai_consent import ClientAiConsentRepository
    from ..repositories.patient import PatientRepository

router = APIRouter(prefix="/api/patients", tags=["patients"])

ConsentRepo = Annotated["ClientAiConsentRepository", Depends(get_client_ai_consent_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
ClinicianTimezone = Annotated[tzinfo, Depends(get_owner_timezone)]


@router.get("/{patient_id}/ai-consent", response_model=AiConsentRecord)
def get_ai_consent(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    patients: PatientsRepo,
    consents: ConsentRepo,
    audit: AuditService = Depends(get_audit_service),
) -> AiConsentRecord:
    """The client's current answer, or ``null`` if nobody has asked, and the history."""
    patient = _require_patient(patients, patient_id, user.id)
    record = ai_consent_record(patient_id, repo=consents)
    audit.log_patient_action(
        AuditAction.PATIENT_AI_CONSENT_VIEWED,
        user,
        request,
        patient,
        changes={"entries": len(record.history)},
    )
    return record


@router.post(
    "/{patient_id}/ai-consent",
    response_model=AiConsentRecord,
    status_code=status.HTTP_201_CREATED,
)
def post_ai_consent(
    patient_id: str,
    payload: RecordAiConsentRequest,
    request: Request,
    user: CurrentUser,
    patients: PatientsRepo,
    consents: ConsentRepo,
    tz: ClinicianTimezone,
    audit: AuditService = Depends(get_audit_service),
) -> AiConsentRecord:
    """Record that the client agreed or declined, and return the updated record.

    ``effective_on`` defaults to today and is refused with a 400 when it is
    after today. "Today" is the clinician's own calendar day, from their
    timezone preference, so an answer recorded in the evening is not dated
    tomorrow.
    """
    patient = _require_patient(patients, patient_id, user.id)
    today = datetime.now(tz).date()
    try:
        event = record_ai_consent(
            patient_id,
            payload.decision,
            payload.effective_on or today,
            "clinician",
            user.id,
            modality=payload.modality,
            client_stated_location=payload.client_stated_location,
            consented_by=payload.consented_by,
            today=today,
            repo=consents,
        )
    except FutureAiConsentDateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The date can't be in the future.",
        ) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_AI_CONSENT_RECORDED,
        user,
        request,
        patient,
        changes={
            "event_id": event.id,
            "decision": event.decision,
            "effective_on": event.effective_on.isoformat(),
            "source": event.source,
            "modality": event.modality,
            "consented_by": event.consented_by,
            # Whether a place was given, never the place: the record holds it.
            "client_stated_location": event.client_stated_location is not None,
        },
    )
    return ai_consent_record(patient_id, repo=consents)
