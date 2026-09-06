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
* ``POST /api/patients/{patient_id}/coverage/verify`` — the chart card's
  re-verify button: run an eligibility check now, through the practice's
  own clearinghouse account, and return the coverage with the answer.

Saving a plan (here, or at intake) also queues that same check on the
post-save task queue when the practice's ``eligibility_auto_check`` setting
is on (the default). ``POST /api/internal/jobs/check-eligibility`` is where
the queue delivers it; it scopes itself to the tenant the same way the
SOAP-generation worker does and is reachable only by the Cloud Tasks
invoker.

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

import logging
import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
    require_cloud_tasks_invoker,
)
from ..claims.clearinghouse import (
    ClearinghouseClient,
    ClearinghouseError,
    ClearinghouseRateLimitedError,
    ClearinghouseUnavailableError,
)
from ..claims.credentials import get_clearinghouse_credential_provider
from ..claims.eligibility import (
    BillingIdentity,
    CoverageNotFoundError,
    EligibilityAutoCheck,
    EligibilityCheckFailedError,
    EligibilityDeps,
    EligibilityNotPossibleError,
    eligibility_actor_type,
    eligibility_audit_changes,
    get_eligibility_auto_check,
    load_billing_identity,
    run_eligibility,
    summary_for_coverage,
)
from ..claims.stedi import StediClearinghouseClient
from ..db import arm_current_user_id, get_db_session, set_tenant_schema
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
from ..models.eligibility import (
    EligibilityTrigger,  # noqa: TC001 — Pydantic resolves the field type at runtime
)
from ..repositories import (
    get_patient_coverage_repository,
    get_patient_repository,
    get_payer_repository,
    get_user_repository,
)
from ..repositories.coverage import ActiveCoverageExistsError
from ..services import AuditService, get_audit_service
from ..services.coverage_intake import new_payer
from ..services.session_generation_worker import resolve_tenant_for_user
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..models import User
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.user import UserRepository

logger = logging.getLogger(__name__)

payers_router = APIRouter(
    prefix="/api/payers",
    tags=["payers"],
    dependencies=[Depends(require_active_subscription)],
)
router = APIRouter(prefix="/api/patients", tags=["patient-coverage"])
jobs_router = APIRouter(prefix="/api/internal/jobs", tags=["patient-coverage"])

