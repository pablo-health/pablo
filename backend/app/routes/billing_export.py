# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's own records for a period, as a CSV download.

``GET /api/billing/export?from=&to=&kind=claims|payments`` streams one of the
two files :mod:`app.payments.period_export` renders: everything the practice
billed in the window, or every row of its charge ledger recorded in it. Both
ends of the window are inclusive, and the filename carries it, so a year's
file is self-describing on disk and a re-export overwrites the one it
replaces.

This is the practice's copy of its own books — for the accountant at year
end, for a payer audit, to reconcile against the bank, and to leave with if
it leaves. That is a different thing from the biller handoff in
:mod:`app.routes.claims_export`, which is a package assembled for someone
outside the practice, and the difference shows: no names, no member ids, no
diagnoses and no tax id go into these files, and a claim with a scrub finding
is exported rather than refused, because withholding the record is no way to
explain the problem in it.

**Streamed, not buffered.** The repositories yield a row at a time and the
renderer emits chunks, so a year of a busy practice never sits in memory at
once — not the route's, and not the database driver's.

**Audited as a period.** One row per export, naming the window and how many
rows went out. The disclosure here is a whole period rather than a single
record, so that is what the audit entry says: not which clients were in it —
listing every client of a year would put the practice's roster into the audit
trail to describe a file that carries client ids and nothing else.

The count is only known once the last row has gone, so the entry is written
from a background task after the body is fully sent. An export the client
abandoned mid-stream leaves no entry, the same way the tenant archive does:
the row would otherwise claim a disclosure that did not finish.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..api_errors import UnprocessableEntityError
from ..auth.service import require_baa_acceptance
from ..models.audit import AuditAction, ResourceType
from ..payments.period_export import (
    ExportTally,
    stream_claims_csv,
    stream_payments_csv,
)
from ..repositories import (
    get_claim_repository,
    get_patient_payment_repository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from .claims import _practice_timezone

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ..models import User
    from ..repositories.claims import ClaimRepository
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..repositories.user import UserRepository

router = APIRouter(prefix="/api/billing", tags=["billing"])

CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
ClaimsRepo = Annotated["ClaimRepository", Depends(get_claim_repository)]
PaymentsRepo = Annotated["PatientPaymentRepository", Depends(get_patient_payment_repository)]
UsersRepo = Annotated["UserRepository", Depends(get_user_repository)]

ExportKind = Literal["claims", "payments"]


@router.get("/export", response_class=StreamingResponse)
def export_period(
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    payments: PaymentsRepo,
    users: UsersRepo,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    kind: ExportKind = "claims",
    audit: AuditService = Depends(get_audit_service),
) -> StreamingResponse:
    """Stream the period's claims or payments CSV."""
    if to_date < from_date:
        raise UnprocessableEntityError("The period ends before it starts.")

    timezone = _practice_timezone(users, user.id)
    tally = ExportTally()
    if kind == "claims":
        body: Iterator[str] = stream_claims_csv(
            claims.iter_for_period(from_date, to_date), timezone=timezone, tally=tally
        )
    else:
        # A calendar window becomes a pair of moments in the practice's own
        # timezone, half-open at the far end: the instant the day after the
        # window begins. Anything else either drops a row recorded in the last
        # second of the last day or counts one twice.
        body = stream_payments_csv(
            payments.iter_ledger_for_period(
                start=datetime.combine(from_date, time.min, tzinfo=timezone),
                end=datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone),
            ),
            timezone=timezone,
            tally=tally,
        )

    window = f"{from_date.isoformat()}..{to_date.isoformat()}"

    def _record_disclosure() -> None:
        audit.log(
            AuditAction.BILLING_PERIOD_EXPORTED,
            user,
            request,
            resource_type=ResourceType.BILLING_PERIOD_EXPORT,
            resource_id=window,
            changes={
                "format": "csv",
                "kind": kind,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "row_count": tally.rows,
            },
        )

    filename = f"{kind}-{from_date.isoformat()}-to-{to_date.isoformat()}.csv"
    return StreamingResponse(
        body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
        background=BackgroundTask(_record_disclosure),
    )
