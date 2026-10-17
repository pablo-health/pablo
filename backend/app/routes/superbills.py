# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Superbills: the itemised receipt a client files with their own insurer.

``GET /api/patients/{patient_id}/superbill?start=YYYY-MM-DD&end=YYYY-MM-DD``
renders the client's visits for the period into a PDF and streams it back;
nothing is stored. A visit a claim was built for prints from the claim, and
one with no claim from the appointment. The diagnosis is printed only when the
client's ``superbill_includes_diagnosis`` is on. With anything missing, the
answer is 422 carrying every finding and no document — see
:mod:`app.claims.superbill` for what is checked and why.

Access follows the claim routes: the client must be one the caller can see,
and an absent or ungranted client is **404, never 403**. Both outcomes are
audited as a disclosure-grade event with identifiers only — the period,
whether a diagnosis was printed, and the appointment, claim, line and charge
ids the document was rendered from, or the codes and field paths of what was
missing. Nothing clinical and nobody's name
reaches an audit payload, a log line or the download's filename.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from ..auth.service import require_baa_acceptance
from ..claims.assembly import billing_snapshot, client_snapshot
from ..claims.superbill import SuperbillRefusedError, build_superbill, render_superbill_pdf
from ..db import get_db_session
from ..models.audit import AuditAction, ResourceType
from ..models.claims import FindingResponse
from ..models.superbill import SuperbillRefusedResponse
from ..repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_claim_repository,
    get_clinician_profile_repository,
    get_patient_payment_repository,
    get_patient_repository,
    get_user_repository,
)
from ..scheduling_engine.services.rate_resolver import resolve_rate_cents
from ..services import AuditService, get_audit_service
from ..services.practice_billing_profile import load_billing_tax_id
from ..utcnow import utc_now
from ._deps import client_not_found, visible_patient
from .claims import _practice_timezone, get_billing_profile_loader
from .people_term import people_words_for

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..models import User
    from ..repositories.claims import ClaimRepository
    from ..repositories.clinician_profile import ClinicianProfileRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.appointment_type import AppointmentTypeRepository

router = APIRouter(prefix="/api/patients", tags=["superbills"])


def get_billing_tax_id_loader() -> str | None:
    """The practice's full tax id, read only when a superbill is rendered."""
    return load_billing_tax_id(get_db_session())


# String forward references inside ``Annotated``, as the claim routes do.
CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
ClaimsRepo = Annotated["ClaimRepository", Depends(get_claim_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
PaymentsRepo = Annotated["PatientPaymentRepository", Depends(get_patient_payment_repository)]
AppointmentsRepo = Annotated["AppointmentRepository", Depends(get_appointment_repository)]
AppointmentTypesRepo = Annotated[
    "AppointmentTypeRepository", Depends(get_appointment_type_repository)
]
ClinicianProfilesRepo = Annotated[
    "ClinicianProfileRepository", Depends(get_clinician_profile_repository)
]
UsersRepo = Annotated["UserRepository", Depends(get_user_repository)]
TaxId = Annotated[str | None, Depends(get_billing_tax_id_loader)]
BillingProfile = Annotated["Mapping[str, object]", Depends(get_billing_profile_loader)]


@router.get(
    "/{patient_id}/superbill",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}},
        422: {"model": SuperbillRefusedResponse},
    },
)
def generate_superbill(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    payments: PaymentsRepo,
    appointments: AppointmentsRepo,
    appointment_types: AppointmentTypesRepo,
    clinician_profiles: ClinicianProfilesRepo,
    users: UsersRepo,
    tax_id: TaxId,
    billing_profile: BillingProfile,
    start: Annotated[date, Query(description="First date of service, inclusive.")],
    end: Annotated[date, Query(description="Last date of service, inclusive.")],
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """The client's superbill for the period, as a PDF download."""
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The period's end date is before its start date.",
        )
    patient = visible_patient(patients, patient_id, user.id, client_not_found)
    people = people_words_for(user.id, users, clinician_profiles)
    generated_at = utc_now()
    includes_diagnosis = patient.superbill_includes_diagnosis

    def rate_for(appointment: Appointment) -> int | None:
        appointment_type = (
            appointment_types.get(appointment.appointment_type_id, user.id)
            if appointment.appointment_type_id is not None
            else None
        )
        return resolve_rate_cents(patient.rate_cents, appointment_type)

    try:
        superbill = build_superbill(
            patient_id=patient_id,
            period_start=start,
            period_end=end,
            patient=client_snapshot(patient),
            billing=billing_snapshot(billing_profile, clinician_profiles.get(user.id), user),
            claims=claims.list_by_patient(patient_id),
            charges=payments.list_charges(patient_id),
            appointments=appointments.list_by_patient(user.id, patient_id),
            rate_for=rate_for,
            includes_diagnosis=includes_diagnosis,
            timezone=_practice_timezone(users, user.id),
            tax_id=tax_id,
            license_for=clinician_profiles.get,
            generated_at=generated_at,
            people=people,
        )
    except SuperbillRefusedError as exc:
        findings = [
            FindingResponse(severity=f.severity, code=f.code, message=f.message, field=f.field)
            for f in exc.findings
        ]
        audit.log(
            AuditAction.SUPERBILL_REFUSED,
            user,
            request,
            resource_type=ResourceType.PATIENT,
            resource_id=patient_id,
            patient=patient,
            changes={
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "includes_diagnosis": includes_diagnosis,
                "findings": [{"code": f.code, "field": f.field} for f in findings],
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=SuperbillRefusedResponse(message=str(exc), findings=findings).model_dump(),
        ) from exc

    pdf = render_superbill_pdf(superbill, people)
    audit.log(
        AuditAction.SUPERBILL_GENERATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        patient=patient,
        changes={
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "includes_diagnosis": includes_diagnosis,
            "appointment_ids": list(superbill.appointment_ids),
            "claim_ids": list(superbill.claim_ids),
            "claim_line_ids": list(superbill.line_ids),
            "charge_ids": list(superbill.charge_ids),
        },
    )
    filename = f"superbill-{start.isoformat()}-to-{end.isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
