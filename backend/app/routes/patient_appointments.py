# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a patient can see of their own calendar.

The first route that runs as a patient rather than a clinician, so it sets the
shape:

* **The id comes from the principal, never from the request.** A
  ``{patient_id}`` in the path is an IDOR surface by construction; reading it
  off :class:`~app.auth.patient_context.PatientContext` leaves nothing to
  compare and nothing to forget.
* **The response names its columns.** Row-level security decides which rows a
  patient reaches, not which columns, so
  :class:`~app.models.scheduling.PatientAppointmentResponse` lists fields
  rather than inheriting them.
* **One principal per session.** This route reads only the patient's own rows
  on the patient-armed session. Routes that also need the whole diary must
  take a separate owner-armed session rather than widening this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request

from ..auth.patient_context import PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..models.audit import AuditAction, ResourceType
from ..models.scheduling import (
    PatientAppointmentListResponse,
    PatientAppointmentResponse,
)
from ..repositories import get_appointment_repository
from ..scheduling_engine.repositories.appointment import AppointmentRepository
from ..services.audit_service import AuditService, get_audit_service

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment

router = APIRouter(prefix="/api/patient", tags=["patient-appointments"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]
# A runtime expression, so ``AppointmentRepository`` cannot live under
# TYPE_CHECKING; ``Appointment`` can, since it only appears in annotations.
Appointments = Annotated[AppointmentRepository, Depends(get_appointment_repository)]

# ``AuditService`` is spelled out in the route signature rather than aliased:
# the route-audit guardrail matches the parameter annotation by name.


def _to_patient_view(appointment: Appointment) -> PatientAppointmentResponse:
    """Project an appointment down to what its patient may see.

    Field-by-field, so a new column on the domain model never becomes a
    response field by default.
    """
    return PatientAppointmentResponse(
        id=appointment.id,
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        duration_minutes=appointment.duration_minutes,
        status=appointment.status,
        session_type=appointment.session_type,
        video_link=appointment.video_link,
        video_platform=appointment.video_platform,
        recurrence_rule=appointment.recurrence_rule,
        recurring_appointment_id=appointment.recurring_appointment_id,
        # Shown here too, so a patient can see which past cancellations
        # counted as late.
        late_cancellation=appointment.late_cancellation,
    )


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

    data = [_to_patient_view(a) for a in own]
    return PatientAppointmentListResponse(data=data, total=len(data))
