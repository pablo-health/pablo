# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Write-offs: money a practice decides not to collect.

``POST /api/patients/{patient_id}/write-offs`` adds one ``write_off`` row to
the client's ledger. Three gates keep it from being an unlogged discount:

* ``reason`` must be one of ``app.db.models.WRITE_OFF_REASONS`` — the fixed
  set the CHECK constraint enforces.
* ``courtesy`` is refused unless the practice has opted into courtesy
  waivers on its billing profile; ``small_balance`` is refused unless the
  client's current balance is at or under the practice's own threshold.
* The amount can never exceed the balance — a write-off reduces a debt, it
  does not manufacture a credit.

Per client, per amount, never a list: there is no bulk write-off route, and
writing off many clients' balances in one motion is a decision that deserves
a slower path than a checkbox.

Access and audit follow the rest of this route family: an unseen client is
404, never 403, and every write-off is a disclosure-grade audit event —
actor, reason, amount, and the claim ids the balance was standing against.
Never a diagnosis, never a payer's member id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..auth.service import require_baa_acceptance
from ..db import get_db_session
from ..db.models import DEFAULT_CHARGE_CURRENCY, WRITE_OFF_REASONS
from ..models.audit import AuditAction, ResourceType
from ..models.payments import ChargeResponse, CreateWriteOffRequest
from ..payments.balance import patient_balance
from ..repositories import get_patient_payment_repository, get_patient_repository
from ..services import AuditService, get_audit_service
from ..services.practice_billing_profile import load_billing_profile
from .patient_payments import _require_patient, _to_charge_response

if TYPE_CHECKING:
    from ..models import User
    from ..models.payments import PatientCharge
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_payment import PatientPaymentRepository

router = APIRouter(prefix="/api/patients", tags=["patient-payments"])

PaymentsRepo = Annotated["PatientPaymentRepository", Depends(get_patient_payment_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
CurrentUser = Annotated["User", Depends(require_baa_acceptance)]


class WriteOffPolicy(BaseModel):
    """The two practice-level switches a write-off is checked against."""

    allow_courtesy_writeoffs: bool = False
    small_balance_cents: int = 500


def get_write_off_policy() -> WriteOffPolicy:
    """The practice's waiver policy, read from its billing profile.

    A dependency of its own — like ``patient_statements.get_practice_block``
    beside it — so a test can override it directly rather than stand up a
    database session for one route.
    """
    profile = load_billing_profile(get_db_session())
    small_balance_cents = profile.get("small_balance_cents")
    return WriteOffPolicy(
        allow_courtesy_writeoffs=bool(profile.get("allow_courtesy_writeoffs")),
        small_balance_cents=small_balance_cents if isinstance(small_balance_cents, int) else 0,
    )


Policy = Annotated[WriteOffPolicy, Depends(get_write_off_policy)]


def _outstanding_claim_ids(ledger: list[PatientCharge]) -> list[str]:
    """The claims behind this client's ledger, for the audit trail.

    Every distinct claim id on the ledger, not an attempt to allocate this
    write-off to one bill in particular: a write-off is a lump sum against
    the whole balance, and the disclosure names every claim the balance could
    have come from rather than guessing which one it was.
    """
    return sorted({charge.claim_id for charge in ledger if charge.claim_id is not None})


@router.post("/{patient_id}/write-offs", response_model=ChargeResponse)
def create_write_off(
    patient_id: str,
    payload: CreateWriteOffRequest,
    request: Request,
    user: CurrentUser,
    payments: PaymentsRepo,
    patients: PatientsRepo,
    policy: Policy,
    audit: AuditService = Depends(get_audit_service),
) -> ChargeResponse:
    """Write off part or all of a client's balance, with a stated reason.

    404 for a client the caller cannot see, matching every other route in
    this family. 422 for a reason outside the fixed set, or for an amount
    that would take the write-off past what is actually owed. 403 for a
    reason practice policy has not opted into.
    """
    _require_patient(patients, patient_id, user.id)

    if payload.reason not in WRITE_OFF_REASONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown write-off reason: {payload.reason!r}.",
        )

    ledger = payments.list_charges(patient_id)
    balance_cents = patient_balance(ledger).balance_cents

    if payload.reason == "courtesy" and not policy.allow_courtesy_writeoffs:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Courtesy write-offs are turned off for this practice. Turn them "
            "on in Settings > Billing before writing one off this way.",
        )
    if payload.reason == "small_balance" and balance_cents > policy.small_balance_cents:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This client's balance is above the practice's small-balance "
            "write-off threshold.",
        )
    if payload.amount_cents > balance_cents:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A write-off cannot exceed the client's balance.",
        )

    charge = payments.add_ledger_row(
        patient_id=patient_id,
        kind="write_off",
        amount_cents=payload.amount_cents,
        currency=DEFAULT_CHARGE_CURRENCY,
        user_id=user.id,
        write_off_reason=payload.reason,
        note=payload.note,
    )
    audit.log(
        AuditAction.PATIENT_WRITE_OFF_CREATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        changes={
            "charge_id": charge.id,
            "reason": payload.reason,
            "amount_cents": payload.amount_cents,
            "claim_ids": _outstanding_claim_ids(ledger),
        },
    )
    return _to_charge_response(charge)
