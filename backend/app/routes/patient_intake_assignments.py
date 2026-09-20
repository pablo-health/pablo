# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Asking somebody to fill a form in, and them filling it in.

The form builder decides what a practice asks. These routes are what
happens next: a clinician sends a published version to a patient, and the
patient works through it a question at a time from the portal. Two
surfaces, two routers:

  Patient portal — ``/api/patient/intake``

    GET  /assignments                              -> the forms I was asked for
    GET  /assignments/{id}                         -> one form, with what I saved
    PUT  /assignments/{id}/items/{item_id}         -> save one answer
    POST /assignments/{id}/signatures              -> sign a consent document
    POST /assignments/{id}/artifacts               -> attach a file I uploaded
    DELETE /assignments/{id}/artifacts/{id}        -> take one back off
    PUT  /assignments/{id}/items/{item_id}/coverage -> the plan on my card
    POST /assignments/{id}/submit                  -> hand it in, get a receipt

  Clinician — ``/api/patients``

    POST /{patient_id}/intake-assignments          -> ask for a form
    GET  /{patient_id}/intake-assignments          -> what was asked, and how far
    GET  /{patient_id}/intake-assignments/{id}     -> what they answered
    POST /{patient_id}/intake-assignments/{id}/withdraw

Five things shape all of it.

**The patient id comes from the principal, never from the request.** No
patient route takes one, so a body naming somebody else changes nothing
about which rows are read or written.

**Both patient reads and the save require step-up.** A form carries the
patient's own answers about why they came and how they have been feeling.
A link that reached the wrong inbox is one factor in a stranger's hands.

**The server decides whether a form is finished.** Every response that
mentions progress carries a ``complete`` the server computed from the rows
just now. Nothing asks the client what it thinks, which matters here more
than usual: a question somebody skipped and a question they were never
shown look the same from a browser.

**Asking twice is asking once.** Sending the same version to the same
patient again — a second click, or portal access reissued after a link
expired — returns the assignment that is already live, with a 200 rather
than a 201. There is no path that leaves somebody holding two copies of
one form.

**A status code is the whole answer.** A ``409`` means the form is no
longer this patient's to fill in, because it was handed in or withdrawn. A
``422`` on a save means the answer does not fit the question, and on a
submit it means the form is not finished and names what is outstanding.
Both messages say what to do about it in the words the patient is reading.

**A consent document is signed, not saved.** It has a route of its own
because its answer is a record rather than a value: the signature row says
which revision of which text was read, by whom, in what role, from which
session and how strongly that session had proved who was holding it. The
save route refuses the type outright, so the only thing that can assert a
signature is the thing that takes one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from ..api_errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..claims.eligibility import (
    IntakeEligibilityCheck,
    get_intake_eligibility_check,
)
from ..intake.answers import AnswerError
from ..intake.consent_statement import consent_statement
from ..intake.items import stored_config
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.coverage import IntakeCoverage  # noqa: TC001 — fastapi resolves the annotation
from ..models.patient_intake_assignment_api import (
    ArtifactWriteResponse,
    AttachArtifactRequest,
    ClinicianIntakeAnswerResponse,
    ClinicianIntakeAssignmentDetailResponse,
    CreateAssignmentRequest,
    IntakeArtifactResponse,
    IntakeAssignmentDetailResponse,
    IntakeAssignmentItemResponse,
    IntakeAssignmentResponse,
    IntakeCorrectionResponse,
    IntakeProgressResponse,
    IntakeSubmissionResponse,
    SaveAnswerRequest,
    SavedAnswerResponse,
    SaveIntakeCoverageResponse,
    SubmittedMeasureResponse,
)
from ..models.patient_intake_signature_api import (
    IntakeSignatureResponse,
    SignDocumentRequest,
)
from ..outcome_measures.service import (  # noqa: TC001 — fastapi resolves the annotation
    OutcomeMeasureService,
)
from ..repositories import (
    ArtifactSlotTakenError,
    DocumentAlreadyAttachedError,
    get_intake_document_repository,
    get_intake_packet_repository,
    get_patient_coverage_repository,
    get_patient_document_repository,
    get_patient_intake_artifact_repository,
    get_patient_intake_assignment_repository,
    get_patient_intake_signature_repository,
    get_patient_repository,
    get_payer_repository,
)
from ..request_context import extract_request_context
from ..services.audit_service import AuditService, get_audit_service
from ..services.patient_intake_artifact_service import (
    DocumentNotUsableError,
    IntakeArtifactService,
    NotACardItemError,
    NotAnUploadItemError,
    WrongSideError,
)
from ..services.patient_intake_assignment_service import (
    AssignmentClosedError,
    CorrectionOutstandingError,
    CorrectionScopeError,
    FrozenResponseError,
    IncompleteFormError,
    IntakeAssignmentService,
    UnpublishedVersionError,
    event_item_ids,
)
from ..services.patient_intake_signature_service import (
    AlreadySignedError,
    IntakeSignatureService,
    NotAffirmedError,
    NotASignableItemError,
    SignerRoleNotAskedError,
    SigningRequest,
    StaleDocumentVersionError,
    TypedNameError,
    UnsignableDocumentError,
)
from ..utcnow import utc_now
from .patient_intake import get_intake_outcome_measure_service

