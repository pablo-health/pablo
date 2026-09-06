# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Claims: build one from a session, check it, correct or void it.

Routes
------

* ``POST /api/claims/from-session/{appointment_id}`` — snapshot the visit,
  the client's coverage and the practice's billing identity into a
  ``draft`` claim. 422 when the client has no active coverage.
* ``GET /api/claims/{claim_id}`` — the claim with its lines, every receipt
  it has collected, the deadline it is under and what to do next (see
  ``app.routes.claim_tracker`` for the list across clients and the
  on-demand status check).
* ``POST /api/claims/{claim_id}/validate`` — run the scrub. With a blocking
  finding: 422 carrying every finding, and the claim stays a draft.
  Without: the claim becomes ``validated`` and the response carries any
  warnings. 409 unless the claim is a draft.
* ``POST /api/claims/{claim_id}/correct`` — a replacement claim (frequency
  ``7``) rebuilt from today's sources, naming this one as its parent.
  409 on a draft: a draft is simply rebuilt.
* ``POST /api/claims/{claim_id}/void`` — a void (frequency ``8``) of this
  claim. 409 on a draft, which has never been filed and has nothing to void.
* ``GET /api/patients/{patient_id}/claims`` — the client's claims, newest
  first.

A claim past ``draft`` is never edited in place; ``correct`` and ``void``
are the only ways it changes, and both leave the original row untouched.

Access
------

The client must be one the caller can see, decided by reading them through
the request's tenant-scoped repository exactly as the coverage routes do;
an absent or ungranted client — or a claim belonging to one — is **404,
never 403**. Every route is audited: a claim carries diagnosis codes and a
named person's demographics. The audit rows carry the claim id, its
control number, its state, the payer row id and the parent claim id.
Nothing off the card and nothing clinical ever reaches an audit payload or
a log line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.service import require_baa_acceptance
from ..claims.assembly import (
    AppointmentNotFoundError,
    ClaimSources,
    ClientNotFoundError,
    NoActiveCoverageError,
    PayerNotFoundError,
    build_claim_from_session,
    build_corrected_claim,
    build_void_claim,
)
from ..claims.scrub import scrub
from ..claims.tracker import detail_response
from ..claims.transitions import ClaimNotValidError, advance
from ..db import get_db_session
from ..models.audit import AuditAction, ResourceType
from ..models.claims import (
    BuildClaimRequest,
    Claim,
    ClaimDetailResponse,
    ClaimListResponse,
    ClaimResponse,
    ClaimValidationFailed,
    FindingResponse,
    ValidateClaimResponse,
)
from ..repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_claim_receipt_repository,
    get_claim_repository,
    get_clinician_profile_repository,
    get_patient_coverage_repository,
    get_patient_repository,
    get_payer_repository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from ..services.practice_billing_profile import load_billing_profile
from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import tzinfo

    from ..claims.scrub import Finding
    from ..models import User
    from ..models.patient import Patient
    from ..repositories.claim_receipts import ClaimReceiptRepository
    from ..repositories.claims import ClaimRepository
    from ..repositories.clinician_profile import ClinicianProfileRepository
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.appointment_type import AppointmentTypeRepository

router = APIRouter(prefix="/api/claims", tags=["claims"])
patient_claims_router = APIRouter(prefix="/api/patients", tags=["claims"])


def get_billing_profile_loader() -> Mapping[str, object]:
    """The practice's billing profile, read when a claim is built."""
    return load_billing_profile(get_db_session())


