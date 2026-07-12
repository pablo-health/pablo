# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Compliance reminder and evidence-document routes.

Clinicians track their own license renewal, malpractice insurance, CAQH
attestation, HIPAA training, and NPI here. These items are the clinician's
own credentials, not patient PHI, so the routes do not feed the audit log.

Document sub-routes (``/{item_id}/documents``) let a clinician attach an
evidence file (PDF, PNG, JPEG) to a compliance item — e.g. a license copy
or insurance declarations page. Files are stored via the configured storage
backend (GCS or local filesystem) through :mod:`app.services.compliance_storage`.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..api_errors import BadRequestError, NotFoundError, ServerError
from ..auth.service import get_current_user, get_tenant_context
from ..compliance import (
    ComplianceTemplate,
    Edition,
    get_template,
    list_templates_for_edition,
)
from ..models import User
from ..repositories import get_compliance_document_repository, get_compliance_item_repository
from ..repositories.postgres.compliance_document import (
    ComplianceDocument,
    PostgresComplianceDocumentRepository,
)
from ..repositories.postgres.compliance_item import (
    ComplianceItem,
    PostgresComplianceItemRepository,
)
from ..services.compliance_storage import (
    ComplianceStorageBackend,
    ComplianceStorageNotConfiguredError,
)
from ..settings import Settings, get_settings
from ..utcnow import utc_now

# ``compliance_items`` (and the compliance_* reminder tables) live in the
# tenant schema with row-level security keyed on ``app.current_user_id``.
# That GUC is only set as a side effect of ``get_tenant_context`` — without
# it every query fail-closes to zero rows. Declaring it as a router-level
# dependency guarantees the GUC is armed before any handler touches the repo,
# even though the handlers read the user via ``get_current_user``.
router = APIRouter(
    prefix="/api/compliance",
    tags=["compliance"],
    dependencies=[Depends(get_tenant_context)],
)

# Maximum length of the free-form document_type field (matches the DB column).
_DOCUMENT_TYPE_MAX_LEN = 50


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


def _template_to_response(t: ComplianceTemplate) -> ComplianceTemplateResponse:
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


def _current_edition() -> Edition:
    return get_settings().pablo_edition


def _validate(payload: ComplianceItemPayload, user: User) -> None:
    template = get_template(payload.item_type)
    edition = _current_edition()
    if template is None:
        raise BadRequestError(
            f"Unknown item_type '{payload.item_type}'",
            {"allowed": [t.item_type for t in list_templates_for_edition(edition)]},
            code="UNKNOWN_ITEM_TYPE",
        )
    # Refuse to create instances of templates the current edition doesn't see —
    # otherwise a Core deployment could carry rows that only render correctly
    # on a paid tier.
    if template not in list_templates_for_edition(edition):
        raise BadRequestError(
            f"item_type '{payload.item_type}' is not available on this edition",
            {"edition": edition, "required": template.min_edition},
            code="EDITION_GATED",
        )
    # Refuse to create instances of templates the caller's provider_type
    # cannot see — mirrors the listing filter so the create/update gate is
    # consistent with what the wizard surfaces.  A None provider_type means
    # no restriction (backward-compatible with deployments that don't
    # collect provider type).
    if user.provider_type is not None and user.provider_type not in template.provider_types:
        raise BadRequestError(
            f"item_type '{payload.item_type}' is not available for your provider type",
            {"provider_type": user.provider_type, "required": list(template.provider_types)},
            code="PROVIDER_TYPE_GATED",
        )


def _to_response(item: ComplianceItem) -> ComplianceItemResponse:
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


# Allowed MIME types for compliance evidence documents. Mirrors the patient-
# document whitelist — PDF is the primary format (official certificates, BAAs,
# declarations pages); PNG/JPEG cover phone photos of physical documents.
COMPLIANCE_DOC_ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
)

