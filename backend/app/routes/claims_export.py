# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The biller handoff: claims out of the practice as a CSV or a CMS-1500 PDF.

Routes
------

* ``GET /api/claims/export.csv?from=&to=`` — every validated-or-later claim
  with a service line dated in the range (inclusive), one CSV row per
  line. A draft in the range is left out.
* ``GET /api/claims/{claim_id}/cms1500.pdf`` — one claim on a letter page
  in the CMS-1500 layout. 409 on a draft.

Both run the scrub again over what is about to leave and refuse the whole
package — 422, code ``CLAIM_EXPORT_BLOCKED``, ``details.claims`` naming
each blocked claim and its findings — when any claim has a blocking one.
A validated claim has already passed the scrub, so this only bites when a
rule tightened after validation; it is the last check before disclosure.

Both are downloads (``Content-Disposition: attachment``) and neither
changes a claim. Each is a disclosure of a claim's contents to someone
outside the practice, so each writes an audit row: the CSV export one row
naming every claim it carried (ids, control numbers, range and count), the
PDF one row for its claim. Nothing off the card — no member id, no date
of birth, no diagnosis — reaches an audit payload or a log line.

Mounted before the claims router: ``/api/claims/export.csv`` would
otherwise be read as a claim id.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import require_baa_acceptance
from ..claims.cms1500 import render_cms1500
from ..claims.export import ExportBlockedError, check_export, claims_to_csv
from ..models.audit import AuditAction, ResourceType
from ..models.claims import ClaimExportFinding, FindingResponse
from ..repositories import get_claim_repository, get_patient_repository
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..models import User
    from ..models.claims import Claim
    from ..models.patient import Patient
    from ..repositories.claims import ClaimRepository
    from ..repositories.patient import PatientRepository

router = APIRouter(prefix="/api/claims", tags=["claims"])

CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
ClaimsRepo = Annotated["ClaimRepository", Depends(get_claim_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]

_CLAIM_NOT_FOUND = "Claim not found."


@router.get("/export.csv", response_class=Response)
def export_claims_csv(
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """The biller CSV for every validated-or-later claim dated in the range."""
    if to_date < from_date:
        raise UnprocessableEntityError("The range ends before it starts.")
    selected = claims.list_for_export(from_date, to_date)
    _refuse_if_blocked(selected)

    audit.log(
        AuditAction.CLAIMS_EXPORTED,
        user,
        request,
        resource_type=ResourceType.CLAIM_EXPORT,
        resource_id=f"{from_date.isoformat()}..{to_date.isoformat()}",
        changes={
            "format": "csv",
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "count": len(selected),
            "claim_ids": [claim.id for claim in selected],
            "control_numbers": [claim.control_number for claim in selected],
        },
    )
    filename = f"claims-{from_date.isoformat()}-{to_date.isoformat()}.csv"
    return Response(
        content=claims_to_csv(selected),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{claim_id}/cms1500.pdf", response_class=Response)
def export_claim_cms1500(
    claim_id: str,
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """One claim on a letter page in the CMS-1500 layout."""
    claim, patient = _require_claim(claims, patients, claim_id, user.id)
    if claim.state == "draft":
        raise ConflictError("A draft claim cannot be exported; validate it first.")
    _refuse_if_blocked([claim])

    audit.log(
        AuditAction.CLAIM_EXPORTED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id=claim.id,
        patient=patient,
        changes={
            "format": "cms1500_pdf",
            "claim_id": claim.id,
            "control_number": claim.control_number,
            "state": claim.state,
            "payer_id": claim.payer_id,
        },
    )
    return Response(
        content=render_cms1500(claim, now=utc_now()),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="claim-{claim.control_number}.pdf"'},
    )


def _require_claim(
    claims: ClaimRepository, patients: PatientRepository, claim_id: str, user_id: str
) -> tuple[Claim, Patient]:
    """404 for an absent claim, and for one whose client the caller cannot see."""
    claim = claims.get(claim_id)
    if claim is None:
        raise NotFoundError(_CLAIM_NOT_FOUND)
    patient = patients.get(claim.patient_id, user_id)
    if patient is None:
        raise NotFoundError(_CLAIM_NOT_FOUND)
    return claim, patient


def _refuse_if_blocked(selected: list[Claim]) -> None:
    """422 ``CLAIM_EXPORT_BLOCKED`` naming every claim that would leave with a blocking finding."""
    try:
        check_export(selected, today=utc_now().date())
    except ExportBlockedError as exc:
        blocked = [
            ClaimExportFinding(
                claim_id=b.claim_id,
                control_number=b.control_number,
                findings=[
                    FindingResponse(
                        severity=f.severity, code=f.code, message=f.message, field=f.field
                    )
                    for f in b.findings
                ],
            ).model_dump()
            for b in exc.blocked
        ]
        raise UnprocessableEntityError(
            "Some claims have blocking findings; nothing was exported.",
            details={"claims": blocked},
            code="CLAIM_EXPORT_BLOCKED",
        ) from exc
