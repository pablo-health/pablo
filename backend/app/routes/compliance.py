# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``/api/compliance`` — the compliance-item view of the task table.

Compliance items are tasks with ``category = 'compliance'`` (see
:mod:`app.routes.tasks`). This router answers in the shape it always has, so
a client built before tasks existed keeps working, and is kept for one
release while clients move to ``/api/tasks``:

* the list is compliance tasks only — nothing a compliance client cannot
  render;
* get, update, complete and delete reach any of the caller's tasks by id,
  so a link to a task works from either surface;
* a create is a compliance task, entered by hand.

Clinicians track their own licence renewal, malpractice insurance, CAQH
attestation, HIPAA training and NPI here. These are the clinician's own
credentials, not patient PHI, so the routes do not feed the audit log.

Document sub-routes (``/{item_id}/documents``) let a clinician attach an
evidence file (PDF, PNG, JPEG) to a task — a licence copy, an insurance
declarations page. Files are stored via the configured storage backend (GCS
or local filesystem) through :mod:`app.services.compliance_storage`.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..api_errors import NotFoundError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user, get_tenant_context
from ..compliance import ComplianceTemplate, list_templates_for_edition
from ..models import User
from ..repositories import get_compliance_document_repository, get_compliance_item_repository
from ..repositories.postgres.compliance_document import (
    ComplianceDocument,
    PostgresComplianceDocumentRepository,
)
from ..repositories.postgres.task import PostgresComplianceItemRepository, Task
from ..services import tasks as task_service
from ..services.compliance_storage import ComplianceStorageBackend
from ..services.tasks import get_compliance_documents_storage
from ..settings import Settings, get_settings
from ..utcnow import utc_now

# The task table lives in the tenant schema with row-level security keyed on
# ``app.current_user_id``. That GUC is only set as a side effect of
# ``get_tenant_context`` — without it every query fail-closes to zero rows.
# Declaring it as a router-level dependency guarantees the GUC is armed before
# any handler touches the repo, even though the handlers read the user via
# ``get_current_user``.
router = APIRouter(
    prefix="/api/compliance",
    tags=["compliance"],
    dependencies=[Depends(get_tenant_context)],
)

#: Kept importable from here: it is what an older caller overrides in tests.
COMPLIANCE_DOC_ALLOWED_MIME_TYPES = task_service.DOCUMENT_ALLOWED_MIME_TYPES

__all__ = [
    "COMPLIANCE_DOC_ALLOWED_MIME_TYPES",
    "get_compliance_documents_storage",
    "router",
]

_NOT_FOUND = "Compliance item not found"


class ComplianceTemplateResponse(BaseModel):
    item_type: str
    label: str
    description: str
    cadence_days: int | None
    reminder_windows: list[int]
    multi_instance: bool
    min_edition: str
    sort_order: int
    provider_types: list[str]


def template_to_response(t: ComplianceTemplate) -> ComplianceTemplateResponse:
    return ComplianceTemplateResponse(
        item_type=t.item_type,
        label=t.label,
        description=t.description,
        cadence_days=t.cadence_days,
        reminder_windows=list(t.reminder_windows),
        multi_instance=t.multi_instance,
        min_edition=t.min_edition,
        sort_order=t.sort_order,
        provider_types=list(t.provider_types),
    )


class ComplianceItemPayload(BaseModel):
    item_type: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=255)
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ComplianceItemResponse(BaseModel):
    id: str
    item_type: str
    label: str
    due_date: date | None
    notes: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


