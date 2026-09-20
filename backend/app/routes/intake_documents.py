# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's consent documents — writing them, and reading one to sign.

A practice writes the documents it asks people to agree to: consent to
treatment, a telehealth agreement, a notice of privacy practices. These
routes are the editor behind that, plus the one route the portal calls when
somebody is about to sign.

**Two surfaces, and neither is a chart.** Like the form builder it sits
beside, nothing here belongs to a patient: a consent document is the
practice's own paperwork, the same words whoever reads them. The clinician
routes carry the practice's ordinary credential — an accepted agreement and
a tenant context — and take no patient id. The patient route carries a
patient principal and still returns nothing about that patient; it is here
rather than on the clinician surface because the portal has no clinician
credential to offer, not because the words are anybody's.

**A published version never changes.** That is the rule the whole surface
is shaped around, because a signature records the digest of the text that
was signed. An edit to a published version is a ``409`` and the answer is a
new version, which carries the published text forward to be edited.

**Only published versions leave the practice.** The patient route answers
``404`` for a draft, and the same ``404`` for a document that does not
exist and for one belonging to another practice — so nothing on this
surface confirms that somebody else's document is real.

Publishing is the only write that is audited. Drafts change all day and
signify nothing; which text went live, and when, is what a signature will
be read against.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..intake.consent_statement import CURRENT_CONSENT_STATEMENT_VERSION, consent_statement
from ..intake.documents import render_html
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.intake_document_api import (
    CreateDocumentRequest,
    IntakeDocumentResponse,
    PatientDocumentResponse,
    UpdateDocumentRequest,
)
from ..repositories import get_intake_document_repository
from ..services.audit_service import AuditService, get_audit_service
from ..services.intake_document_service import (
    IntakeDocumentService,
    PublishedDocumentError,
    SignerRoleError,
)

if TYPE_CHECKING:
    from ..repositories.intake_document import IntakeDocumentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intake/documents", tags=["intake-documents"])

