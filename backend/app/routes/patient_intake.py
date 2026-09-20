# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The intake form, filled in by the person it is about.

A patient answers a short form before their first appointment: confirm who
the chart says they are, say what brings them in, and complete the PHQ-9 and
the GAD-7. Two routes serve it — one that hands out the form, one that takes
it back — and four things shape both.

* **The patient id comes from the principal, never from the request.** There
  is no ``patient_id`` in either route's inputs, so a body that names another
  patient changes nothing about where the rows land.
* **Both routes require step-up.** The form hands back a name and a date of
  birth, and the submission carries clinical answers. A link that reached the
  wrong inbox is one factor in a stranger's hands.
* **The scoring is arithmetic, not judgement.** Totals and severity bands
  come from the instrument registry, which sums validated items and looks up
  a published band. Nothing here interprets a score, and no model sees one.
* **Confirming identity is an attestation, not a gate.** A patient saying the
  name or the date of birth is wrong is recorded for the clinician to read.
  It does not block the submission and it never edits the chart: who someone
  is, is the session's business, not this form's.

A resubmission is a new submission. Both tables are append-only records of an
administration, so the second set of answers sits beside the first rather
than replacing it, and the clinician sees both.

The clinician's read of those submissions lives here too, on its own router
under the ordinary chart prefix. It is the same table from the other side of
the room, and three things separate the two surfaces.

* **A different principal, checked a different way.** The patient routes take
  a patient principal and step-up; this one takes a clinician who has
  accepted the agreement and holds a grant on the chart. Neither credential
  satisfies the other's door.
* **It hands back the words, not the scores.** PHQ-9 and GAD-7 answers were
  scored into outcome measures when they arrived, and the chart already
  renders those. Re-exposing item scores here would put the same instrument
  on the screen twice, from two sources that can disagree.
* **Reading it is a disclosure.** What a patient wrote about why they came,
  and anything they said the chart has wrong about them, is their own text.
  The read goes on the audit log the way opening a conversation does.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from ..api_errors import BadRequestError, ForbiddenError, NotFoundError
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.patient_intake_api import (
    IntakeFormResponse,
    IntakeIdentityResponse,
    IntakeInstrumentResponse,
    IntakeMeasureResponse,
    IntakeResponseOptionResponse,
    IntakeSubmissionResponse,
    SubmitIntakeRequest,
)
from ..outcome_measures.instruments import INSTRUMENT_REGISTRY, InstrumentValidationError
from ..outcome_measures.item_text import FREQUENCY_OPTIONS, FREQUENCY_PROMPT, ITEM_TEXT
from ..outcome_measures.service import OutcomeMeasureService, UnknownInstrumentError
from ..rate_limit import get_intake_submit_limiter
from ..repositories import (
    get_outcome_measure_repository,
    get_patient_intake_submission_repository,
    get_patient_repository,
)
from ..services.audit_service import AuditService, get_audit_service
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories.outcome_measure import OutcomeMeasureRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_intake_submission import PatientIntakeSubmissionRepository

router = APIRouter(prefix="/api/patient/intake", tags=["patient-intake"])