PayersRepo = Annotated["PayerRepository", Depends(get_payer_repository)]
CoverageRepo = Annotated["PatientCoverageRepository", Depends(get_patient_coverage_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
AutoCheck = Annotated[EligibilityAutoCheck, Depends(get_eligibility_auto_check)]

_NO_COVERAGE = "No coverage on file."
_PAYER_NOT_FOUND = "Payer not found."
_CLEARINGHOUSE_BUSY = "The clearinghouse is not answering right now. Try again in a minute."


def get_clearinghouse_client(
    ctx: TenantContext = Depends(get_tenant_context),
) -> ClearinghouseClient | None:
    """The practice's clearinghouse account, or ``None`` when none is configured."""
    return _clearinghouse_client_for(ctx.practice_id)


def _clearinghouse_client_for(practice_id: str | None) -> ClearinghouseClient | None:
    credentials = get_clearinghouse_credential_provider().get(practice_id)
    return StediClearinghouseClient(credentials) if credentials is not None else None


def get_billing_identity(user: CurrentUser) -> BillingIdentity | None:
    """Who the 270 is asked as: the practice's billing NPI, else the clinician's."""
    return load_billing_identity(get_db_session(), user)


def _to_payer_response(payer: Payer) -> PayerResponse:
    return PayerResponse(**payer.model_dump())


def _to_coverage_response(coverage: PatientCoverage, payer: Payer) -> CoverageResponse:
    fields = coverage.model_dump(exclude={"payer_id", "last_271"})
    return CoverageResponse(
        payer=_to_payer_response(payer), eligibility=summary_for_coverage(coverage), **fields
    )


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
    auto_check: AutoCheck,
    audit: AuditService = Depends(get_audit_service),
) -> CoverageResponse:
    """Put a plan on file for a client.

    409 when the client already has an active coverage — edit that one, or
    take it off file first. One active primary coverage per client is the
    rule, and the database enforces it too.

    Queues an eligibility check when the practice has auto-check on; the
    answer lands on the row and shows on the next read.
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
    auto_check(created.id, user.id, "save")
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
    auto_check: AutoCheck,
    audit: AuditService = Depends(get_audit_service),
) -> CoverageResponse:
    """Edit the active coverage. Partial: an omitted field keeps its value.

    A changed plan is a different question to the payer, so the stored
    eligibility answer is cleared and (with auto-check on) asked again.
    """
    _require_patient(patients, patient_id, user.id)
    active = _require_active_coverage(coverage, patient_id)

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("payer_id") is not None:
        _require_payer(payers, changes["payer_id"])
    updated = coverage.update(
        active.model_copy(update={**changes, "last_271": None, "verified_at": None})
    )
    payer = _require_payer(payers, updated.payer_id)

    audit.log(
        AuditAction.PATIENT_COVERAGE_UPDATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        changes={"coverage_id": updated.id, "payer_id": payer.id},
    )
    auto_check(updated.id, user.id, "save")
    return _to_coverage_response(updated, payer)


@router.post("/{patient_id}/coverage/verify", response_model=CoverageResponse)
def verify_coverage(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    coverage: CoverageRepo,
    payers: PayersRepo,
    patients: PatientsRepo,
    client: ClearinghouseClient | None = Depends(get_clearinghouse_client),
    identity: BillingIdentity | None = Depends(get_billing_identity),
    audit: AuditService = Depends(get_audit_service),
) -> CoverageResponse:
    """Run an eligibility check now and return the coverage with the answer.

    409 when the practice cannot ask yet (no clearinghouse account, no NPI,
    a payer with no electronic id) — the detail says which, and nothing was
    sent. 503 when the clearinghouse is unreachable or rate-limiting; 502
    when it refused the inquiry outright. An AAA rejection from the payer
    is not an error here: it is stored and rendered as the check's answer.

    The check discloses the client to the payer, so it is audited whenever
    an inquiry went out — answered or not.
    """
    _require_patient(patients, patient_id, user.id)
    active = _require_active_coverage(coverage, patient_id)
    deps = EligibilityDeps(
        client=client, identity=identity, coverage=coverage, payers=payers, patients=patients
    )
    try:
        check = run_eligibility(active.id, user, deps)
    except EligibilityNotPossibleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CoverageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_COVERAGE) from exc
    except EligibilityCheckFailedError as exc:
        audit.log(
            AuditAction.PATIENT_COVERAGE_VERIFIED,
            user,
            request,
            resource_type=ResourceType.PATIENT,
            resource_id=patient_id,
            patient=exc.patient,
            changes=eligibility_audit_changes(
                exc.coverage, "manual", failure=type(exc.cause).__name__
            ),
        )
        raise _clearinghouse_http_error(exc.cause) from exc

    audit.log(
        AuditAction.PATIENT_COVERAGE_VERIFIED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        patient=check.patient,
        changes=eligibility_audit_changes(check.coverage, "manual", summary=check.summary),
    )
    return _to_coverage_response(check.coverage, check.payer)


def _clearinghouse_http_error(cause: ClearinghouseError) -> HTTPException:
    if isinstance(cause, ClearinghouseUnavailableError | ClearinghouseRateLimitedError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_CLEARINGHOUSE_BUSY
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"The clearinghouse refused the check: {cause}",
    )


class CheckEligibilityJob(BaseModel):
    """Cloud Tasks payload for a queued eligibility check.

    Opaque identifiers only — no tenant schema, no member id. The worker
    re-resolves the tenant from ``user_id`` server-side.
    """

    coverage_id: str
    user_id: str
    trigger: EligibilityTrigger


@jobs_router.post("/check-eligibility", status_code=status.HTTP_200_OK)
def check_eligibility_job(
    payload: CheckEligibilityJob,
    http_request: Request,
    coverage: CoverageRepo,
    payers: PayersRepo,
    patients: PatientsRepo,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: the check queued by a coverage save or an intake submission.

    Invoked only by Cloud Tasks (service-account OIDC, enforced by
    ``require_cloud_tasks_invoker``). Scopes the request session to the
    job's tenant first — the invoker's token carries no tenant — then runs
    the same check the re-verify button does, audited under the owning
    clinician with the system as the actor.

    Answers ``200`` once the job is accounted for: a stored answer, a
    non-retryable outcome (unknown tenant, vanished coverage, a practice
    that cannot ask yet) or a vendor refusal. A transient failure (the
    clearinghouse unreachable or rate-limiting) answers ``503`` so the queue
    retries with backoff; queue config bounds the attempts.
    """
    tenant = resolve_tenant_for_user(payload.user_id)
    if tenant is None:
        logger.warning(
            "check-eligibility job: no active tenant for coverage %s — dropping",
            payload.coverage_id,
        )
        return {"status": "unknown_tenant"}
    practice_id, schema = tenant
    session = get_db_session()
    set_tenant_schema(session, schema)
    arm_current_user_id(session, payload.user_id)

    user = user_repo.get(payload.user_id)
    if user is None:
        return {"status": "unknown_user"}

    deps = EligibilityDeps(
        client=_clearinghouse_client_for(practice_id),
        identity=load_billing_identity(session, user),
        coverage=coverage,
        payers=payers,
        patients=patients,
    )
    try:
        check = run_eligibility(payload.coverage_id, user, deps)
    except CoverageNotFoundError:
        return {"status": "not_found"}
    except EligibilityNotPossibleError:
        logger.info(
            "check-eligibility job: practice cannot ask yet, coverage %s", payload.coverage_id
        )
        return {"status": "skipped"}
    except EligibilityCheckFailedError as exc:
        audit.log(
            AuditAction.PATIENT_COVERAGE_VERIFIED,
            user,
            http_request,
            resource_type=ResourceType.PATIENT,
            resource_id=exc.patient.id,
            patient=exc.patient,
            changes=eligibility_audit_changes(
                exc.coverage, payload.trigger, failure=type(exc.cause).__name__
            ),
            actor_type=eligibility_actor_type(payload.trigger),
        )
        if isinstance(exc.cause, ClearinghouseUnavailableError | ClearinghouseRateLimitedError):
            raise _clearinghouse_http_error(exc.cause) from exc
        logger.warning(
            "check-eligibility job: clearinghouse refused, coverage %s: %s",
            payload.coverage_id,
            type(exc.cause).__name__,
        )
        return {"status": "refused"}

    audit.log(
        AuditAction.PATIENT_COVERAGE_VERIFIED,
        user,
        http_request,
        resource_type=ResourceType.PATIENT,
        resource_id=check.patient.id,
        patient=check.patient,
        changes=eligibility_audit_changes(check.coverage, payload.trigger, summary=check.summary),
        actor_type=eligibility_actor_type(payload.trigger),
    )
    return {"status": check.summary.status}


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