# The portal's read of the same rows. A separate router because the two
# surfaces share no dependency: this one is reached with a patient
# principal, which the clinician door does not accept and should not.
patient_router = APIRouter(prefix="/api/patient/intake/documents", tags=["intake-documents"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]


def get_intake_document_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> IntakeDocumentService:
    """The document service on a tenant-scoped clinician session.

    The tenant context is the whole isolation story for this surface: every
    query underneath runs against one practice's schema, and no row here
    belongs to a narrower owner than the practice.
    """
    repo: IntakeDocumentRepository = get_intake_document_repository()
    return IntakeDocumentService(repo)


def get_patient_intake_document_service() -> IntakeDocumentService:
    """The same service on the patient-armed session.

    No ``get_tenant_context`` dependency: a patient principal arms its own
    schema through ``get_patient_context``, which the one route using this
    already depends on.
    """
    return IntakeDocumentService(get_intake_document_repository())


DocumentService = Annotated[IntakeDocumentService, Depends(get_intake_document_service)]
PatientDocumentService = Annotated[
    IntakeDocumentService, Depends(get_patient_intake_document_service)
]


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on the portal route.

    Same bar as the rest of the patient intake surface. A consent document
    is the practice's own text rather than anybody's record, but it is read
    on the way to signing something, and one factor reaching the wrong
    person should not put a signature in front of them.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


def _signer_roles(value: object) -> list[str]:
    """A stored ``signer_roles`` column read back as a list of names.

    The repositories hand rows back as ``dict[str, object]``, so the JSONB
    column arrives as ``object``. Anything that is not a list reads as the
    patient alone rather than raising: the column is a record of what an
    editor sent, and the service is where a role has to be one the engine
    recognises. Same shape as ``app.intake.items.stored_config``.
    """
    return [str(role) for role in value] if isinstance(value, list) else ["patient"]


def _response(row: dict[str, object]) -> IntakeDocumentResponse:
    body = str(row["body_markdown"])
    return IntakeDocumentResponse(
        id=str(row["id"]),
        document_key=str(row["document_key"]),
        title=str(row["title"]),
        body_markdown=body,
        rendered_html=render_html(body),
        version=int(row["version"]),  # type: ignore[call-overload]
        digest=str(row["digest"]),
        published_at=row["published_at"],  # type: ignore[arg-type]
        requires_signature=bool(row["requires_signature"]),
        signer_roles=_signer_roles(row["signer_roles"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


def _require(service: IntakeDocumentService, document_id: str) -> dict[str, object]:
    row = service.get(document_id)
    if row is None:
        raise NotFoundError("Document not found", {"document_id": document_id})
    return row


@router.get("", response_model=list[IntakeDocumentResponse])
def list_documents(
    service: DocumentService,
    published_only: bool = False,
    _user: User = Depends(require_baa_acceptance),
) -> list[IntakeDocumentResponse]:
    """Every document the practice has written, newest version of each.

    ``published_only`` asks a different question, and the form editor is
    why. The default list is what the practice is working on — the newest
    version of each document, draft or not. The form editor needs what can
    be asked for, which is the newest PUBLISHED version of each, and the two
    differ for exactly the document somebody is midway through revising.
    Filtering the default list by ``published_at`` would drop that document
    out of the picker while it is being edited, which is the moment it is
    most likely to already be on a form.
    """
    rows = service.list_published() if published_only else service.list_documents()
    return [_response(row) for row in rows]


@router.post("", response_model=IntakeDocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    body: CreateDocumentRequest,
    service: DocumentService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeDocumentResponse:
    """Start a document. It arrives as an unpublished version 1."""
    try:
        created = service.create(
            title=body.title,
            body_markdown=body.body_markdown,
            requires_signature=body.requires_signature,
            signer_roles=body.signer_roles,
        )
    except SignerRoleError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    return _response(created)


@router.get("/{document_id}", response_model=IntakeDocumentResponse)
def get_document(
    document_id: str,
    service: DocumentService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeDocumentResponse:
    """One version, with the HTML it renders to."""
    return _response(_require(service, document_id))


@router.put("/{document_id}", response_model=IntakeDocumentResponse)
def update_document(
    document_id: str,
    body: UpdateDocumentRequest,
    service: DocumentService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeDocumentResponse:
    """Save a draft's title or text.

    A published version is a ``409``: its words are what somebody's
    signature was a signature to, so changes go on a new version.
    """
    _require(service, document_id)
    try:
        updated = service.update_draft(
            document_id, title=body.title, body_markdown=body.body_markdown
        )
    except PublishedDocumentError as exc:
        raise ConflictError(
            "This version is published. Start a new version to make changes.",
            {"document_id": document_id},
        ) from exc
    return _response(updated)


@router.post("/{document_id}/publish", response_model=IntakeDocumentResponse)
def publish_document(
    document_id: str,
    request: Request,
    service: DocumentService,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeDocumentResponse:
    """Freeze this version so forms can start asking people to sign it."""
    _require(service, document_id)
    try:
        published = service.publish(document_id, user.id)
    except PublishedDocumentError as exc:
        raise ConflictError(
            "This version is already published.", {"document_id": document_id}
        ) from exc

    # Which text went live, identified the way a signature will identify it.
    # The digest rather than the words: a consent document is not PHI, but
    # the audit log is a record of events and not a second copy of the
    # practice's paperwork.
    audit.log(
        action=AuditAction.INTAKE_DOCUMENT_PUBLISHED,
        user=user,
        request=request,
        resource_type=ResourceType.INTAKE_DOCUMENT,
        resource_id=document_id,
        changes={
            "document_key": str(published["document_key"]),
            "version": int(published["version"]),  # type: ignore[call-overload]
            "digest": str(published["digest"]),
        },
    )
    return _response(published)


@router.post(
    "/{document_id}/new-version",
    response_model=IntakeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def new_version(
    document_id: str,
    service: DocumentService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeDocumentResponse:
    """Start a draft from this document's latest text.

    A document that already has an unpublished draft hands that one back
    rather than stacking a second — there is only ever one thing being
    edited.
    """
    _require(service, document_id)
    return _response(service.new_version(document_id))


@patient_router.get("/{document_id}", response_model=PatientDocumentResponse)
def read_document(
    document_id: str,
    patient: CurrentPatient,
    service: PatientDocumentService,
    _: None = Depends(subscription_exempt),
) -> PatientDocumentResponse:
    """The document somebody is being asked to sign.

    Not audited, and not for the usual reason. The rows here are the
    practice's own paperwork: this route discloses nothing about the person
    reading it, and returns the same words to everybody. A patient's
    SIGNATURE is a record about them, and it belongs to the surface that
    takes it rather than to this one.

    A draft is a ``404``, the same answer an id from another practice gets.
    Only what went live can be signed, and the surface never says which of
    the two a missing document was.

    Exempt from the subscription gate like every patient route: a patient
    does not hold the practice's subscription, and paperwork they were
    asked to read should not fail for a billing state they cannot see.
    """
    _require_stepped_up(patient)
    row = service.get(document_id)
    if row is None or row["published_at"] is None:
        raise NotFoundError("Document not found", {"document_id": document_id})
    return PatientDocumentResponse(
        id=str(row["id"]),
        document_key=str(row["document_key"]),
        title=str(row["title"]),
        rendered_html=render_html(str(row["body_markdown"])),
        version=int(row["version"]),  # type: ignore[call-overload]
        digest=str(row["digest"]),
        requires_signature=bool(row["requires_signature"]),
        signer_roles=_signer_roles(row["signer_roles"]),
        # Today's wording, because a signature taken from this screen will
        # record today's version. A signature already taken reads back its
        # own version from its own row.
        consent_statement=consent_statement(),
        consent_statement_version=CURRENT_CONSENT_STATEMENT_VERSION,
    )


__all__ = ["get_intake_document_service", "patient_router", "router"]