# Module-level aliases with string forward references, the way the coverage
# routes declare theirs: the repository types are imported only for type
# checking, and a string inside an ``Annotated`` built here is left alone by
# the framework, whereas an unresolvable name in a function signature is
# silently read as a query parameter.
CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
ClaimsRepo = Annotated["ClaimRepository", Depends(get_claim_repository)]
ReceiptsRepo = Annotated["ClaimReceiptRepository", Depends(get_claim_receipt_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
AppointmentsRepo = Annotated["AppointmentRepository", Depends(get_appointment_repository)]
AppointmentTypesRepo = Annotated[
    "AppointmentTypeRepository", Depends(get_appointment_type_repository)
]
CoverageRepo = Annotated["PatientCoverageRepository", Depends(get_patient_coverage_repository)]
PayersRepo = Annotated["PayerRepository", Depends(get_payer_repository)]
ClinicianProfilesRepo = Annotated[
    "ClinicianProfileRepository", Depends(get_clinician_profile_repository)
]
UsersRepo = Annotated["UserRepository", Depends(get_user_repository)]
BillingProfile = Annotated["Mapping[str, object]", Depends(get_billing_profile_loader)]

_CLAIM_NOT_FOUND = "Claim not found."
_CLIENT_NOT_FOUND = "Client not found."


def get_claim_sources(
    user: CurrentUser,
    appointments: AppointmentsRepo,
    appointment_types: AppointmentTypesRepo,
    patients: PatientsRepo,
    coverage: CoverageRepo,
    payers: PayersRepo,
    clinician_profiles: ClinicianProfilesRepo,
    users: UsersRepo,
    billing_profile: BillingProfile,
) -> ClaimSources:
    """Everything the assembly reads, bound to this request's tenant."""
    return ClaimSources(
        appointments=appointments,
        appointment_types=appointment_types,
        patients=patients,
        coverage=coverage,
        payers=payers,
        clinician_profiles=clinician_profiles,
        billing_profile=lambda: billing_profile,
        timezone=_practice_timezone(users, user.id),
    )


Sources = Annotated[ClaimSources, Depends(get_claim_sources)]


def _practice_timezone(users: UserRepository, user_id: str) -> tzinfo:
    """The clinician's calendar timezone; UTC if it is unset or unknown."""
    try:
        return ZoneInfo(users.get_preferences(user_id).timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _require_patient(patients: PatientRepository, patient_id: str, user_id: str) -> Patient:
    """404 unless this client is visible to this clinician in this practice."""
    patient = patients.get(patient_id, user_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_CLIENT_NOT_FOUND)
    return patient


def _require_claim(
    claims: ClaimRepository, patients: PatientRepository, claim_id: str, user_id: str
) -> tuple[Claim, Patient]:
    """404 for an absent claim, and for one whose client the caller cannot see."""
    claim = claims.get(claim_id)
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_CLAIM_NOT_FOUND)
    patient = patients.get(claim.patient_id, user_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_CLAIM_NOT_FOUND)
    return claim, patient


def _require_past_draft(claim: Claim, verb: str) -> None:
    if claim.state == "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A draft claim cannot be {verb}; edit the visit and rebuild it instead.",
        )


def _audit_changes(claim: Claim) -> dict[str, object]:
    return {
        "claim_id": claim.id,
        "control_number": claim.control_number,
        "state": claim.state,
        "frequency_code": claim.frequency_code,
        "payer_id": claim.payer_id,
        "parent_claim_id": claim.parent_claim_id,
    }


def _to_findings(findings: list[Finding]) -> list[FindingResponse]:
    return [
        FindingResponse(severity=f.severity, code=f.code, message=f.message, field=f.field)
        for f in findings
    ]


def _to_response(claim: Claim) -> ClaimResponse:
    return ClaimResponse(**claim.model_dump())


@router.post(
    "/from-session/{appointment_id}",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
)
def build_claim(
    appointment_id: str,
    request: Request,
    user: CurrentUser,
    sources: Sources,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    payload: BuildClaimRequest | None = None,
    audit: AuditService = Depends(get_audit_service),
) -> ClaimResponse:
    """A draft claim for the visit, snapshotted from what is on file now."""
    add_on = payload.add_on if payload is not None else None
    try:
        claim = build_claim_from_session(appointment_id, user, sources, add_on=add_on)
    except (AppointmentNotFoundError, ClientNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found."
        ) from exc
    except NoActiveCoverageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The client has no active coverage on file.",
        ) from exc
    except PayerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The client's coverage names a payer that is no longer on file.",
        ) from exc

    created = claims.create(claim)
    patient = _require_patient(patients, created.patient_id, user.id)
    audit.log(
        AuditAction.CLAIM_CREATED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id=created.id,
        patient=patient,
        changes={**_audit_changes(created), "appointment_id": appointment_id},
    )
    return _to_response(created)