# The clinician's read of the same table, under the chart prefix every other
# per-patient read lives at. A separate router because the two surfaces share
# no dependency: this one has a patient id in the path precisely because the
# caller is not the patient.
clinician_router = APIRouter(prefix="/api/patients", tags=["patient-intake"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

# ``AuditService`` is spelled out in the route signatures rather than aliased:
# the route-audit guardrail matches the parameter annotation by name.

# The screeners this version of the form asks, in the order it asks them.
_FORM_INSTRUMENTS = ("phq9", "gad7")

# Bumped when the question set changes, so a stored submission can be read
# back against the form that produced it.
_FORM_VERSION = 1

_REASON_PROMPT = "What brings you in?"


def get_intake_outcome_measure_service(
    repo: OutcomeMeasureRepository = Depends(get_outcome_measure_repository),
) -> OutcomeMeasureService:
    """The outcome-measure service on the patient-armed session.

    Deliberately not the clinician router's dependency of the same shape:
    that one depends on ``get_tenant_context``, which a patient principal
    cannot satisfy and should not.
    """
    return OutcomeMeasureService(repo)


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on every route here.

    Same bar as the patient chat surface, for the same reason: one factor
    reaching the wrong person should not open a chart.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


def _instrument_form(code: str) -> IntakeInstrumentResponse:
    """The form section for one screener, assembled from the registry.

    Item keys come from the registry's ``valid_keys``, so the form can only
    ask what the scorer will accept.
    """
    defn = INSTRUMENT_REGISTRY[code]
    prompts = ITEM_TEXT[code]
    return IntakeInstrumentResponse(
        code=defn.code,
        display_name=defn.display_name,
        prompt=FREQUENCY_PROMPT,
        items={str(i + 1): prompt for i, prompt in enumerate(prompts)},
        response_options=[
            IntakeResponseOptionResponse(value=option.value, label=option.label)
            for option in FREQUENCY_OPTIONS
        ],
    )


@router.get("/form", response_model=IntakeFormResponse)
def get_intake_form(
    request: Request,
    patient: CurrentPatient,
    patients: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> IntakeFormResponse:
    """The intake form, with the caller's own name and date of birth on it.

    Exempt from the subscription gate: a patient does not hold the practice's
    subscription, and a form they were asked to fill in should not fail for a
    billing state they cannot see.
    """
    _require_stepped_up(patient)

    own = patients.get_for_patient_principal(patient.patient_id)
    if own is None:
        raise NotFoundError("Record not found")

    # The identity fields are a disclosure, so the read is on the record.
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_VIEWED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT,
        resource_id=patient.patient_id,
    )

    return IntakeFormResponse(
        identity=IntakeIdentityResponse(
            first_name=own.first_name,
            last_name=own.last_name,
            date_of_birth=own.date_of_birth,
        ),
        reason_prompt=_REASON_PROMPT,
        instruments=[_instrument_form(code) for code in _FORM_INSTRUMENTS],
    )


@router.post(
    "/submissions",
    response_model=IntakeSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_intake(
    body: SubmitIntakeRequest,
    request: Request,
    patient: CurrentPatient,
    submissions: PatientIntakeSubmissionRepository = Depends(
        get_patient_intake_submission_repository
    ),
    measures: OutcomeMeasureService = Depends(get_intake_outcome_measure_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> IntakeSubmissionResponse:
    """Record a completed intake form.

    One request, one transaction: two scored screeners and the raw form body
    that produced them. Scoring runs before any write, so a form that fails
    validation leaves nothing behind.
    """
    _require_stepped_up(patient)
    get_intake_submit_limiter().check(patient.patient_id)

    answers = {"phq9": body.phq9, "gad7": body.gad7}
    submitted_at = utc_now()

    recorded = []
    for code in _FORM_INSTRUMENTS:
        try:
            recorded.append(
                measures.create_self_report(
                    patient_id=patient.patient_id,
                    instrument=code,
                    item_scores=answers[code],
                    administered_at=submitted_at,
                )
            )
        except (InstrumentValidationError, UnknownInstrumentError) as exc:
            # The message names an item key and a range, never an answer.
            raise BadRequestError(str(exc), {"instrument": code}) from exc

    submission_id = str(uuid.uuid4())
    submissions.add_for_patient_principal(
        {
            "id": submission_id,
            "patient_id": patient.patient_id,
            "submitted_at": submitted_at,
            "payload": {
                "form_version": _FORM_VERSION,
                "name_confirmed": body.name_confirmed,
                "dob_confirmed": body.dob_confirmed,
                "corrections": body.corrections,
                "reason_text": body.reason_text,
                "instruments": {code: answers[code] for code in _FORM_INSTRUMENTS},
                "outcome_measure_ids": {m.instrument: m.id for m in recorded},
            },
            "created_by": patient.patient_id,
            "created_at": submitted_at,
        }
    )

    # Which instruments were submitted, and nothing they said: the answers
    # and the scores are on the rows, where a clinician reads them.
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_INTAKE_SUBMITTED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_INTAKE_SUBMISSION,
        resource_id=submission_id,
        changes={"instruments": list(_FORM_INSTRUMENTS)},
    )

    return IntakeSubmissionResponse(
        id=submission_id,
        submitted_at=submitted_at,
        measures=[
            IntakeMeasureResponse(
                id=m.id,
                instrument=m.instrument,
                total_score=m.total_score,
                severity=m.severity,
            )
            for m in recorded
        ],
    )


# ---------------------------------------------------------------------------
# The clinician's read
# ---------------------------------------------------------------------------


def get_clinician_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """The patient repository on a tenant-scoped session.

    Deliberately not the patient routes' dependency of the same shape: that
    one is armed for a patient principal, which a clinician is not.
    """
    return get_patient_repository()


def get_clinician_intake_submission_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientIntakeSubmissionRepository:
    """The submission repository on a tenant-scoped session."""
    return get_patient_intake_submission_repository()


class ClinicianIntakeSubmissionResponse(BaseModel):
    """One submission as the chart shows it.

    The item answers and the scores derived from them are deliberately
    absent. They are on the outcome-measure rows, which the chart already
    reads, trends and bands; a second copy here would be the same
    instrument rendered twice from two places.

    ``name_confirmed`` and ``dob_confirmed`` default to true for a row
    written before the form asked, so an old submission reads as "nothing
    flagged" rather than as a correction nobody made.
    """

    id: str
    submitted_at: datetime
    name_confirmed: bool
    dob_confirmed: bool
    corrections: str | None
    reason_text: str


def _submission_response(row: dict[str, object]) -> ClinicianIntakeSubmissionResponse:
    """Project a stored row onto the fields the chart shows.

    Reads defensively out of the JSON payload rather than indexing it: the
    column is a record of what a form sent, and a row written by an earlier
    version of that form is a normal thing to find, not an error.
    """
    payload = row.get("payload")
    body: dict[str, object] = payload if isinstance(payload, dict) else {}
    corrections = body.get("corrections")
    reason_text = body.get("reason_text")
    return ClinicianIntakeSubmissionResponse(
        id=str(row["id"]),
        submitted_at=row["submitted_at"],  # type: ignore[arg-type]
        name_confirmed=bool(body.get("name_confirmed", True)),
        dob_confirmed=bool(body.get("dob_confirmed", True)),
        corrections=str(corrections) if corrections else None,
        reason_text=str(reason_text) if reason_text else "",
    )


@clinician_router.get("/{patient_id}/intake-submissions")
def list_patient_intake_submissions(
    patient_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    submissions: PatientIntakeSubmissionRepository = Depends(
        get_clinician_intake_submission_repository
    ),
    audit: AuditService = Depends(get_audit_service),
) -> list[ClinicianIntakeSubmissionResponse]:
    """A patient's intake submissions, newest first.

    No beta gate and no pagination. A practice that stops paying for some
    optional thing should not lose sight of a clinical record it already
    collected, and a patient files one of these before a first appointment
    and rarely again.

    A patient with no submissions is a 200 and an empty list, not a 404:
    the chart exists, it just has nothing on this surface yet.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    rows = submissions.list_for_clinician(patient_id, user.id)

    # How many were disclosed, and not a word of any of them.
    audit.log(
        action=AuditAction.PATIENT_INTAKE_SUBMISSION_VIEWED,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_SUBMISSION,
        resource_id=patient_id,
        patient=patient,
        changes={"count": len(rows)},
    )

    return [_submission_response(row) for row in rows]


__all__ = [
    "ClinicianIntakeSubmissionResponse",
    "clinician_router",
    "get_clinician_intake_submission_repository",
    "get_clinician_patient_repository",
    "get_intake_outcome_measure_service",
    "router",
]