if TYPE_CHECKING:
    from ..intake.completion import Completion
    from ..repositories.patient import PatientRepository

router = APIRouter(prefix="/api/patient/intake", tags=["patient-intake"])

# The clinician's side, under the chart prefix every other per-patient
# surface lives at. A separate router because the two share no dependency:
# this one has a patient id in the path precisely because the caller is not
# the patient.
clinician_router = APIRouter(prefix="/api/patients", tags=["patient-intake"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

# ``AuditService`` is spelled out in the route signatures rather than
# aliased: the route-audit guardrail matches the parameter annotation by
# name.


def get_patient_intake_assignment_service() -> IntakeAssignmentService:
    """The assignment service on whichever principal armed the session.

    No ``get_tenant_context`` dependency, unlike the clinician-only
    services elsewhere: this one is shared by both surfaces, and a patient
    request arms its schema through ``get_patient_context`` instead. Every
    route below already depends on one principal or the other, so the
    ``search_path`` is set either way by the time this runs.
    """
    return IntakeAssignmentService(
        get_patient_intake_assignment_repository(),
        get_intake_packet_repository(),
    )


def get_clinician_intake_assignment_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> IntakeAssignmentService:
    """The same service on a tenant-scoped clinician session."""
    return get_patient_intake_assignment_service()


def get_clinician_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """The patient repository on a tenant-scoped session."""
    return get_patient_repository()


def get_patient_intake_artifact_service() -> IntakeArtifactService:
    """The artifact service on whichever principal armed the session.

    Six repositories because attaching a file is a statement about six
    tables at once: the artifacts, the form the question is on, the
    assignment whose answer it settles, the document it points at, and —
    when a card question also collects the plan — the payer and the
    coverage it lands on. No principal dependency of its own, for the same
    reason the two services above have none: every route that reaches this
    has already been through one, so the ``search_path`` is set by the time
    it runs.
    """
    return IntakeArtifactService(
        get_patient_intake_artifact_repository(),
        get_patient_intake_assignment_repository(),
        get_intake_packet_repository(),
        get_patient_document_repository(),
        get_payer_repository(),
        get_patient_coverage_repository(),
    )


def get_clinician_intake_artifact_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> IntakeArtifactService:
    """The same service on a tenant-scoped clinician session."""
    return get_patient_intake_artifact_service()


def get_patient_intake_signature_service() -> IntakeSignatureService:
    """The signature service on whichever principal armed the session.

    Four repositories because taking a signature is a statement about four
    things at once: the form the question is on, the document it points at,
    the signatures already given, and the answer the signature settles. No
    principal dependency of its own, for the same reason the assignment
    service above has none — every route that reaches this has already been
    through one, so the ``search_path`` is set by the time it runs.
    """
    return IntakeSignatureService(
        get_patient_intake_signature_repository(),
        get_intake_packet_repository(),
        get_intake_document_repository(),
        get_patient_intake_assignment_repository(),
    )


PatientAssignments = Annotated[
    IntakeAssignmentService, Depends(get_patient_intake_assignment_service)
]
PatientSignatures = Annotated[IntakeSignatureService, Depends(get_patient_intake_signature_service)]
PatientArtifacts = Annotated[IntakeArtifactService, Depends(get_patient_intake_artifact_service)]
ClinicianAssignments = Annotated[
    IntakeAssignmentService, Depends(get_clinician_intake_assignment_service)
]
ClinicianArtifacts = Annotated[
    IntakeArtifactService, Depends(get_clinician_intake_artifact_service)
]


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on every patient route here.

    Same bar as the rest of the patient intake surface, for the same
    reason: one factor reaching the wrong person should not open a chart.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


def _progress(completion: Completion) -> IntakeProgressResponse:
    return IntakeProgressResponse(complete=completion.complete, missing=completion.missing)


def _assignment_response(
    service: IntakeAssignmentService,
    assignment: dict[str, object],
    patient_id: str,
) -> IntakeAssignmentResponse:
    """One assignment with its form's name and the progress on it."""
    name, number = _version_label(service, str(assignment["version_id"]))
    return IntakeAssignmentResponse(
        id=str(assignment["id"]),
        version_id=str(assignment["version_id"]),
        packet_name=name,
        version=number,
        status=str(assignment["status"]),
        assigned_at=assignment["assigned_at"],  # type: ignore[arg-type]
        submitted_at=assignment["submitted_at"],  # type: ignore[arg-type]
        receipt_code=_optional_str(assignment.get("receipt_code")),
        progress=_progress(service.progress(assignment, patient_id)),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _version_label(service: IntakeAssignmentService, version_id: str) -> tuple[str, int]:
    """The form's name and version number, read defensively.

    A version whose template has been archived is still perfectly readable
    — archiving takes a form out of circulation without touching what was
    already sent — so nothing here treats a missing name as an error. It
    reads as an unnamed form rather than failing a list somebody needs.
    """
    version = service.version(version_id)
    if version is None:  # pragma: no cover — the assignment's foreign key holds it
        return "Intake", 1
    template = service.template(str(version["template_id"]))
    name = str(template["name"]) if template else "Intake"
    return name, int(version["version"])  # type: ignore[call-overload]


# ---------------------------------------------------------------------------
# Patient surface
# ---------------------------------------------------------------------------


@router.get("/assignments", response_model=list[IntakeAssignmentResponse])
def list_my_assignments(
    patient: CurrentPatient,
    service: PatientAssignments,
    _: None = Depends(subscription_exempt),
) -> list[IntakeAssignmentResponse]:
    """The forms this patient has been asked to fill in, newest first.

    Not audited. A patient reading their own record is not a disclosure —
    the settled principle behind the patient-principal audit model — and a
    row per portal visit would bury the disclosures that do matter.

    Exempt from the subscription gate like every patient route: a patient
    does not hold the practice's subscription, and a form they were asked
    to fill in should not fail for a billing state they cannot see.
    """
    _require_stepped_up(patient)
    return [
        _assignment_response(service, row, patient.patient_id)
        for row in service.list_for_patient(patient.patient_id)
    ]


@router.get("/assignments/{assignment_id}", response_model=IntakeAssignmentDetailResponse)
def get_my_assignment(
    assignment_id: str,
    patient: CurrentPatient,
    service: PatientAssignments,
    artifacts: PatientArtifacts,
    _: None = Depends(subscription_exempt),
) -> IntakeAssignmentDetailResponse:
    """One form, its questions in order, and whatever has been saved so far.

    Carries ``correction`` when the practice has sent named questions back:
    the note they wrote, the questions they named, and which of those have
    not been answered again yet. Its presence is the whole answer to "am I
    being asked to redo something", so the portal never has to infer that
    from a status string.

    Another patient's assignment id is a 404, indistinguishable from an id
    that does not exist — so the surface never confirms that somebody
    else's form is real.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)
    saved = service.answers(assignment_id, patient.patient_id)
    base = _assignment_response(service, assignment, patient.patient_id)
    return IntakeAssignmentDetailResponse(
        **base.model_dump(),
        correction=_correction_response(service, assignment, patient.patient_id),
        items=[
            IntakeAssignmentItemResponse(
                id=str(row["id"]),
                key=str(row["key"]),
                position=int(row["position"]),  # type: ignore[call-overload]
                item_type=str(row["item_type"]),
                required=bool(row["required"]),
                label=_optional_str(row.get("label")),
                help_text=_optional_str(row.get("help_text")),
                config=stored_config(row["config"]),
                value=saved.get(str(row["id"])),
            )
            for row in service.items(str(assignment["version_id"]))
        ],
        artifacts=[
            _artifact_response(row)
            for row in artifacts.list_for_patient(assignment_id, patient.patient_id)
        ],
    )


@router.put(
    "/assignments/{assignment_id}/items/{item_id}",
    response_model=SavedAnswerResponse,
)
def save_my_answer(
    assignment_id: str,
    item_id: str,
    body: SaveAnswerRequest,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> SavedAnswerResponse:
    """Save one answer.

    Idempotent: the same question saved twice updates one row rather than
    accumulating two, so a retry after a dropped connection is safe. The
    first save moves the form from "sent" to "in progress", and a form that
    has been handed in or withdrawn is a 409 rather than a silent no-op.

    A form the practice sent back is open only where they said. A question
    the correction request did not name is a 409 with a sentence of its own
    — it is not that the form is closed, it is that this part of it is
    settled and the rest is what they are waiting on.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)

    try:
        _saved, worth_auditing = service.save_answer(
            assignment, patient.patient_id, item_id, body.value
        )
    except CorrectionScopeError as exc:
        raise ConflictError(
            "Your practice asked you to look at a different question.",
            {"assignment_id": assignment_id, "item_id": item_id},
        ) from exc
    except (AssignmentClosedError, FrozenResponseError) as exc:
        raise ConflictError(
            "This form is no longer open for changes.", {"assignment_id": assignment_id}
        ) from exc
    except LookupError as exc:
        raise NotFoundError("Question not found", {"item_id": item_id}) from exc
    except AnswerError as exc:
        # The message names the question and what to do about it, never
        # the answer — safe to hand back to whoever is reading the form.
        raise UnprocessableEntityError(str(exc), {"item_id": item_id}) from exc

    if worth_auditing:
        # Which question was answered, and not a word of the answer. One
        # row per visit rather than per save: see the service for the
        # window and the audit action for why.
        audit.log_patient_principal_action(
            action=AuditAction.PATIENT_INTAKE_DRAFT_SAVED,
            request=request,
            patient_id=patient.patient_id,
            resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
            resource_id=assignment_id,
            changes={"item_id": item_id},
        )

    # Re-read so the status and the progress are what the save left behind
    # rather than what was there before it.
    current = _own_assignment(service, assignment_id, patient.patient_id)
    return SavedAnswerResponse(
        item_id=item_id,
        saved_at=utc_now(),
        status=str(current["status"]),
        progress=_progress(service.progress(current, patient.patient_id)),
    )


@router.get(
    "/assignments/{assignment_id}/signatures",
    response_model=list[IntakeSignatureResponse],
)
def list_my_signatures(
    assignment_id: str,
    patient: CurrentPatient,
    service: PatientAssignments,
    signatures: PatientSignatures,
    _: None = Depends(subscription_exempt),
) -> list[IntakeSignatureResponse]:
    """What this patient has already signed on this form, oldest first.

    What the signing screen reads to know it is done. Without it the answer
    would survive only as long as the browser that took the signature, and
    somebody coming back to a finished form would be shown an empty one.

    Not audited. A patient reading their own record is not a disclosure —
    the settled principle behind the patient-principal audit model — and the
    signing event itself is already on the record.
    """
    _require_stepped_up(patient)
    _own_assignment(service, assignment_id, patient.patient_id)
    return [
        _signature_response(row)
        for row in signatures.live_for_assignment(assignment_id, patient.patient_id)
    ]


@router.post(
    "/assignments/{assignment_id}/signatures",
    response_model=IntakeSignatureResponse,
    status_code=status.HTTP_201_CREATED,
)
def sign_my_consent_document(
    assignment_id: str,
    body: SignDocumentRequest,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    signatures: PatientSignatures,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> IntakeSignatureResponse:
    """Type a name against one of the consent documents on this form.

    Every refusal is a status code with a sentence the person signing can
    act on:

    * ``403`` — this session proved one factor. Signing is the one write on
      this surface that is meant to be read back years later, so it is held
      to the same bar as reading the chart rather than a lower one.
    * ``404`` — an item that is not on this form, or is not a document to
      sign. The two are indistinguishable on purpose, like every other id
      on this surface.
    * ``409`` — already signed by this role, or a newer version of the
      document has been published and this item asks for a fresh signature,
      or the form has been handed in. All three mean "not now, and not
      because of anything you typed".
    * ``422`` — nothing typed, the box not ticked, or a role this document
      does not ask for.

    What is recorded, and what is not: the audit entry names the document
    version and the role, never the typed name. The name is the signature —
    it lives on the signature row, and a second copy in the compliance log
    would be a person's name in a place no question needs it.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)
    ip_address, user_agent = extract_request_context(request)

    try:
        signature = signatures.sign(
            SigningRequest(
                assignment=assignment,
                patient_id=patient.patient_id,
                item_id=body.item_id,
                signer_role=body.signer_role,
                typed_name=body.typed_name,
                affirmed=body.affirm,
                auth_strength=patient.auth_strength.value,
                session_id=patient.session_id,
                ip=ip_address,
                user_agent=user_agent,
            )
        )
    except NotASignableItemError as exc:
        raise NotFoundError("Document not found", {"item_id": body.item_id}) from exc
    except NotAffirmedError as exc:
        raise UnprocessableEntityError(
            "Tick the box to confirm this is your signature.", {"item_id": body.item_id}
        ) from exc
    except TypedNameError as exc:
        raise UnprocessableEntityError(
            "Type your name to sign this.", {"item_id": body.item_id}
        ) from exc
    except SignerRoleNotAskedError as exc:
        raise UnprocessableEntityError(
            "This document does not ask for that signature.", {"item_id": body.item_id}
        ) from exc
    except AlreadySignedError as exc:
        raise ConflictError("This has already been signed.", {"item_id": body.item_id}) from exc
    except StaleDocumentVersionError as exc:
        raise ConflictError(
            "There is a newer version of this document to read and sign.",
            {"item_id": body.item_id},
        ) from exc
    except UnsignableDocumentError as exc:
        # A form published pointing at a draft, a missing version, or text
        # whose digest no longer matches. Nothing the patient did, and
        # nothing they can fix — so it says who can.
        raise ConflictError(
            "This document isn't ready to sign. Your practice can sort this out.",
            {"item_id": body.item_id},
        ) from exc
    except AssignmentClosedError as exc:
        raise ConflictError(
            "This form is no longer open for changes.", {"assignment_id": assignment_id}
        ) from exc

    # Which text was signed and in what role. Never the name that was typed.
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_CONSENT_SIGNED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        changes={
            "document_version_id": str(signature["document_version_id"]),
            "signer_role": str(signature["signer_role"]),
        },
    )
    return _signature_response(signature)


def _signature_response(signature: dict[str, object]) -> IntakeSignatureResponse:
    """The stored row as the person who signed it is shown it.

    The address and the browser are left behind: they are evidence about the
    request rather than about the agreement, and nothing on this screen has
    a use for them.
    """
    version = str(signature["consent_statement_version"])
    return IntakeSignatureResponse(
        id=str(signature["id"]),
        assignment_id=str(signature["assignment_id"]),
        item_id=str(signature["item_id"]),
        document_version_id=str(signature["document_version_id"]),
        document_digest=str(signature["document_digest"]),
        signer_role=str(signature["signer_role"]),
        signer_typed_name=str(signature["signer_typed_name"]),
        consent_statement_version=version,
        consent_statement=consent_statement(version),
        signed_at=signature["signed_at"],  # type: ignore[arg-type]
        auth_strength=str(signature["auth_strength"]),
        session_id=_optional_str(signature.get("session_id")),
        evidence_digest=str(signature["evidence_digest"]),
    )


@router.post(
    "/assignments/{assignment_id}/submit",
    response_model=IntakeSubmissionResponse,
)
def submit_my_assignment(
    assignment_id: str,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    measures: OutcomeMeasureService = Depends(get_intake_outcome_measure_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> IntakeSubmissionResponse:
    """Hand the form in.

    A 422 listing the questions still outstanding when it is not finished,
    and nothing about the form changes. A 409 when it has already been
    handed in or withdrawn — submitting twice must not mint a second
    receipt for one set of answers, and the second caller is told the form
    is closed rather than quietly given the first receipt back.

    A form the practice sent back is a 422 too while any question they
    named is still unredone, and the message says so rather than talking
    about the form being unfinished — the rest of it was finished the first
    time, which is how it came to be reviewed at all.

    On success the answers stop being drafts, every measure on the form is
    scored onto the chart, and the response carries the receipt.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)
    correcting = service.open_correction(assignment, patient.patient_id) is not None

    try:
        submission = service.submit(assignment, patient.patient_id, measures)
    except CorrectionOutstandingError as exc:
        raise UnprocessableEntityError(
            "Your practice is still waiting on one of these.",
            {"missing": exc.outstanding},
        ) from exc
    except IncompleteFormError as exc:
        raise UnprocessableEntityError(
            "Some questions still need an answer.", {"missing": exc.missing}
        ) from exc
    except AssignmentClosedError as exc:
        raise ConflictError(
            "This form has already been handed in.", {"assignment_id": assignment_id}
        ) from exc

    # Which measures were on the form, and not a word of what was answered.
    # The same action the fixed intake form writes, because it is the same
    # event: this patient handed their intake in.
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_INTAKE_SUBMITTED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        changes={"instruments": [m.instrument for m in submission.measures]},
    )
    if correcting:
        # Beside the submission rather than instead of it: one says a form
        # arrived, the other that what the practice asked for was done, and
        # a reader of the log wants both.
        audit.log_patient_principal_action(
            action=AuditAction.PATIENT_INTAKE_CORRECTED,
            request=request,
            patient_id=patient.patient_id,
            resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
            resource_id=assignment_id,
        )

    submitted = submission.assignment
    return IntakeSubmissionResponse(
        assignment_id=assignment_id,
        version_id=str(submitted["version_id"]),
        submitted_at=submitted["submitted_at"],  # type: ignore[arg-type]
        receipt_code=str(submitted["receipt_code"]),
        measures=[
            SubmittedMeasureResponse(
                id=m.id,
                instrument=m.instrument,
                total_score=m.total_score,
                severity=m.severity,
            )
            for m in submission.measures
        ],
        notes=submission.notes,
    )


def _correction_response(
    service: IntakeAssignmentService, assignment: dict[str, object], patient_id: str
) -> IntakeCorrectionResponse | None:
    """What the practice has asked this patient to redo, if anything.

    ``None`` unless the form is open for corrections, so the portal reads
    presence rather than comparing a status string. ``outstanding`` is
    computed from the rows here, like every other statement about progress
    on this surface — a client never works out for itself whether the form
    can go back.
    """
    correction = service.open_correction(assignment, patient_id)
    if correction is None:
        return None
    return IntakeCorrectionResponse(
        requested_at=correction["created_at"],  # type: ignore[arg-type]
        note=_optional_str(correction.get("note_to_patient")),
        item_ids=event_item_ids(correction),
        outstanding=service.outstanding_corrections(assignment, patient_id, correction),
    )


def _own_assignment(
    service: IntakeAssignmentService, assignment_id: str, patient_id: str
) -> dict[str, object]:
    """The assignment iff it is the calling patient's, else 404."""
    assignment = service.get_for_patient(assignment_id, patient_id)
    if assignment is None:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})
    return assignment


