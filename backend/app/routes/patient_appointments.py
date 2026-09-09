# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a patient can see of their own calendar.

The first route in the tree that runs as a patient rather than as a
clinician, so a note on the shape it sets.

**The id comes from the principal, never from the request.** There is no
``{patient_id}`` in the path. A route shaped that way has an IDOR surface
by construction — it invites an id the caller controls and then depends on
somebody remembering to compare it. Reading the id off
:class:`~app.auth.patient_context.PatientContext` leaves nothing to
compare and nothing to forget.

**The response names its columns.** Row-level security is row-level: it
decides which rows this patient reaches and says nothing about which
columns, so the appointments row is readable by its own patient in full at
the database layer — clinician notes, visit coding, sync internals and all.
:class:`~app.models.scheduling.PatientAppointmentResponse` is the control
that stops those leaving the building, which is why it lists fields instead
of inheriting them.

**One principal per session.** This route only reads the patient's own
rows, so it runs entirely on the patient-armed session that
``get_patient_context`` set up, and never arms the clinician GUC beside it.
Routes that also need to see the whole diary — free-slot computation, and
the conflict checks inside a booking — must take a *separate*
owner-armed session for that part rather than widening this one.
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
from ..services.audit_service import AuditService, get_audit_service

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

router = APIRouter(prefix="/api/patient", tags=["patient-appointments"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]
Appointments = Annotated["AppointmentRepository", Depends(get_appointment_repository)]

# Spelled out rather than aliased like the two above: the route-audit
# guardrail reads parameter annotations looking for the tenant
# ``AuditService`` by name, and an alias hides it. Matching the shape every
# other audited route uses keeps the check honest instead of teaching it a
# new spelling.


def _to_patient_view(appointment: Appointment) -> PatientAppointmentResponse:
    """Project an appointment down to what its patient may see.

    Field-by-field rather than ``model_validate(appointment)``: a future
    column on the domain model must not become a response field by
    default.
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

    Not gated on the practice's self-booking policy. Whether a patient may
    *make* an appointment is a policy question; whether they may see the
    ones they already have is not, and a practice that has switched
    self-booking off has not thereby made its patients' own schedules
    secret from them.

    Exempt from the subscription gate, declared here rather than inherited:
    a patient is not the party who holds the practice's subscription, and
    knowing when your own appointment is should not be collateral damage of
    a lapsed one. That reasoning covers reading. It deliberately does not
    settle the write side — whether a patient may still BOOK into a lapsed
    practice's calendar is a different question, and the routes that do
    that must answer it themselves rather than inherit this line.
    """
    own = appointments.list_for_patient_principal(patient.patient_id)

    # Audited per row, not once per request. The audit answers "who saw
    # what", and a single "listed 9 appointments" entry cannot answer the
    # second half of that later.
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
