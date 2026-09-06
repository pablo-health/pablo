# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A status check on demand: ask the clearinghouse about one claim now.

``POST /api/claims/{claim_id}/status`` reads the clearinghouse's feed for
this one claim, applies whatever acknowledgement it holds through the same
path the webhook and the poll use, and returns the claim as the tracker's
detail view sees it. A look that found nothing new is recorded as a
``status_checked`` receipt so the tracker shows when somebody last asked.
503 when the practice has no clearinghouse configured or it is not
answering.

Access is the claim routes': a claim whose client the caller cannot see is
404, never 403. Audited as ``claim_status_checked`` with the claim's ids
and where it stands afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..claims.clearinghouse import ClearinghouseError
from ..claims.receipts import ClaimPipeline
from ..claims.status_worker import check_status
from ..db import get_db_session
from ..models.audit import AuditAction, ResourceType
from ..models.claims import ClaimDetailResponse
from ..services import AuditService, get_audit_service
from .claims import (
    ClaimsRepo,
    CurrentUser,
    PatientsRepo,
    PayersRepo,
    ReceiptsRepo,
    _audit_changes,
    _require_claim,
    _to_detail,
)
from .coverage import get_clearinghouse_client

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..claims.clearinghouse import ClearinghouseClient

router = APIRouter(prefix="/api/claims", tags=["claims"])

# String forward references inside ``Annotated``, as the claim routes declare
# theirs: a bare unresolvable name in a signature is silently read as a query
# parameter by the framework.
Clearinghouse = Annotated["ClearinghouseClient | None", Depends(get_clearinghouse_client)]
DbSession = Annotated["Session", Depends(get_db_session)]

_NOT_CONFIGURED = "The practice has no clearinghouse configured."
_CLEARINGHOUSE_BUSY = "The clearinghouse is not answering right now. Try again in a minute."


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
    return _to_detail(refreshed, patient, payers.get(refreshed.payer_id), receipts)