@router.get("/{claim_id}", response_model=ClaimDetailResponse)
def get_claim(
    claim_id: str,
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    payers: PayersRepo,
    receipts: ReceiptsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> ClaimDetailResponse:
    """The claim with every receipt it has collected, its deadline and its next action."""
    claim, patient = _require_claim(claims, patients, claim_id, user.id)
    audit.log(
        AuditAction.CLAIM_VIEWED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id=claim.id,
        patient=patient,
        changes=_audit_changes(claim),
    )
    return detail_response(
        claim,
        receipts.list_for_claim(claim.id),
        payers.get(claim.payer_id),
        today=utc_now().date(),
    )


@router.post("/{claim_id}/validate", response_model=ValidateClaimResponse)
def validate_claim(
    claim_id: str,
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> ValidateClaimResponse:
    """Run the scrub; a clean claim becomes ``validated``, a blocked one stays a draft."""
    claim, patient = _require_claim(claims, patients, claim_id, user.id)
    if claim.state != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only a draft claim can be validated; this one is {claim.state}.",
        )
    try:
        validated = advance(claim, "validate", now=utc_now())
    except ClaimNotValidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=ClaimValidationFailed(
                message="The claim has blocking findings and stays a draft.",
                findings=_to_findings(exc.findings),
            ).model_dump(),
        ) from exc

    warnings = _to_findings(scrub(validated))
    saved = claims.update(validated)
    audit.log(
        AuditAction.CLAIM_VALIDATED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id=saved.id,
        patient=patient,
        changes=_audit_changes(saved),
    )
    return ValidateClaimResponse(claim=_to_response(saved), findings=warnings)


@router.post(
    "/{claim_id}/correct",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
)
def correct_claim(
    claim_id: str,
    request: Request,
    user: CurrentUser,
    sources: Sources,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> ClaimResponse:
    """A replacement claim, rebuilt from today's sources, with this one as its parent."""
    parent, patient = _require_claim(claims, patients, claim_id, user.id)
    _require_past_draft(parent, "corrected")
    try:
        child = build_corrected_claim(parent, user, sources)
    except (AppointmentNotFoundError, ClientNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The visit this claim was built from is no longer on file; void it instead.",
        ) from exc
    except NoActiveCoverageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The client has no active coverage on file.",
        ) from exc
    except PayerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The client's coverage names a payer that is no longer on file.",
        ) from exc

    created = claims.create(child)
    audit.log(
        AuditAction.CLAIM_CORRECTED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id=created.id,
        patient=patient,
        changes=_audit_changes(created),
    )
    return _to_response(created)


@router.post(
    "/{claim_id}/void",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
)
def void_claim(
    claim_id: str,
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> ClaimResponse:
    """A void of this claim: the same claim restated with frequency ``8``."""
    parent, patient = _require_claim(claims, patients, claim_id, user.id)
    _require_past_draft(parent, "voided")
    created = claims.create(build_void_claim(parent))
    audit.log(
        AuditAction.CLAIM_VOIDED,
        user,
        request,
        resource_type=ResourceType.CLAIM,
        resource_id=created.id,
        patient=patient,
        changes=_audit_changes(created),
    )
    return _to_response(created)


@patient_claims_router.get("/{patient_id}/claims", response_model=ClaimListResponse)
def list_patient_claims(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    claims: ClaimsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> ClaimListResponse:
    """The client's claims, newest first."""
    patient = _require_patient(patients, patient_id, user.id)
    data = [_to_response(claim) for claim in claims.list_by_patient(patient_id)]
    audit.log(
        AuditAction.PATIENT_CLAIMS_VIEWED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        patient=patient,
        changes={"claim_ids": [claim.id for claim in data]},
    )
    return ClaimListResponse(data=data, total=len(data))