RepoDep = Annotated[PostgresComplianceItemRepository, Depends(get_compliance_item_repository)]
DocRepoDep = Annotated[
    PostgresComplianceDocumentRepository, Depends(get_compliance_document_repository)
]
UserDep = Annotated[User, Depends(get_current_user)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_compliance_documents_storage(
    settings: Settings = Depends(get_settings),
) -> ComplianceStorageBackend:
    """FastAPI dependency — returns the configured storage backend.

    Tests override this via ``app.dependency_overrides`` to swap in an
    in-memory backend without touching the filesystem or a cloud bucket.
    """
    return ComplianceStorageBackend(
        settings.compliance_documents_storage_root,
        settings=settings,
    )


StorageDep = Annotated[ComplianceStorageBackend, Depends(get_compliance_documents_storage)]


def _doc_to_response(doc: ComplianceDocument) -> ComplianceDocumentResponse:
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


@router.get("/templates", response_model=list[ComplianceTemplateResponse])
def list_compliance_templates(
    user: UserDep,
) -> list[ComplianceTemplateResponse]:
    """Return the catalog of trackable items visible to this edition.

    Templates are filtered by both edition and the caller's provider type
    so that, e.g., prescriber-specific items (DEA registration) do not
    appear in a therapist's compliance wizard. A ``None`` provider type
    (not yet set on the profile) returns the full edition-filtered catalog
    for backward compatibility.
    """
    edition = _current_edition()
    return [
        _template_to_response(t)
        for t in list_templates_for_edition(edition, provider_type=user.provider_type)
    ]


@router.get("", response_model=list[ComplianceItemResponse])
def list_compliance_items(user: UserDep, repo: RepoDep) -> list[ComplianceItemResponse]:
    """List the caller's compliance items, oldest first."""
    return [_to_response(i) for i in repo.list_by_user(user.id)]


@router.post("", response_model=ComplianceItemResponse, status_code=201)
def create_compliance_item(
    payload: ComplianceItemPayload, user: UserDep, repo: RepoDep
) -> ComplianceItemResponse:
    """Create a new compliance item for the caller."""
    _validate(payload, user)
    now = utc_now()
    item = ComplianceItem(
        id=str(uuid.uuid4()),
        user_id=user.id,
        item_type=payload.item_type,
        label=payload.label,
        due_date=payload.due_date,
        notes=payload.notes,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    return _to_response(repo.create(item))


@router.put("/{item_id}", response_model=ComplianceItemResponse)
def update_compliance_item(
    item_id: str, payload: ComplianceItemPayload, user: UserDep, repo: RepoDep
) -> ComplianceItemResponse:
    """Update an existing compliance item (full replace of editable fields)."""
    _validate(payload, user)
    existing = repo.get(item_id, user.id)
    if existing is None:
        raise NotFoundError("Compliance item not found")
    existing.item_type = payload.item_type
    existing.label = payload.label
    existing.due_date = payload.due_date
    existing.notes = payload.notes
    existing.updated_at = utc_now()
    return _to_response(repo.update(existing))


@router.post("/{item_id}/complete", response_model=ComplianceItemResponse)
def complete_compliance_item(item_id: str, user: UserDep, repo: RepoDep) -> ComplianceItemResponse:
    """Mark an item as completed (e.g. attestation done, training renewed)."""
    existing = repo.get(item_id, user.id)
    if existing is None:
        raise NotFoundError("Compliance item not found")
    now = utc_now()
    existing.completed_at = now
    existing.updated_at = now
    return _to_response(repo.update(existing))


@router.delete("/{item_id}", status_code=204)
def delete_compliance_item(item_id: str, user: UserDep, repo: RepoDep) -> None:
    if not repo.delete(item_id, user.id):
        raise NotFoundError("Compliance item not found")


# ---------------------------------------------------------------------------
# Evidence-document sub-routes
# ---------------------------------------------------------------------------
# All document endpoints verify that the target compliance_item belongs to the
# authenticated user before reading or writing a document row. This provides
# the app-layer scoping that mirrors the DB-level RLS on compliance_items.


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
    description: Annotated[str | None, Form()] = None,
) -> ComplianceDocumentResponse:
    """Attach an evidence document to the caller's compliance item.

    Accepts a multipart/form-data body with:
    - ``file`` — the binary upload (PDF, PNG, or JPEG).
    - ``document_type`` — free-form label (e.g. ``"license"``, ``"malpractice_insurance"``).
    - ``description`` — optional human note.

    The file is stored via the configured storage backend (GCS or local
    filesystem). ``storage_uri`` is opaque — callers never see the raw URI.
    """
    # Verify the compliance item belongs to this user before writing anything.
    item = repo.get(item_id, user.id)
    if item is None:
        raise NotFoundError("Compliance item not found")

    # Validate MIME type before reading the body so a bad request fails fast.
    mime_type = file.content_type or ""
    if mime_type not in COMPLIANCE_DOC_ALLOWED_MIME_TYPES:
        raise BadRequestError(
            "Unsupported file type",
            {"mime_type": mime_type, "allowed": sorted(COMPLIANCE_DOC_ALLOWED_MIME_TYPES)},
            code="UNSUPPORTED_MIME_TYPE",
        )

    # Validate document_type length.
    if not document_type or len(document_type) > _DOCUMENT_TYPE_MAX_LEN:
        raise BadRequestError(
            f"document_type must be 1-{_DOCUMENT_TYPE_MAX_LEN} characters",
            {"document_type": document_type},
            code="INVALID_DOCUMENT_TYPE",
        )

    data = await file.read()
    max_bytes = settings.compliance_documents_max_bytes
    if len(data) > max_bytes:
        raise BadRequestError(
            "File too large",
            {"max_bytes": max_bytes, "size_bytes": len(data)},
            code="FILE_TOO_LARGE",
        )
    if len(data) == 0:
        raise BadRequestError("File is empty", code="EMPTY_FILE")

    doc_id = str(uuid.uuid4())
    # Object key: <user_id>/<item_id>/<doc_id> so storage forensics can
    # confirm per-user isolation without a DB join.
    filename = file.filename or "document"
    object_key = f"{user.id}/{item_id}/{doc_id}"
    try:
        storage_uri = storage.put(object_key, data, mime_type)
    except ComplianceStorageNotConfiguredError as exc:
        raise ServerError(
            "Compliance document storage is not configured on this deployment",
            code="STORAGE_NOT_CONFIGURED",
        ) from exc

    now = utc_now()
    doc = ComplianceDocument(
        id=doc_id,
        compliance_item_id=item_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        storage_uri=storage_uri,
        document_type=document_type,
        description=description,
        uploaded_at=now,
        uploaded_by_user_id=user.id,
    )
    doc_repo.create(doc)
    return _doc_to_response(doc)


@router.get("/{item_id}/documents", response_model=list[ComplianceDocumentResponse])
def list_compliance_documents(
    item_id: str,
    user: UserDep,
    repo: RepoDep,
    doc_repo: DocRepoDep,
) -> list[ComplianceDocumentResponse]:
    """List evidence documents attached to the caller's compliance item, newest first."""
    item = repo.get(item_id, user.id)
    if item is None:
        raise NotFoundError("Compliance item not found")
    return [_doc_to_response(d) for d in doc_repo.list_for_item(item_id)]


@router.get("/documents/{document_id}/file")
def download_compliance_document(
    document_id: str,
    user: UserDep,
    doc_repo: DocRepoDep,
    storage: StorageDep,
    # item repo needed to verify the document's parent item belongs to the caller
    repo: RepoDep,
) -> StreamingResponse:
    """Stream the bytes of an evidence document.

    Verifies that the document's parent compliance item belongs to the
    authenticated user before streaming. The ``Content-Disposition`` header
    is set to ``attachment`` so browsers download rather than render in-page.
    """
    doc = doc_repo.get(document_id)
    if doc is None:
        raise NotFoundError("Document not found")

    # App-layer ownership check: verify the parent item belongs to this user.
    if doc.compliance_item_id is not None:
        item = repo.get(doc.compliance_item_id, user.id)
        if item is None:
            raise NotFoundError("Document not found")
    elif doc.uploaded_by_user_id != user.id:
        # Orphaned doc (no item link): fall back to uploader check.
        raise NotFoundError("Document not found")

    try:
        stream = storage.get_stream(doc.storage_uri)
    except ComplianceStorageNotConfiguredError as exc:
        raise ServerError(
            "Compliance document storage is not configured on this deployment",
            code="STORAGE_NOT_CONFIGURED",
        ) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("Document file not found in storage") from exc

    safe_name = (
        doc.filename.replace("\r", "").replace("\n", "").replace('"', "").strip() or "document"
    )
    return StreamingResponse(
        stream,
        media_type=doc.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.delete("/documents/{document_id}", status_code=204)
def delete_compliance_document(
    document_id: str,
    user: UserDep,
    doc_repo: DocRepoDep,
    storage: StorageDep,
    repo: RepoDep,
) -> None:
    """Delete an evidence document, removing the storage object and the DB row.

    Verifies ownership through the parent compliance item before deleting.
    Storage deletion is best-effort; the DB row is always removed.
    """
    doc = doc_repo.get(document_id)
    if doc is None:
        raise NotFoundError("Document not found")

    # Ownership check via parent item.
    if doc.compliance_item_id is not None:
        item = repo.get(doc.compliance_item_id, user.id)
        if item is None:
            raise NotFoundError("Document not found")
    elif doc.uploaded_by_user_id != user.id:
        raise NotFoundError("Document not found")

    # Best-effort storage delete — log warning on failure, still remove the row.
    with contextlib.suppress(ComplianceStorageNotConfiguredError):
        storage.delete(doc.storage_uri)

    doc_repo.delete(document_id)
