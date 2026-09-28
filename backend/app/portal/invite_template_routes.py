# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's invitation wording, and what one client's invitation will say.

* ``GET``/``PUT``/``DELETE /api/portal/invite-template`` — CLINICIAN. The
  practice's own wording: read it (or the default), save it, or go back to
  the default.
* ``POST /api/portal/invite-template/preview`` — CLINICIAN. A draft rendered
  for an example client, for the editor's live preview.
* ``POST /api/patients/{id}/portal-invite/preview`` — CLINICIAN. This client's
  invitation exactly as the invite route would send it, with the link
  withheld.

Whether the wording is editable at all is the delivery adapter's to say (see
``RenderedInviteDelivery``). Where it is not, the template routes answer
``editable: false`` and refuse a save, and the client preview answers
``available: false``: an editor or a preview of text that would never be sent
would be showing a clinician something untrue.

The template itself is not PHI — it is text a practice wrote about itself.
The client preview is: it renders a client's first name and email address, so
it is audited as a read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import _resolve_practice_from_email, require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..repositories import get_patient_repository
from ..repositories.patient import PatientRepository  # noqa: TC001
from ..services.audit_service import AuditService, get_audit_service
from .delivery import PortalInviteDelivery, RenderedInviteDelivery
from .factory import get_invite_delivery
from .invite_composer import FormNames, InviteFacts, compose, get_invite_form_names
from .invite_email import (
    DEFAULT_TEMPLATE,
    PLACEHOLDERS,
    PREVIEW_LINK,
    REQUIRED_PLACEHOLDER,
    InviteTemplate,
    template_problems,
)
from .invite_template_store import InviteTemplateStore, get_invite_template_store
from .practice_routes import ensure_practice_slug

router = APIRouter(tags=["patient-portal"])

#: Who the editor's live preview is addressed to. Obviously an example.
EXAMPLE_FACTS_CLIENT = "Alex"
EXAMPLE_FORMS = ["Intake questionnaire"]


class Placeholder(BaseModel):
    name: str
    label: str
    required: bool


class InviteTemplateResponse(BaseModel):
    #: False when this deployment's email channel sends fixed wording only.
    editable: bool
    subject: str
    body: str
    is_default: bool
    placeholders: list[Placeholder]


class InviteTemplateRequest(BaseModel):
    subject: str = Field(max_length=1000)
    body: str = Field(max_length=20_000)


class RenderedPreview(BaseModel):
    subject: str
    text: str
    #: Empty when the draft could be saved as it is.
    problems: list[str]


class ClientInvitePreviewRequest(BaseModel):
    #: Forms about to be sent alongside the invitation, by version id.
    version_ids: list[str] = Field(default_factory=list, max_length=20)


class ClientInvitePreview(BaseModel):
    #: False when this deployment sends fixed wording that cannot be shown.
    available: bool
    to_email: str | None = None
    subject: str | None = None
    text: str | None = None


def _practice_id(user: User) -> str:
    practice = _resolve_practice_from_email(user.email)
    if practice is None:
        raise ConflictError("No practice is associated with this account yet.")
    return practice[0]


def _editable(delivery: PortalInviteDelivery) -> bool:
    return isinstance(delivery, RenderedInviteDelivery)


def _placeholders() -> list[Placeholder]:
    return [
        Placeholder(name=name, label=label, required=name == REQUIRED_PLACEHOLDER)
        for name, label in PLACEHOLDERS.items()
    ]


def _template_response(
    stored: InviteTemplate | None, delivery: PortalInviteDelivery
) -> InviteTemplateResponse:
    template = stored or DEFAULT_TEMPLATE
    return InviteTemplateResponse(
        editable=_editable(delivery),
        subject=template.subject,
        body=template.body,
        is_default=stored is None,
        placeholders=_placeholders(),
    )


@router.get("/api/portal/invite-template", response_model=InviteTemplateResponse)
def get_invite_template(
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[InviteTemplateStore, Depends(get_invite_template_store)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
) -> InviteTemplateResponse:
    """The practice's invitation wording, or the default."""
    return _template_response(store.get(_practice_id(user)), delivery)


@router.put("/api/portal/invite-template", response_model=InviteTemplateResponse)
def save_invite_template(
    body: InviteTemplateRequest,
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[InviteTemplateStore, Depends(get_invite_template_store)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
) -> InviteTemplateResponse:
    """Save the practice's wording. 422 with what to fix; 409 where this
    deployment sends fixed wording, so a save would never be used."""
    if not _editable(delivery):
        raise ConflictError(
            "Invitation wording cannot be changed on this deployment.",
            code="PORTAL_INVITE_WORDING_FIXED",
        )
    template = InviteTemplate(subject=body.subject, body=body.body)
    problems = template_problems(template)
    if problems:
        raise UnprocessableEntityError(
            " ".join(problems), {"problems": problems}, code="PORTAL_INVITE_TEMPLATE_INVALID"
        )
    practice_id = _practice_id(user)
    store.save(practice_id, template)
    return _template_response(store.get(practice_id), delivery)


@router.delete("/api/portal/invite-template", response_model=InviteTemplateResponse)
def reset_invite_template(
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[InviteTemplateStore, Depends(get_invite_template_store)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
) -> InviteTemplateResponse:
    """Go back to the default wording."""
    store.reset(_practice_id(user))
    return _template_response(None, delivery)


@router.post("/api/portal/invite-template/preview", response_model=RenderedPreview)
def preview_invite_template(
    body: InviteTemplateRequest,
    user: Annotated[User, Depends(require_active_subscription)],
) -> RenderedPreview:
    """A draft, rendered for an example client, and what would stop it saving."""
    draft = InviteTemplate(subject=body.subject, body=body.body)
    address = ensure_practice_slug(_practice_id(user))
    rendered = compose(
        draft,
        InviteFacts(
            client_first_name=EXAMPLE_FACTS_CLIENT,
            practice_name=address.display_name,
            forms=EXAMPLE_FORMS,
        ),
        PREVIEW_LINK,
    )
    return RenderedPreview(
        subject=rendered.subject, text=rendered.text, problems=template_problems(draft)
    )


@router.post(
    "/api/patients/{patient_id}/portal-invite/preview",
    response_model=ClientInvitePreview,
    status_code=status.HTTP_200_OK,
)
def preview_client_invite(  # noqa: PLR0913 — FastAPI Depends-injected params are idiomatic
    patient_id: str,
    body: ClientInvitePreviewRequest,
    request: Request,
    user: Annotated[User, Depends(require_active_subscription)],
    patients: Annotated[PatientRepository, Depends(get_patient_repository)],
    store: Annotated[InviteTemplateStore, Depends(get_invite_template_store)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
    form_names: Annotated[FormNames, Depends(get_invite_form_names)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ClientInvitePreview:
    """This client's invitation as it would be sent now, link withheld.

    ``available: false`` where the deployment sends fixed wording, without
    reading the chart at all.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found.")
    if not _editable(delivery):
        return ClientInvitePreview(available=False)

    practice_id = _practice_id(user)
    rendered = compose(
        store.get(practice_id),
        InviteFacts(
            client_first_name=patient.first_name or "",
            practice_name=ensure_practice_slug(practice_id).display_name,
            forms=form_names(patient_id, user.id, body.version_ids),
        ),
        PREVIEW_LINK,
    )
    audit.log(
        AuditAction.PATIENT_PORTAL_INVITE_PREVIEWED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        patient=patient,
    )
    return ClientInvitePreview(
        available=True, to_email=patient.email, subject=rendered.subject, text=rendered.text
    )
