# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's financial report: aging, payer mix, collections rate, and
claim-to-payment lag, for an explicit window.

``GET /api/billing/report?from=&to=`` is the read-only fourth surface on the
Billing page, beside the unbilled queue, balances and the claims tracker.
Every figure is computed here, on read, from the ledger and the claims table
as they stand right now — see :mod:`app.payments.reporting` for the
arithmetic, none of which is restated in this route.

Two windows, the same split :mod:`app.payments.period_export` already draws.
Payer mix and claim-to-payment lag are claims read by claims — a claim already
carries what was billed and what came back, so they are selected the way the
biller export selects them, by a service line dated in ``[from, to]``.
Collections rate is a ledger read by when money moved, the half-open window
the CSV export already uses. Aging is a live snapshot of everything still
owed, aged as of the end of ``to`` — there is no "since when" for a balance
that is still open, only "as of when".

There is no default window: a report with an implicit "this month" behind it
answers a question the caller did not ask, silently, the day the calendar
turns over.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..api_errors import UnprocessableEntityError
from ..auth.service import require_baa_acceptance
from ..models.audit import AuditAction, ResourceType
from ..models.billing_report import (
    AgingBucketResponse,
    AgingReportResponse,
    BillingReportResponse,
    ClaimPaymentLagReportResponse,
    CollectionsRateResponse,
    PayerLagResponse,
    PayerMixEntryResponse,
    PayerMixReportResponse,
)
from ..payments.reporting import (
    aging_report,
    claim_payment_lag_report,
    collections_rate_report,
    payer_mix_report,
)
from ..repositories import (
    get_claim_receipt_repository,
    get_claim_repository,
    get_patient_payment_repository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from .claims import _practice_timezone

if TYPE_CHECKING:
    from ..models import User
    from ..models.claims import Claim, ClaimReceipt
    from ..repositories.claim_receipts import ClaimReceiptRepository
    from ..repositories.claims import ClaimRepository
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..repositories.user import UserRepository

router = APIRouter(prefix="/api/billing", tags=["billing"])

CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
ClaimsRepo = Annotated["ClaimRepository", Depends(get_claim_repository)]
ClaimReceiptsRepo = Annotated["ClaimReceiptRepository", Depends(get_claim_receipt_repository)]
PaymentsRepo = Annotated["PatientPaymentRepository", Depends(get_patient_payment_repository)]
UsersRepo = Annotated["UserRepository", Depends(get_user_repository)]


@router.get("/report", response_model=BillingReportResponse)
def get_billing_report(
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    claim_receipts: ClaimReceiptsRepo,
    payments: PaymentsRepo,
    users: UsersRepo,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    audit: AuditService = Depends(get_audit_service),
) -> BillingReportResponse:
    """The four sections together, for the window given."""
    if to_date < from_date:
        raise UnprocessableEntityError("The period ends before it starts.")

    timezone = _practice_timezone(users, user.id)
    window_end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone)

    claims_in_window: list[Claim] = list(claims.iter_for_period(from_date, to_date))
    receipts_by_claim: dict[str, list[ClaimReceipt]] = {
        claim.id: claim_receipts.list_for_claim(claim.id) for claim in claims_in_window
    }
    ledger_in_window = list(
        payments.iter_ledger_for_period(
            start=datetime.combine(from_date, time.min, tzinfo=timezone), end=window_end
        )
    )

    aging = aging_report(payments.list_all_charges(), as_of=window_end)
    payer_mix = payer_mix_report(claims_in_window)
    collections_rate = collections_rate_report(ledger_in_window)
    claim_lag = claim_payment_lag_report(claims_in_window, receipts_by_claim)

    audit.log(
        AuditAction.BILLING_REPORT_VIEWED,
        user,
        request,
        resource_type=ResourceType.BILLING_REPORT,
        resource_id=f"{from_date.isoformat()}..{to_date.isoformat()}",
        changes={
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "claim_count": len(claims_in_window),
            "claim_ids": [claim.id for claim in claims_in_window],
            "payer_ids": sorted({claim.payer_id for claim in claims_in_window}),
        },
    )

    return BillingReportResponse(
        from_date=from_date,
        to_date=to_date,
        aging=AgingReportResponse(
            buckets=[
                AgingBucketResponse(label=bucket.label, count=bucket.count, cents=bucket.cents)
                for bucket in aging.buckets
            ]
        ),
        payer_mix=PayerMixReportResponse(
            entries=[
                PayerMixEntryResponse(
                    payer_id=entry.payer_id,
                    payer_name=entry.payer_name,
                    billed_cents=entry.billed_cents,
                    collected_cents=entry.collected_cents,
                )
                for entry in payer_mix.entries
            ]
        ),
        collections_rate=CollectionsRateResponse(
            billed_cents=collections_rate.billed_cents,
            collected_cents=collections_rate.collected_cents,
            contractual_adjustment_cents=collections_rate.contractual_adjustment_cents,
            write_off_cents=collections_rate.write_off_cents,
        ),
        claim_payment_lag=ClaimPaymentLagReportResponse(
            by_payer=[
                PayerLagResponse(
                    payer_id=lag.payer_id,
                    payer_name=lag.payer_name,
                    claim_count=lag.claim_count,
                    median_days=lag.median_days,
                    p90_days=lag.p90_days,
                )
                for lag in claim_lag.by_payer
            ]
        ),
    )
