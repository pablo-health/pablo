# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Coverage on file: the practice's payer list and each client's plan.

Routes
------

Practice-level, no client attached (not a PHI surface, same posture as
``/api/practice/billing-profile``):

* ``GET /api/payers`` — the practice's payer list, for the picker and Settings.
* ``POST /api/payers`` — add a payer; deadlines default for the payer id.
* ``PATCH /api/payers/{payer_row_id}`` — edit a payer, deadlines included.

Per client, audited (a plan is protected health information about a named
person, and reading or writing it is a patient-record access):

* ``GET /api/patients/{patient_id}/coverage`` — the active primary coverage.
* ``POST /api/patients/{patient_id}/coverage`` — put a plan on file. Names a
  payer already on the list, or adds one from the card on the way through.
* ``PATCH /api/patients/{patient_id}/coverage`` — edit the active coverage.
* ``DELETE /api/patients/{patient_id}/coverage`` — take it off file. The row
  is deactivated, not deleted: a claim filed under it still has something to
  point at.

Access
------

The client must be one the caller can see. That is decided by reading them
through the request's tenant-scoped repository, exactly as the payments
routes do, and the answer for a client who is absent or ungranted is **404,
never 403** — a 403 would confirm the id exists.

What is logged: the audit rows carry the coverage row id and the payer row
id. The member id, the subscriber's name and date of birth never reach a log
line or an audit payload.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from ..models.audit import AuditAction, ResourceType
from ..models.coverage import (
    CoverageResponse,
    CreateCoverageRequest,
    CreatePayerRequest,
    PatientCoverage,
    Payer,
    PayerListResponse,
    PayerResponse,
    UpdateCoverageRequest,
    UpdatePayerRequest,
)
from ..repositories import (
    get_patient_coverage_repository,
    get_patient_repository,
    get_payer_repository,
)
from ..repositories.coverage import ActiveCoverageExistsError
from ..services import AuditService, get_audit_service
from ..services.coverage_intake import new_payer
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..models import User
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.patient import PatientRepository

payers_router = APIRouter(
    prefix="/api/payers",
    tags=["payers"],
    dependencies=[Depends(require_active_subscription)],
)
router = APIRouter(prefix="/api/patients", tags=["patient-coverage"])

