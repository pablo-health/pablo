# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The unbilled-sessions queue — Billing's top-level working surface.

Billing used to be a page inside Settings, which meant the nav item never
landed a clinician anywhere they'd actually work. This is the surface it
should have been from the start: sessions that happened (a finalized note
exists) and were never successfully charged, newest first, each linking back
to the session where the charge action already lives.

"Unbilled" is derived at read time from the note's ``finalized_at`` and the
charge ledger's ``status`` — there is no stored billed/unbilled flag to drift
out of sync with either. A session is in the queue when its note is finalized
and, if it has an appointment, that appointment carries no ``succeeded``
charge. A finalized session with no appointment at all has nothing to check
against and stays in the queue too — there is no ledger entry that could ever
clear it, which correctly reflects that nobody has recorded charging for it.

The amount shown is the resolved rate a charge would use today (client
override, else appointment-type default), the same resolution the charge
action itself applies — not a stored or historical figure, so it can move if
the rate changes before the clinician acts on the row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from ..auth.service import require_baa_acceptance
from ..db.models import DEFAULT_CHARGE_CURRENCY
from ..models import AuditAction, User
from ..models.billing_queue import UnbilledQueueResponse, UnbilledSessionItem
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
    get_appointment_repository,
    get_appointment_type_repository,
    get_patient_payment_repository,
)
from ..scheduling_engine.services.rate_resolver import resolve_rate_cents
from ..services import AuditService, get_audit_service
from .sessions import get_notes_repository, get_patient_repository, get_session_repository

if TYPE_CHECKING:
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.appointment_type import AppointmentTypeRepository

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/unbilled-sessions", response_model=UnbilledQueueResponse)
def get_unbilled_sessions(
    request: Request,
    user: User = Depends(require_baa_acceptance),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    appointment_repo: AppointmentRepository = Depends(get_appointment_repository),
    appointment_type_repo: AppointmentTypeRepository = Depends(get_appointment_type_repository),
    payments_repo: PatientPaymentRepository = Depends(get_patient_payment_repository),
    audit: AuditService = Depends(get_audit_service),
) -> UnbilledQueueResponse:
    notes = notes_repo.list_finalized(user.id)
    if not notes:
        return UnbilledQueueResponse(items=[])

    session_ids = [note.session_id for note in notes if note.session_id is not None]
    sessions = session_repo.get_multiple(session_ids, user.id)
    appointments_by_session = appointment_repo.get_by_session_ids(session_ids, user.id)

    appointment_ids = [a.id for a in appointments_by_session.values()]
    succeeded_appointment_ids = payments_repo.succeeded_appointment_ids(appointment_ids)

    patient_ids = list({s.patient_id for s in sessions.values()})
    patients = patient_repo.get_multiple(patient_ids, user.id)
    appointment_types = {t.id: t for t in appointment_type_repo.list_by_user(user.id)}

    items: list[UnbilledSessionItem] = []
    for note in notes:
        if note.session_id is None:
            continue
        session = sessions.get(note.session_id)
        if session is None:
            continue

        appointment = appointments_by_session.get(note.session_id)
        if appointment is not None and appointment.id in succeeded_appointment_ids:
            continue

        patient = patients.get(session.patient_id)
        appointment_type = None
        if appointment is not None and appointment.appointment_type_id is not None:
            appointment_type = appointment_types.get(appointment.appointment_type_id)
        amount_cents = resolve_rate_cents(
            patient.rate_cents if patient is not None else None, appointment_type
        )

        items.append(
            UnbilledSessionItem(
                session_id=session.id,
                patient_id=session.patient_id,
                patient_name=patient.display_name if patient is not None else "Unknown",
                session_date=session.session_date,
                amount_cents=amount_cents,
                currency=DEFAULT_CHARGE_CURRENCY,
            )
        )
        audit.log_session_action(AuditAction.SESSION_VIEWED, user, request, session, patient)

    return UnbilledQueueResponse(items=items)