def _artifact_response(row: dict[str, object]) -> IntakeArtifactResponse:
    side = row.get("side")
    return IntakeArtifactResponse(
        id=str(row["id"]),
        assignment_id=str(row["assignment_id"]),
        item_id=str(row["item_id"]),
        document_id=str(row["document_id"]),
        side=str(side) if side is not None else None,
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# The files a form asked for
# ---------------------------------------------------------------------------
#
# A patient uploads through the document surface they already have, and then
# says which question the file answers. Two routes, and the shape of both is
# the same point: the request names a document, and the server decides what
# that means. Nothing a caller sends becomes the question's answer — that is
# written from the rows afterwards, by the service.


@router.post(
    "/assignments/{assignment_id}/artifacts",
    response_model=ArtifactWriteResponse,
    status_code=status.HTTP_201_CREATED,
)
def attach_my_artifact(
    assignment_id: str,
    body: AttachArtifactRequest,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    artifacts: PatientArtifacts,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> ArtifactWriteResponse:
    """Say that a file already uploaded answers one of this form's questions.

    Every refusal is a status code with a sentence the person filling the
    form in can act on:

    * ``403`` — this session proved one factor, like every route here.
    * ``404`` — a form, a question or a document that is not this patient's
      to reach. All three answer the same way, so no id on this surface can
      be used to find out what exists.
    * ``409`` — that side of the card already has a photo, or this file
      already answers something, or the form has been handed in. All three
      mean "not now, and not because of anything you sent".
    * ``422`` — a question that does not ask for a file, or a side of a card
      this question did not ask for.

    What is recorded: which document arrived and which side of a card it is.
    Never the filename — people name files after what is in them.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)

    try:
        artifact = artifacts.attach(
            assignment,
            patient.patient_id,
            body.item_id,
            body.document_id,
            body.side,
        )
    except AssignmentClosedError as exc:
        raise ConflictError(
            "This form is no longer open for changes.", {"assignment_id": assignment_id}
        ) from exc
    except LookupError as exc:
        raise NotFoundError("Question not found", {"item_id": body.item_id}) from exc
    except NotAnUploadItemError as exc:
        raise UnprocessableEntityError(
            "This question does not ask for a file.", {"item_id": body.item_id}
        ) from exc
    except WrongSideError as exc:
        raise UnprocessableEntityError(
            "Say which side of the card this photo is.", {"item_id": body.item_id}
        ) from exc
    except DocumentNotUsableError as exc:
        # An id that was never uploaded, one that belongs to somebody else,
        # and one whose upload never finished all land here and all answer
        # the same way.
        raise NotFoundError("File not found", {"document_id": body.document_id}) from exc
    except ArtifactSlotTakenError as exc:
        raise ConflictError(
            "There is already a photo of that side.", {"item_id": body.item_id}
        ) from exc
    except DocumentAlreadyAttachedError as exc:
        raise ConflictError(
            "That file is already on this form.", {"document_id": body.document_id}
        ) from exc

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_INTAKE_ARTIFACT_UPLOADED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        session_id=patient.session_id,
        changes={
            "item_id": body.item_id,
            "document_id": body.document_id,
            "side": body.side,
        },
    )
    return _artifact_write_response(service, artifacts, assignment_id, patient, artifact)


@router.delete(
    "/assignments/{assignment_id}/artifacts/{artifact_id}",
    response_model=ArtifactWriteResponse,
)
def remove_my_artifact(
    assignment_id: str,
    artifact_id: str,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    artifacts: PatientArtifacts,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> ArtifactWriteResponse:
    """Take a photo back off a form that has not been handed in.

    A ``409`` once the form is in: what was submitted stays submitted, and
    the way to correct it afterwards is to talk to the practice rather than
    to edit the record. A ``404`` for an artifact that is not this
    patient's, indistinguishable from one that never existed.

    The file itself goes with the row. It was attached to a form still
    being filled in, so it was never sent to the practice, and the
    practice's record of what arrived has nothing to keep.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)

    try:
        artifact = artifacts.remove(assignment, patient.patient_id, artifact_id)
    except AssignmentClosedError as exc:
        raise ConflictError(
            "This form has already been handed in.", {"assignment_id": assignment_id}
        ) from exc
    except LookupError as exc:
        raise NotFoundError("File not found", {"artifact_id": artifact_id}) from exc

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_INTAKE_ARTIFACT_REMOVED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        session_id=patient.session_id,
        changes={
            "item_id": str(artifact["item_id"]),
            "document_id": str(artifact["document_id"]),
        },
    )
    return _artifact_write_response(service, artifacts, assignment_id, patient, artifact)


def _artifact_write_response(
    service: IntakeAssignmentService,
    artifacts: IntakeArtifactService,
    assignment_id: str,
    patient: PatientContext,
    artifact: dict[str, object],
) -> ArtifactWriteResponse:
    """The artifact that changed, and where the form stands now.

    Re-reads the assignment rather than reusing the one the route already
    held: attaching a file is what moves a form from "sent" to "in
    progress", and the status on the response has to be what the write left
    behind rather than what was there before it.
    """
    _ = artifacts  # the rows are read back through the assignment's progress
    current = _own_assignment(service, assignment_id, patient.patient_id)
    return ArtifactWriteResponse(
        artifact=_artifact_response(artifact),
        status=str(current["status"]),
        progress=_progress(service.progress(current, patient.patient_id)),
    )


@router.put(
    "/assignments/{assignment_id}/items/{item_id}/coverage",
    response_model=SaveIntakeCoverageResponse,
)
def save_my_intake_coverage(
    assignment_id: str,
    item_id: str,
    body: IntakeCoverage,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    artifacts: PatientArtifacts,
    eligibility: IntakeEligibilityCheck = Depends(get_intake_eligibility_check),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> SaveIntakeCoverageResponse:
    """Put the plan written on the card on file.

    Not the question's answer — the photograph is. These are the details
    printed on the card, and they go where the chart, a claim and an
    eligibility check already read a plan from. There is no second copy on
    the form to disagree with the first.

    When the deployment can ask a payer, a check is queued and this request
    returns without waiting for it: a payer that takes thirty seconds must
    never be the reason somebody's first appointment is late. The verdict
    lands on the coverage record, which is where the chart reads it.

    A ``422`` when the question does not ask for these details, a ``404``
    for a question that is not on this form, a ``409`` once the form is in.

    What is recorded is which coverage row and which payer. Never the
    member id, the group or the subscriber — those are the card, and the
    card is not something a compliance log needs a copy of.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)

    try:
        coverage = artifacts.save_coverage(assignment, patient.patient_id, item_id, body)
    except AssignmentClosedError as exc:
        raise ConflictError(
            "This form is no longer open for changes.", {"assignment_id": assignment_id}
        ) from exc
    except LookupError as exc:
        raise NotFoundError("Question not found", {"item_id": item_id}) from exc
    except (NotAnUploadItemError, NotACardItemError) as exc:
        raise UnprocessableEntityError(
            "This question does not ask for insurance details.", {"item_id": item_id}
        ) from exc

    # The clinician who asked for the form is who the check runs as: a
    # patient principal has no reach into a payer, and the queued job
    # resolves its tenant from that id.
    requested = eligibility(coverage.id, str(assignment["assigned_by"]))

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_COVERAGE_CREATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_COVERAGE,
        resource_id=coverage.id,
        session_id=patient.session_id,
        changes={
            "source": "intake",
            "payer_id": coverage.payer_id,
            "eligibility_requested": requested,
        },
    )
    return SaveIntakeCoverageResponse(coverage_id=coverage.id, eligibility_requested=requested)


# ---------------------------------------------------------------------------
# Clinician surface
# ---------------------------------------------------------------------------


@clinician_router.post(
    "/{patient_id}/intake-assignments",
    response_model=IntakeAssignmentResponse,
)
def assign_intake(
    patient_id: str,
    body: CreateAssignmentRequest,
    request: Request,
    response: Response,
    service: ClinicianAssignments,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeAssignmentResponse:
    """Ask this patient to fill in a version of a form.

    A 201 when the request is new and a 200 when one was already live for
    the same version — sending twice must not leave somebody holding two
    copies of one form, and reissuing portal access is not a second ask.
    An unpublished version is a 422: a form can only be sent frozen, so
    that the questions somebody answered stay readable as they were asked.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    try:
        assignment, created = service.assign(patient_id, body.version_id, user.id)
    except UnpublishedVersionError as exc:
        raise UnprocessableEntityError(
            "Publish this form before sending it.", {"version_id": body.version_id}
        ) from exc
    except LookupError as exc:
        raise NotFoundError("Form not found", {"version_id": body.version_id}) from exc

    if created:
        # Which version was asked for, and nothing about the questions on
        # it. The form holds nobody's answers yet.
        audit.log(
            action=AuditAction.PATIENT_INTAKE_ASSIGNED,
            user=user,
            request=request,
            resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
            resource_id=str(assignment["id"]),
            patient=patient,
            changes={"version_id": body.version_id},
        )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _assignment_response(service, assignment, patient_id)


@clinician_router.get(
    "/{patient_id}/intake-assignments",
    response_model=list[IntakeAssignmentResponse],
)
def list_patient_intake_assignments(
    patient_id: str,
    service: ClinicianAssignments,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
) -> list[IntakeAssignmentResponse]:
    """Which forms this patient was asked for, and how far each one has got.

    No audit row, deliberately, and it is the one read on this surface
    without one. What comes back is which form was sent, when, and a count
    of questions still outstanding — no answer, no patient-authored word,
    nothing the patient wrote. The disclosure is reading what they
    answered, and that route lands with the submission surface.

    A patient with no assignments is a 200 and an empty list, not a 404:
    the chart exists, it just has nothing on this surface yet.
    """
    if patients.get(patient_id, user.id) is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})
    return [
        _assignment_response(service, row, patient_id)
        for row in service.list_for_clinician(patient_id, user.id)
    ]