PayersRepo = Annotated["PayerRepository", Depends(get_payer_repository)]
CoverageRepo = Annotated["PatientCoverageRepository", Depends(get_patient_coverage_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
CurrentUser = Annotated["User", Depends(require_baa_acceptance)]

_NO_COVERAGE = "No coverage on file."
_PAYER_NOT_FOUND = "Payer not found."


def _to_payer_response(payer: Payer) -> PayerResponse:
    return PayerResponse(**payer.model_dump())


def _to_coverage_response(coverage: PatientCoverage, payer: Payer) -> CoverageResponse:
    fields = coverage.model_dump(exclude={"payer_id", "last_271"})
    return CoverageResponse(payer=_to_payer_response(payer), **fields)


def _require_patient(patients: PatientRepository, patient_id: str, user_id: str) -> None:
    """404 unless this client is visible to this clinician in this practice."""
    if patients.get(patient_id, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")


def _require_payer(payers: PayerRepository, payer_row_id: str) -> Payer:
    payer = payers.get(payer_row_id)
    if payer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_PAYER_NOT_FOUND)
    return payer


def _require_active_coverage(
    coverage: PatientCoverageRepository, patient_id: str
) -> PatientCoverage:
    active = coverage.get_active(patient_id)
    if active is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_COVERAGE)
    return active


# ---------------------------------------------------------------------------
# Payers
# ---------------------------------------------------------------------------


@payers_router.get("", response_model=PayerListResponse)
def list_payers(
    payers: PayersRepo,
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PayerListResponse:
    """The practice's payer list, by name."""
    data = [_to_payer_response(payer) for payer in payers.list()]
    return PayerListResponse(data=data, total=len(data))


@payers_router.post("", response_model=PayerResponse, status_code=status.HTTP_201_CREATED)
def create_payer(
    payload: CreatePayerRequest,
    payers: PayersRepo,
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PayerResponse:
    """Add a payer. A carve-out must name a payer already on the list."""
    if payload.carveout_of is not None:
        _require_payer(payers, payload.carveout_of)
    payer = payers.create(
        new_payer(
            name=payload.name,
            payer_id=payload.payer_id,
            is_carveout=payload.is_carveout,
            carveout_of=payload.carveout_of,
            timely_filing_days=payload.timely_filing_days,
            corrected_claim_days=payload.corrected_claim_days,
            appeal_days=payload.appeal_days,
        )
    )
    return _to_payer_response(payer)


@payers_router.patch("/{payer_row_id}", response_model=PayerResponse)
def update_payer(
    payer_row_id: str,
    payload: UpdatePayerRequest,
    payers: PayersRepo,
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PayerResponse:
    """Edit a payer. Partial: a field the caller did not send keeps its value."""
    payer = _require_payer(payers, payer_row_id)
    changes = payload.model_dump(exclude_unset=True)
    carveout_of = changes.get("carveout_of")
    if carveout_of is not None:
        if carveout_of == payer.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A payer cannot be a carve-out of itself.",
            )
        _require_payer(payers, carveout_of)
    updated = payers.update(payer.model_copy(update=changes))
    return _to_payer_response(updated)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@router.get("/{patient_id}/coverage", response_model=CoverageResponse)
def get_coverage(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    coverage: CoverageRepo,
    payers: PayersRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> CoverageResponse:
    """The client's active primary coverage.

    404 when there is none, matching the unknown-client shape: "is there a
    plan on file" is answered by an absent resource rather than an empty
    object every caller would have to special-case.
    """
    _require_patient(patients, patient_id, user.id)
    active = _require_active_coverage(coverage, patient_id)
    payer = _require_payer(payers, active.payer_id)

    audit.log(
        AuditAction.PATIENT_COVERAGE_VIEWED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        changes={"coverage_id": active.id, "payer_id": payer.id},
    )
    return _to_coverage_response(active, payer)


@router.post(
    "/{patient_id}/coverage",
    response_model=CoverageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_coverage(
    patient_id: str,
    payload: CreateCoverageRequest,
    request: Request,
    user: CurrentUser,
    coverage: CoverageRepo,
    payers: PayersRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> CoverageResponse:
    """Put a plan on file for a client.

    409 when the client already has an active coverage — edit that one, or
    take it off file first. One active primary coverage per client is the
    rule, and the database enforces it too.
    """
    _require_patient(patients, patient_id, user.id)
    if coverage.get_active(patient_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This client already has coverage on file.",
        )

    if payload.new_payer is not None:
        payer = payers.create(
            new_payer(name=payload.new_payer.name, payer_id=payload.new_payer.payer_id)
        )
    else:
        payer = _require_payer(payers, payload.payer_id or "")

    now = utc_now()
    try:
        created = coverage.create(
            PatientCoverage(
                id=str(uuid.uuid4()),
                patient_id=patient_id,
                payer_id=payer.id,
                created_at=now,
                updated_at=now,
                **payload.model_dump(exclude={"payer_id", "new_payer"}),
            )
        )
    except ActiveCoverageExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This client already has coverage on file.",
        ) from exc

    audit.log(
        AuditAction.PATIENT_COVERAGE_CREATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        changes={"coverage_id": created.id, "payer_id": payer.id},
    )
    return _to_coverage_response(created, payer)


@router.patch("/{patient_id}/coverage", response_model=CoverageResponse)
def update_coverage(
    patient_id: str,
    payload: UpdateCoverageRequest,
    request: Request,
    user: CurrentUser,
    coverage: CoverageRepo,
    payers: PayersRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> CoverageResponse:
    """Edit the active coverage. Partial: an omitted field keeps its value."""
    _require_patient(patients, patient_id, user.id)
    active = _require_active_coverage(coverage, patient_id)

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("payer_id") is not None:
        _require_payer(payers, changes["payer_id"])
    updated = coverage.update(active.model_copy(update=changes))
    payer = _require_payer(payers, updated.payer_id)

    audit.log(
        AuditAction.PATIENT_COVERAGE_UPDATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        changes={"coverage_id": updated.id, "payer_id": payer.id},
    )
    return _to_coverage_response(updated, payer)


@router.delete("/{patient_id}/coverage", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_coverage(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    coverage: CoverageRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """Take the active coverage off file. The row stays, inactive."""
    _require_patient(patients, patient_id, user.id)
    active = _require_active_coverage(coverage, patient_id)

    deactivated = coverage.update(active.model_copy(update={"active": False}))

    audit.log(
        AuditAction.PATIENT_COVERAGE_DEACTIVATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        changes={"coverage_id": deactivated.id, "payer_id": deactivated.payer_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
