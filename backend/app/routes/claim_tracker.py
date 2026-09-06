# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The claims tracker: where every filed claim stands, and a status check on demand.

Routes
------

* ``GET /api/claims`` — every claim the caller can see, newest first, with
  its receipts, the deadline it is under and what to do next. ``state``
  may be given more than once to filter; ``limit`` caps the list.
* ``POST /api/claims/{claim_id}/status`` — read the clearinghouse's feed
  for this one claim now, apply whatever acknowledgement it holds, and
  return the claim as it stands. 503 when the practice has no
  clearinghouse configured or it is not answering.

Access is the same as the claim routes: the row policies scope the list
to the caller's clients, and a claim whose client the caller cannot see is
404. Both routes are audited; the tracker row names every claim it
showed, the status row its one claim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..claims.clearinghouse import ClearinghouseError
from ..claims.receipts import ClaimPipeline
from ..claims.status_worker import check_status
from ..claims.tracker import detail_response
from ..db import get_db_session
from ..db.models import CLAIM_STATES
from ..models.audit import AuditAction, ResourceType
from ..models.claims import ClaimDetailResponse, ClaimTrackerResponse
from ..repositories import get_claim_receipt_repository
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now
from .claims import (
    ClaimsRepo,
    CurrentUser,
    PatientsRepo,
    PayersRepo,
    _audit_changes,
    _require_claim,
)
from .coverage import get_clearinghouse_client

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..claims.clearinghouse import ClearinghouseClient
    from ..repositories.claim_receipts import ClaimReceiptRepository

router = APIRouter(prefix="/api/claims", tags=["claims"])

# String forward references inside ``Annotated``, as the claim routes declare
# theirs: a bare unresolvable name in a signature is silently read as a query
# parameter by the framework.
ReceiptsRepo = Annotated["ClaimReceiptRepository", Depends(get_claim_receipt_repository)]
Clearinghouse = Annotated["ClearinghouseClient | None", Depends(get_clearinghouse_client)]
DbSession = Annotated["Session", Depends(get_db_session)]

_MAX_TRACKER_ROWS = 500
_DEFAULT_TRACKER_ROWS = 200
_NOT_CONFIGURED = "The practice has no clearinghouse configured."
_CLEARINGHOUSE_BUSY = "The clearinghouse is not answering right now. Try again in a minute."


@router.get("", response_model=ClaimTrackerResponse)
def list_claims(
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    receipts: ReceiptsRepo,
    payers: PayersRepo,
    state: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_TRACKER_ROWS)] = _DEFAULT_TRACKER_ROWS,
    audit: AuditService = Depends(get_audit_service),
) -> ClaimTrackerResponse:
    """The tracker: every claim the caller can see, newest first."""
    states = tuple(state) if state else CLAIM_STATES
    unknown = sorted(set(states) - set(CLAIM_STATES))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown claim state(s): {', '.join(unknown)}.",
        )
    today = utc_now().date()
    data = [
        detail_response(
            claim, receipts.list_for_claim(claim.id), payers.get(claim.payer_id), today=today
        )
        for claim in claims.list_by_state(states, limit=limit, newest_first=True)
    ]
    audit.log(
        AuditAction.CLAIMS_TRACKER_VIEWED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id="tracker",
        changes={"claim_ids": [claim.id for claim in data], "states": list(states)},
    )
    return ClaimTrackerResponse(data=data, total=len(data))


@router.post("/{claim_id}/status", response_model=ClaimDetailResponse)
def check_claim_status(
    claim_id: str,
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    receipts: ReceiptsRepo,
    patients: PatientsRepo,
    payers: PayersRepo,
    client: Clearinghouse,
    session: DbSession,
    audit: AuditService = Depends(get_audit_service),
) -> ClaimDetailResponse:
    """Ask the clearinghouse about this claim now and return where it stands."""
    claim, patient = _require_claim(claims, patients, claim_id, user.id)
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _NOT_CONFIGURED)
    pipeline = ClaimPipeline(
        claims=claims, receipts=receipts, session=session, principal_user_id=user.id
    )
    try:
        refreshed = check_status(pipeline, client, claim)
    except ClearinghouseError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CLEARINGHOUSE_BUSY) from exc
    audit.log(
        AuditAction.CLAIM_STATUS_CHECKED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id=refreshed.id,
        patient=patient,
        changes=_audit_changes(refreshed),
    )
    return detail_response(
        refreshed,
        receipts.list_for_claim(refreshed.id),
        payers.get(refreshed.payer_id),
        today=utc_now().date(),
    )