@clinician_router.get(
    "/{patient_id}/intake-assignments/{assignment_id}",
    response_model=ClinicianIntakeAssignmentDetailResponse,
)
def get_patient_intake_assignment(
    patient_id: str,
    assignment_id: str,
    request: Request,
    service: ClinicianAssignments,
    artifacts: ClinicianArtifacts,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ClinicianIntakeAssignmentDetailResponse:
    """What this patient answered, question by question.

    This is the disclosure the list route deliberately is not. What comes
    back is the patient's own words — why they came, anything they said the
    chart has wrong about them, every answer they gave — so reading it goes
    on the record the way opening a conversation does. The entry carries how
    many answers were disclosed and not one of them.

    An assignment id belonging to another patient's chart is a 404, so the
    path cannot be used to find out whose form an id names.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    assignment = service.get_for_clinician(assignment_id, user.id)
    if assignment is None or str(assignment["patient_id"]) != patient_id:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})

    saved = service.answers_for_clinician(assignment_id, user.id)
    attached = artifacts.list_for_clinician(assignment_id, user.id)
    base = _assignment_response(service, assignment, patient_id)

    audit.log(
        action=AuditAction.PATIENT_INTAKE_SUBMISSION_VIEWED,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"count": len(saved), "artifacts": len(attached)},
    )

    return ClinicianIntakeAssignmentDetailResponse(
        **base.model_dump(),
        patient_id=patient_id,
        items=[
            ClinicianIntakeAnswerResponse(
                id=str(row["id"]),
                key=str(row["key"]),
                position=int(row["position"]),  # type: ignore[call-overload]
                item_type=str(row["item_type"]),
                required=bool(row["required"]),
                label=_optional_str(row.get("label")),
                help_text=_optional_str(row.get("help_text")),
                config=stored_config(row["config"]),
                value=saved.get(str(row["id"])),
            )
            for row in service.items(str(assignment["version_id"]))
        ],
        artifacts=[_artifact_response(row) for row in attached],
    )


@clinician_router.post(
    "/{patient_id}/intake-assignments/{assignment_id}/withdraw",
    response_model=IntakeAssignmentResponse,
)
def withdraw_intake_assignment(
    patient_id: str,
    assignment_id: str,
    request: Request,
    service: ClinicianAssignments,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeAssignmentResponse:
    """Stop asking for this form.

    Never a delete: whatever the patient already answered stays readable,
    and the row records that the practice stopped asking. A later save from
    the portal is refused with a 409.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    withdrawn = service.withdraw(assignment_id, user.id)
    if withdrawn is None or str(withdrawn["patient_id"]) != patient_id:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})

    audit.log(
        action=AuditAction.PATIENT_INTAKE_WITHDRAWN,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"version_id": str(withdrawn["version_id"])},
    )
    return _assignment_response(service, withdrawn, patient_id)


# Shared with the review surface next door, which renders the same three
# shapes out of the same rows. Aliases rather than a rename, so the call
# sites above stay as they read and the seam is one place rather than
# scattered underscores crossing a module boundary.
assignment_response = _assignment_response
signature_response = _signature_response
optional_str = _optional_str

__all__ = [
    "assignment_response",
    "clinician_router",
    "get_clinician_intake_artifact_service",
    "get_clinician_intake_assignment_service",
    "get_clinician_patient_repository",
    "get_patient_intake_artifact_service",
    "get_patient_intake_assignment_service",
    "get_patient_intake_signature_service",
    "optional_str",
    "router",
    "signature_response",
]