def _to_response(item: Task) -> ComplianceItemResponse:
    return ComplianceItemResponse(
        id=item.id,
        item_type=item.item_type,
        label=item.label,
        due_date=item.due_date,
        notes=item.notes,
        completed_at=item.completed_at.isoformat() if item.completed_at else None,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


class ComplianceDocumentResponse(BaseModel):
    id: str
    compliance_item_id: str | None
    filename: str
    mime_type: str
    size_bytes: int
    document_type: str
    description: str | None
    uploaded_at: str
    uploaded_by_user_id: str


def doc_to_response(doc: ComplianceDocument) -> ComplianceDocumentResponse:
    return ComplianceDocumentResponse(
        id=doc.id,
        compliance_item_id=doc.compliance_item_id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        document_type=doc.document_type,
        description=doc.description,
        uploaded_at=doc.uploaded_at.isoformat(),
        uploaded_by_user_id=doc.uploaded_by_user_id,
    )


RepoDep = Annotated[PostgresComplianceItemRepository, Depends(get_compliance_item_repository)]
DocRepoDep = Annotated[
    PostgresComplianceDocumentRepository, Depends(get_compliance_document_repository)
]
UserDep = Annotated[User, Depends(get_current_user)]
SubscriptionExemptDep = Annotated[None, Depends(subscription_exempt)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[ComplianceStorageBackend, Depends(get_compliance_documents_storage)]


@router.get("/templates", response_model=list[ComplianceTemplateResponse])
def list_compliance_templates(
    user: UserDep,
    _exempt: SubscriptionExemptDep,
) -> list[ComplianceTemplateResponse]:
    """Return the catalog of trackable items visible to this edition.

    Templates are filtered by both edition and the caller's provider type
    so that, e.g., prescriber-specific items (DEA registration) do not
    appear in a therapist's compliance wizard. A ``None`` provider type
    (not yet set on the profile) returns the full edition-filtered catalog
    for backward compatibility.
    """
    edition = task_service.current_edition()
    return [
        template_to_response(t)
        for t in list_templates_for_edition(edition, provider_type=user.provider_type)
    ]


@router.get("", response_model=list[ComplianceItemResponse])
def list_compliance_items(
    user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep
) -> list[ComplianceItemResponse]:
    """List the caller's compliance tasks, oldest first."""
    return [_to_response(i) for i in repo.list_by_user(user.id)]


@router.post("", response_model=ComplianceItemResponse, status_code=201)
def create_compliance_item(
    payload: ComplianceItemPayload, user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep
) -> ComplianceItemResponse:
    """Create a compliance task for the caller."""
    task_service.validate_compliance_type(payload.item_type, user)
    now = utc_now()
    item = Task(
        id=str(uuid.uuid4()),
        user_id=user.id,
        item_type=payload.item_type,
        label=payload.label,
        due_date=payload.due_date,
        notes=payload.notes,
        completed_at=None,
        created_at=now,
        updated_at=now,
        category="compliance",
        source="manual",
    )
    return _to_response(repo.create(item))


@router.put("/{item_id}", response_model=ComplianceItemResponse)
def update_compliance_item(
    item_id: str,
    payload: ComplianceItemPayload,
    user: UserDep,
    repo: RepoDep,
    _exempt: SubscriptionExemptDep,
) -> ComplianceItemResponse:
    """Replace a task's compliance fields; what this shape doesn't carry is kept."""
    task_service.validate_compliance_type(payload.item_type, user)
    existing = repo.get(item_id, user.id)
    if existing is None:
        raise NotFoundError(_NOT_FOUND)
    existing.item_type = payload.item_type
    existing.label = payload.label
    existing.due_date = payload.due_date
    existing.notes = payload.notes
    existing.updated_at = utc_now()
    return _to_response(repo.update(existing))


@router.post("/{item_id}/complete", response_model=ComplianceItemResponse)
def complete_compliance_item(
    item_id: str, user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep
) -> ComplianceItemResponse:
    """Mark a task done (attestation filed, training renewed)."""
    existing = repo.get(item_id, user.id)
    if existing is None:
        raise NotFoundError(_NOT_FOUND)
    return _to_response(task_service.complete_task(repo, existing))


@router.delete("/{item_id}", status_code=204)
def delete_compliance_item(
    item_id: str, user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep
) -> None:
    if not repo.delete(item_id, user.id):
        raise NotFoundError(_NOT_FOUND)


# ---------------------------------------------------------------------------
# Evidence-document sub-routes
# ---------------------------------------------------------------------------


@router.post("/{item_id}/documents", response_model=ComplianceDocumentResponse, status_code=201)
async def upload_compliance_document(
    item_id: str,
    file: UploadFile,
    document_type: Annotated[str, Form()],
    user: UserDep,
    repo: RepoDep,
    doc_repo: DocRepoDep,
    storage: StorageDep,
    settings: SettingsDep,
    _exempt: SubscriptionExemptDep,
    description: Annotated[str | None, Form()] = None,
) -> ComplianceDocumentResponse:
    """Attach an evidence document to one of the caller's tasks.

    Multipart: ``file`` (PDF, PNG or JPEG), ``document_type`` (a free-form
    label such as ``"license"``) and an optional ``description``.
    ``storage_uri`` is opaque — callers never see the raw URI.
    """
    doc = await task_service.upload_document(
        task_id=item_id,
        file=file,
        document_type=document_type,
        description=description,
        user=user,
        repo=repo,
        doc_repo=doc_repo,
        storage=storage,
        settings=settings,
        not_found_message=_NOT_FOUND,
    )
    return doc_to_response(doc)


@router.get("/{item_id}/documents", response_model=list[ComplianceDocumentResponse])
def list_compliance_documents(
    item_id: str,
    user: UserDep,
    repo: RepoDep,
    doc_repo: DocRepoDep,
    _exempt: SubscriptionExemptDep,
) -> list[ComplianceDocumentResponse]:
    """List evidence documents attached to the caller's task, newest first."""
    docs = task_service.list_documents(
        task_id=item_id, user=user, repo=repo, doc_repo=doc_repo, not_found_message=_NOT_FOUND
    )
    return [doc_to_response(d) for d in docs]


@router.get("/documents/{document_id}/file")
def download_compliance_document(
    document_id: str,
    user: UserDep,
    doc_repo: DocRepoDep,
    storage: StorageDep,
    repo: RepoDep,
    _exempt: SubscriptionExemptDep,
) -> StreamingResponse:
    """Stream an evidence document's bytes, as an attachment, to the task's owner."""
    doc, stream = task_service.open_document(
        document_id=document_id, user=user, repo=repo, doc_repo=doc_repo, storage=storage
    )
    return StreamingResponse(
        stream,
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{task_service.download_filename(doc)}"'
        },
    )


@router.delete("/documents/{document_id}", status_code=204)
def delete_compliance_document(
    document_id: str,
    user: UserDep,
    doc_repo: DocRepoDep,
    storage: StorageDep,
    repo: RepoDep,
    _exempt: SubscriptionExemptDep,
) -> None:
    """Delete an evidence document: the stored object best-effort, the row always."""
    task_service.delete_document(
        document_id=document_id, user=user, repo=repo, doc_repo=doc_repo, storage=storage
    )
