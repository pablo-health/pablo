# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a patient can see of their own calendar.

The first route that runs as a patient rather than a clinician, so it sets the
shape:

* **The id comes from the principal, never from the request.** A
  ``{patient_id}`` in the path is an IDOR surface by construction; reading it
  off :class:`~app.auth.patient_context.PatientContext` leaves nothing to
  compare and nothing to forget.
* **The read names its columns, and so does the response.** Row-level security
  decides which rows a patient reaches, not which columns, so the repository
  selects the columns rather than the row and
  :class:`~app.models.patient_facing.PatientAppointmentResponse` lists fields
  rather than inheriting them.
* **One principal per session.** This route reads only the patient's own rows
  on the patient-armed session. Routes that also need the whole diary must
  take a separate owner-armed session rather than widening this one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..auth.patient_context import PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..models.audit import AuditAction, ResourceType
from ..models.scheduling import PatientAppointmentListResponse
from ..repositories import get_appointment_repository
from ..scheduling_engine.repositories.appointment import AppointmentRepository
from ..services.audit_service import AuditService, get_audit_service

router = APIRouter(prefix="/api/patient", tags=["patient-appointments"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]
# A runtime expression, so ``AppointmentRepository`` cannot live under
# TYPE_CHECKING.
Appointments = Annotated[AppointmentRepository, Depends(get_appointment_repository)]

# ``AuditService`` is spelled out in the route signature rather than aliased:
# the route-audit guardrail matches the parameter annotation by name.


@router.get("/appointments", response_model=PatientAppointmentListResponse)
def list_my_appointments(
    request: Request,
    patient: CurrentPatient,
    appointments: Appointments,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientAppointmentListResponse:
    """Every appointment belonging to the calling patient, soonest first.

    Not gated on the self-booking policy: whether a patient may make an
    appointment is a policy question; seeing the ones they have is not.

    Exempt from the subscription gate: a patient does not hold the practice's
    subscription. That covers reading only; whether a patient may still book
    into a lapsed practice is for the booking routes to decide.
    """
    # Already projected: the repository selects the patient-facing columns, so
    # the staff-authored ones are never read here, let alone serialised.
    own = appointments.list_for_patient_principal(patient.patient_id)

    # Per row, not per request: the audit answers "who saw what".
    for appointment in own:
        audit.log_patient_principal_action(
            action=AuditAction.APPOINTMENT_VIEWED,
            request=request,
            patient_id=patient.patient_id,
            resource_type=ResourceType.APPOINTMENT,
            resource_id=appointment.id,
        )

    return PatientAppointmentListResponse(data=own, total=len(own))
