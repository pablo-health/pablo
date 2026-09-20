# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Building an intake form — the clinician's side of intake.

A practice decides what its own intake asks. These routes are the editor
behind that: templates, the versions of each, and the ordered items on a
version. Nothing here touches a patient, an answer or a submission — a form
is the practice's paperwork, the same questions whoever it is sent to.

That is also why the shape of these routes differs from the rest of the
chart. There is no patient id in any path, no ``has_patient_access`` check
and no per-record disclosure audit, because there is no record about anybody
to disclose. What these routes do carry is the practice's own credential:
an accepted agreement and a tenant context, the same door every other
clinician surface uses.

**Publishing is the only write that is audited, and the only one that is
irreversible.** A published version is frozen, and every form a patient
answers from then on is read back against it. Drafts change all day and
signify nothing; which version went live and when is worth having on the
record, so that one write is logged.

Two status codes carry most of the meaning here. A ``409`` means the version
is published and the edit had to be made on a new one. A ``422`` means the
form does not make sense yet, and it names the item that does not — a
question whose settings are wrong, two questions with the same name, or a
question that depends on an answer nobody has given yet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..intake.items import ItemDraft, stored_config
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.intake_packet_api import (
    CreateTemplateRequest,
    IntakeItemResponse,
    IntakeTemplateResponse,
    IntakeVersionDetailResponse,
    IntakeVersionResponse,
    ReplaceItemsRequest,
    UpdateTemplateRequest,
)
from ..repositories import get_intake_packet_repository
from ..services.audit_service import AuditService, get_audit_service
from ..services.intake_packet_service import (
    IntakePacketService,
    ItemConfigError,
    PublishedVersionError,
)

if TYPE_CHECKING:
    from ..repositories.intake_packet import IntakePacketRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intake", tags=["intake-packets"])


def get_intake_packet_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> IntakePacketService:
    """The packet service on a tenant-scoped session.

    The tenant context is the whole isolation story for this surface: every
    query underneath runs against one practice's schema, and no row here
    belongs to a narrower owner than the practice.
    """
    repo: IntakePacketRepository = get_intake_packet_repository()
    return IntakePacketService(repo)


PacketService = Annotated[IntakePacketService, Depends(get_intake_packet_service)]


