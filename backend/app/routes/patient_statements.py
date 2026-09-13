# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Statements: what a client owes, as a document they can be handed.

``GET /api/patients/{patient_id}/statement`` renders the client's whole
ledger into a PDF and streams it back; nothing is stored, and nothing is
sent anywhere — a practice that emails statements does it through its own
channels, from the file this returns.

Deliberately NOT gated on the card processor, matching the balance route it
shares its arithmetic with: a practice that bills insurance and takes no
cards at all still has clients who owe it money, and refusing to print the
figure because there is no way to charge a card would be answering a
question nobody asked.

Access follows the other patient routes: the client must be one the caller
can see, and an absent or ungranted client is **404, never 403**. The
download is audited as a disclosure with identifiers only — the ledger rows
the document totalled and the balance it printed. No name reaches an audit
payload, a log line or the filename.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, Response

from ..auth.service import require_baa_acceptance
from ..db import get_db_session
from ..models.audit import AuditAction, ResourceType
from ..payments.balance import outcome_is_known
from ..payments.statement import PracticeBlock, build_statement, render_statement_pdf
from ..repositories import (
    get_appointment_repository,
    get_claim_repository,
    get_patient_coverage_repository,
    get_patient_payment_repository,
    get_patient_repository,
    get_payer_repository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from ..services.practice_billing_profile import load_billing_profile
from ..utcnow import utc_now
from .claims import _practice_timezone, _require_patient

if TYPE_CHECKING:
    from ..models import User
    from ..repositories.claims import ClaimRepository
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

router = APIRouter(prefix="/api/patients", tags=["statements"])


def get_practice_block() -> PracticeBlock:
    """The practice's own name, address and phone, as the statement prints it.

    Read from the billing profile the claims surface already keeps, so a
    practice fills its identity in once. The tax id is not read at all — the
    encrypted value stays encrypted, because nobody needs an EIN to pay a
    bill.
    """
    profile = load_billing_profile(get_db_session())
    return PracticeBlock(
        name=_text(profile.get("legal_name")),
        address_line1=_text(profile.get("address_line1")),
        address_line2=_text(profile.get("address_line2")),
        city=_text(profile.get("city")),
        state=_text(profile.get("state")),
        postal_code=_text(profile.get("postal_code")),
        phone=_text(profile.get("phone")),
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
PaymentsRepo = Annotated["PatientPaymentRepository", Depends(get_patient_payment_repository)]
ClaimsRepo = Annotated["ClaimRepository", Depends(get_claim_repository)]
AppointmentsRepo = Annotated["AppointmentRepository", Depends(get_appointment_repository)]
UsersRepo = Annotated["UserRepository", Depends(get_user_repository)]
Practice = Annotated[PracticeBlock, Depends(get_practice_block)]
CoverageRepo = Annotated["PatientCoverageRepository", Depends(get_patient_coverage_repository)]
PayersRepo = Annotated["PayerRepository", Depends(get_payer_repository)]


@router.get(
    "/{patient_id}/statement",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def generate_statement(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    patients: PatientsRepo,
    payments: PaymentsRepo,
    claims: ClaimsRepo,
    appointments: AppointmentsRepo,
    users: UsersRepo,
    practice: Practice,
    coverage: CoverageRepo,
    payers: PayersRepo,
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """The client's statement, as a PDF download."""
    patient = _require_patient(patients, patient_id, user.id)
    charges = payments.list_charges(patient_id)
    active = coverage.get_active(patient_id)
    statement = build_statement(
        patient_id=patient_id,
        client_name=patient.display_name,
        charges=charges,
        claims=claims.list_by_patient(patient_id),
        appointments=appointments.list_by_patient(user.id, patient_id),
        practice=practice,
        timezone=_practice_timezone(users, user.id),
        generated_at=utc_now(),
        outcome_known=outcome_is_known(payers.get(active.payer_id) if active is not None else None),
    )
    pdf = render_statement_pdf(statement)

    audit.log(
        AuditAction.STATEMENT_GENERATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        patient=patient,
        changes={
            "charge_ids": list(statement.charge_ids),
            "balance_cents": statement.balance_cents,
        },
    )
    # The client id is already on the audit row above; the filename is what
    # lands in a downloads folder and carries nothing.
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="statement.pdf"'},
    )
