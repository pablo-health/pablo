# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Balances — every client who owes the practice money, oldest first.

``GET /api/billing/balances`` is the collections half of the billing page.
The unbilled queue beside it answers "what have I not charged for yet"; this
answers "who has not paid", which is a different question with a different
answer: a session charged and declined is off the queue and on this list.

There is no stored balance to select on, by design — a balance is arithmetic
over the ledger, and storing it would create a second source of truth that
drifts the first time a webhook lands out of order. So the read totals every
ledger row the caller can see, groups by client, and keeps the ones that come
out non-zero. The arithmetic itself is
:func:`app.payments.balance.patient_balance` and is not restated here, so this
list and the chart header can never disagree.

A negative balance is kept rather than filtered out: it is a refund the
practice owes, and a collections screen that shows only debts is the one place
that would never surface it.

Access is the schema plus the ``has_patient_access`` row policy, exactly as
the claims tracker does it: rows for a client the caller cannot see never
arrive, and any that name an unreadable client are dropped rather than
refused.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from ..auth.service import require_baa_acceptance
from ..models import AuditAction, User
from ..models.audit import ResourceType
from ..models.payments import BalancesResponse, ClientBalanceItem
from ..payments.balance import BalanceSummary, patient_balance
from ..repositories import (
    PatientRepository,
    get_patient_payment_repository,
    get_patient_repository,
)
from ..services import AuditService, get_audit_service

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.payments import PatientCharge
    from ..repositories.patient_payment import PatientPaymentRepository

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/balances", response_model=BalancesResponse)
def list_balances(
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    payments: PatientPaymentRepository = Depends(get_patient_payment_repository),
    audit: AuditService = Depends(get_audit_service),
) -> BalancesResponse:
    """Clients carrying a balance, the oldest outstanding first."""
    by_patient: dict[str, list[PatientCharge]] = defaultdict(list)
    for charge in payments.list_all_charges():
        by_patient[charge.patient_id].append(charge)

    visible = patients.get_multiple(list(by_patient), user.id)
    items: list[ClientBalanceItem] = []
    for patient_id, charges in by_patient.items():
        patient = visible.get(patient_id)
        if patient is None:
            continue
        summary = patient_balance(charges)
        if summary.balance_cents == 0:
            continue
        items.append(
            ClientBalanceItem(
                patient_id=patient_id,
                patient_name=patient.display_name,
                balance_cents=summary.balance_cents,
                currency=charges[0].currency,
                outstanding_since=_outstanding_since(charges, summary),
            )
        )

    # Oldest first, with the client id breaking ties so two balances written
    # in the same transaction do not swap places between reads.
    items.sort(key=lambda item: (item.outstanding_since, item.patient_id))

    audit.log(
        AuditAction.BALANCES_LISTED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id="balances",
        changes={
            "count": len(items),
            "patient_ids": [item.patient_id for item in items],
        },
    )
    return BalancesResponse(items=items)


def _outstanding_since(charges: list[PatientCharge], summary: BalanceSummary) -> datetime:
    """When the money behind this balance first went on the ledger.

    The earliest row belonging to a visit that still has a balance — so a
    client seen for two years whose only unpaid visit was last week sorts as
    a week old, not two years. Which visits those are comes from the balance
    summary rather than from re-deciding here what counts as owed.

    Falls back to the client's earliest row when the balance sits entirely on
    rows that hang off no visit, which have no visit line to date them by.
    """
    open_visits = {visit.appointment_id for visit in summary.by_visit if visit.balance_cents != 0}
    dated = [c.created_at for c in charges if c.appointment_id in open_visits]
    return min(dated) if dated else min(c.created_at for c in charges)