def _version_response(row: dict[str, object]) -> IntakeVersionResponse:
    return IntakeVersionResponse(
        id=str(row["id"]),
        version=int(row["version"]),  # type: ignore[call-overload]
        published_at=row["published_at"],  # type: ignore[arg-type]
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


def _item_response(row: dict[str, object]) -> IntakeItemResponse:
    return IntakeItemResponse(
        id=str(row["id"]),
        key=str(row["key"]),
        position=int(row["position"]),  # type: ignore[call-overload]
        item_type=str(row["item_type"]),
        required=bool(row["required"]),
        resign_on_new_version=bool(row["resign_on_new_version"]),
        config=stored_config(row["config"]),
    )


def _template_response(
    service: IntakePacketService, row: dict[str, object]
) -> IntakeTemplateResponse:
    return IntakeTemplateResponse(
        id=str(row["id"]),
        name=str(row["name"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
        archived_at=row["archived_at"],  # type: ignore[arg-type]
        versions=[_version_response(v) for v in service.list_versions(str(row["id"]))],
    )


def _require_template(service: IntakePacketService, template_id: str) -> dict[str, object]:
    row = service.get_template(template_id)
    if row is None:
        raise NotFoundError("Form not found", {"template_id": template_id})
    return row


def _require_version(
    service: IntakePacketService, template_id: str, version_id: str
) -> dict[str, object]:
    """One version of one template.

    The template is checked first so a version id from another practice's
    form — or from another form in this one — is a miss rather than a read.
    """
    _require_template(service, template_id)
    row = service.get_version(version_id)
    if row is None or str(row["template_id"]) != template_id:
        raise NotFoundError("Version not found", {"version_id": version_id})
    return row


@router.get("/templates", response_model=list[IntakeTemplateResponse])
def list_templates(
    service: PacketService,
    include_archived: bool = False,
    _user: User = Depends(require_baa_acceptance),
) -> list[IntakeTemplateResponse]:
    """Every intake form the practice has built."""
    return [
        _template_response(service, row)
        for row in service.list_templates(include_archived=include_archived)
    ]


@router.post(
    "/templates",
    response_model=IntakeTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    body: CreateTemplateRequest,
    service: PacketService,
    user: User = Depends(require_baa_acceptance),
) -> IntakeTemplateResponse:
    """Start a new form. It arrives with an empty first draft to edit."""
    template = service.create_template(body.name, user.id)
    return _template_response(service, template)


@router.get("/templates/{template_id}", response_model=IntakeTemplateResponse)
def get_template(
    template_id: str,
    service: PacketService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeTemplateResponse:
    """One form and its versions."""
    return _template_response(service, _require_template(service, template_id))


@router.patch("/templates/{template_id}", response_model=IntakeTemplateResponse)
def update_template(
    template_id: str,
    body: UpdateTemplateRequest,
    service: PacketService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeTemplateResponse:
    """Rename a form, take it out of circulation, or bring it back.

    Archiving is never a delete: a version somebody filled in has to stay
    readable for as long as their record does.
    """
    _require_template(service, template_id)
    if body.name is not None:
        service.rename_template(template_id, body.name)
    if body.archived is not None:
        service.set_archived(template_id, body.archived)
    return _template_response(service, _require_template(service, template_id))


@router.post(
    "/templates/{template_id}/versions",
    response_model=IntakeVersionDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    template_id: str,
    service: PacketService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeVersionDetailResponse:
    """Start a draft from the current version's questions.

    A form that already has an unpublished draft hands that one back rather
    than stacking a second — there is only ever one thing being edited.
    """
    _require_template(service, template_id)
    draft = service.create_version(template_id)
    if draft is None:  # pragma: no cover — the template was just read
        raise NotFoundError("Form not found", {"template_id": template_id})
    return _version_detail(service, template_id, draft)


@router.get(
    "/templates/{template_id}/versions/{version_id}",
    response_model=IntakeVersionDetailResponse,
)
def get_version(
    template_id: str,
    version_id: str,
    service: PacketService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeVersionDetailResponse:
    """One version and the questions on it, in order."""
    version = _require_version(service, template_id, version_id)
    return _version_detail(service, template_id, version)


@router.put(
    "/templates/{template_id}/versions/{version_id}/items",
    response_model=IntakeVersionDetailResponse,
)
def replace_items(
    template_id: str,
    version_id: str,
    body: ReplaceItemsRequest,
    service: PacketService,
    _user: User = Depends(require_baa_acceptance),
) -> IntakeVersionDetailResponse:
    """Save the whole question list, in the order it is sent.

    Wholesale rather than per-item: the editor holds an ordered list and
    sends the list. A published version is a ``409`` — its questions are
    what somebody's answers were answers to, so changes go on a new version.
    """
    _require_version(service, template_id, version_id)
    drafts = [
        ItemDraft(
            key=item.key,
            item_type=item.item_type,
            required=item.required,
            resign_on_new_version=item.resign_on_new_version,
            config=item.config,
        )
        for item in body.items
    ]
    try:
        service.replace_items(version_id, drafts)
    except PublishedVersionError as exc:
        raise ConflictError(
            "This version is published. Start a new version to make changes.",
            {"version_id": version_id},
        ) from exc
    version = _require_version(service, template_id, version_id)
    return _version_detail(service, template_id, version)


@router.post(
    "/templates/{template_id}/versions/{version_id}/publish",
    response_model=IntakeVersionDetailResponse,
)
def publish_version(
    template_id: str,
    version_id: str,
    request: Request,
    service: PacketService,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeVersionDetailResponse:
    """Freeze this version and start sending it.

    Refuses a form that does not make sense yet, naming the question that
    does not. Nothing is written when it refuses, so the draft is left
    exactly as it was.
    """
    _require_version(service, template_id, version_id)
    try:
        published = service.publish(version_id, user.id)
    except PublishedVersionError as exc:
        raise ConflictError(
            "This version is already published.", {"version_id": version_id}
        ) from exc
    except ItemConfigError as exc:
        raise UnprocessableEntityError(str(exc), {"version_id": version_id}) from exc

    items = service.list_items(version_id)

    # Which version went live and how many questions it asks. Not one of
    # them: a form holds nobody's answers, and the questions are on the rows.
    audit.log(
        action=AuditAction.INTAKE_TEMPLATE_PUBLISHED,
        user=user,
        request=request,
        resource_type=ResourceType.INTAKE_PACKET_VERSION,
        resource_id=version_id,
        changes={
            "template_id": template_id,
            "version": int(published["version"]),  # type: ignore[call-overload]
            "item_count": len(items),
        },
    )

    return _version_detail(service, template_id, published)


def _version_detail(
    service: IntakePacketService, template_id: str, version: dict[str, object]
) -> IntakeVersionDetailResponse:
    base = _version_response(version)
    return IntakeVersionDetailResponse(
        **base.model_dump(),
        template_id=template_id,
        items=[_item_response(row) for row in service.list_items(str(version["id"]))],
    )


__all__ = ["get_intake_packet_service", "router"]
