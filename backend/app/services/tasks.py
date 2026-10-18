# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What both task surfaces do: ``/api/tasks`` and the ``/api/compliance`` alias.

The two routers are over the same rows and differ only in the shape they
answer with. Everything that decides something — which compliance template a
task may follow, when a repeating task comes back, who may touch an evidence
document — lives here once, so the alias cannot drift from the real thing
while both exist.
"""

from __future__ import annotations

import calendar
import contextlib
import uuid
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING

from fastapi import Depends, UploadFile

from ..api_errors import BadRequestError, NotFoundError, ServerError
from ..compliance import Edition, get_template, list_templates_for_edition
from ..repositories.postgres.compliance_document import ComplianceDocument
from ..services.compliance_storage import (
    ComplianceStorageBackend,
    ComplianceStorageNotConfiguredError,
)
from ..settings import Settings, get_settings
from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ..models import User
    from ..repositories.postgres.compliance_document import PostgresComplianceDocumentRepository
    from ..repositories.postgres.task import PostgresTaskRepository, Task

#: ``item_type`` on a task that follows no compliance template.
PLAIN_TASK_TYPE = "task"

#: The template a compliance task added by hand, with a title of its own, follows.
CUSTOM_COMPLIANCE_TYPE = "custom"

#: MIME types an evidence document may be. PDF is the usual format
#: (certificates, BAAs, declarations pages); PNG/JPEG cover phone photos.
DOCUMENT_ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/png", "image/jpeg"}
)

#: Maximum length of the free-form document_type field (matches the column).
DOCUMENT_TYPE_MAX_LEN = 50

#: How far a deep chain of steps is followed when checking for a cycle. A
#: plan is a handful of levels at most; this only bounds a corrupt chain.
_MAX_PARENT_DEPTH = 32


def current_edition() -> Edition:
    return get_settings().pablo_edition


def validate_compliance_type(item_type: str, user: User) -> None:
    """Refuse a compliance task whose template this deployment or user can't see."""
    template = get_template(item_type)
    edition = current_edition()
    if template is None:
        raise BadRequestError(
            f"Unknown item_type '{item_type}'",
            {"allowed": [t.item_type for t in list_templates_for_edition(edition)]},
            code="UNKNOWN_ITEM_TYPE",
        )
    # Refuse to create instances of templates the current edition doesn't see —
    # otherwise a Core deployment could carry rows that only render correctly
    # on a paid tier.
    if template not in list_templates_for_edition(edition):
        raise BadRequestError(
            f"item_type '{item_type}' is not available on this edition",
            {"edition": edition, "required": template.min_edition},
            code="EDITION_GATED",
        )
    # Mirrors the listing filter so the create/update gate agrees with what
    # the wizard surfaces. A None provider_type means no restriction.
    if user.provider_type is not None and user.provider_type not in template.provider_types:
        raise BadRequestError(
            f"item_type '{item_type}' is not available for your provider type",
            {"provider_type": user.provider_type, "required": list(template.provider_types)},
            code="PROVIDER_TYPE_GATED",
        )


def validate_parent(
    repo: PostgresTaskRepository, user_id: str, task_id: str | None, parent_task_id: str | None
) -> None:
    """A parent must be the caller's own task, and never the task or one of its steps."""
    if parent_task_id is None:
        return
    if parent_task_id == task_id:
        raise BadRequestError("A task cannot be a step of itself", code="PARENT_IS_SELF")
    current: str | None = parent_task_id
    for _ in range(_MAX_PARENT_DEPTH):
        if current is None:
            return
        parent = repo.get(current, user_id)
        if parent is None:
            raise NotFoundError("Parent task not found")
        if task_id is not None and parent.parent_task_id == task_id:
            raise BadRequestError(
                "A task cannot be a step of one of its own steps", code="PARENT_CYCLE"
            )
        current = parent.parent_task_id
    raise BadRequestError("Too many levels of steps", code="PARENT_TOO_DEEP")


def _add_months(day: date, months: int) -> date:
    """``day`` moved ``months`` later, kept inside the month it lands in (Jan 31 → Feb 28)."""
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def next_due_date(due: date, repeat_rule: str) -> date:
    if repeat_rule == "weekly":
        return due + timedelta(days=7)
    if repeat_rule == "monthly":
        return _add_months(due, 1)
    if repeat_rule == "quarterly":
        return _add_months(due, 3)
    if repeat_rule == "yearly":
        return _add_months(due, 12)
    raise ValueError(f"unknown repeat rule {repeat_rule!r}")


def complete_task(repo: PostgresTaskRepository, task: Task) -> Task:
    """Mark ``task`` done; a dated repeating task files its next occurrence.

    The next one is a new task rather than the same row with a later date, so
    the one just finished stays on record as done, on the day it was done.
    An undated repeating task has no date to repeat from and files nothing.
    """
    now = utc_now()
    task.completed_at = now
    task.updated_at = now
    done = repo.update(task)
    if task.repeat_rule is not None and task.due_date is not None:
        repo.create(
            replace(
                task,
                id=str(uuid.uuid4()),
                due_date=next_due_date(task.due_date, task.repeat_rule),
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
    return done


# ---------------------------------------------------------------------------
# Evidence documents
# ---------------------------------------------------------------------------
# Every document operation checks that the task it hangs off belongs to the
# caller before reading or writing a document row — the app-layer twin of the
# row policy on the task table.


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


def _not_configured() -> ServerError:
    return ServerError(
        "Compliance document storage is not configured on this deployment",
        code="STORAGE_NOT_CONFIGURED",
    )


async def upload_document(
    *,
    task_id: str,
    file: UploadFile,
    document_type: str,
    description: str | None,
    user: User,
    repo: PostgresTaskRepository,
    doc_repo: PostgresComplianceDocumentRepository,
    storage: ComplianceStorageBackend,
    settings: Settings,
    not_found_message: str,
) -> ComplianceDocument:
    if repo.get(task_id, user.id) is None:
        raise NotFoundError(not_found_message)

    # Validate MIME type before reading the body so a bad request fails fast.
    mime_type = file.content_type or ""
    if mime_type not in DOCUMENT_ALLOWED_MIME_TYPES:
        raise BadRequestError(
            "Unsupported file type",
            {"mime_type": mime_type, "allowed": sorted(DOCUMENT_ALLOWED_MIME_TYPES)},
            code="UNSUPPORTED_MIME_TYPE",
        )
    if not document_type or len(document_type) > DOCUMENT_TYPE_MAX_LEN:
        raise BadRequestError(
            f"document_type must be 1-{DOCUMENT_TYPE_MAX_LEN} characters",
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
    # Object key: <user_id>/<task_id>/<doc_id> so storage forensics can
    # confirm per-user isolation without a DB join.
    object_key = f"{user.id}/{task_id}/{doc_id}"
    try:
        storage_uri = storage.put(object_key, data, mime_type)
    except ComplianceStorageNotConfiguredError as exc:
        raise _not_configured() from exc

    doc = ComplianceDocument(
        id=doc_id,
        compliance_item_id=task_id,
        filename=file.filename or "document",
        mime_type=mime_type,
        size_bytes=len(data),
        storage_uri=storage_uri,
        document_type=document_type,
        description=description,
        uploaded_at=utc_now(),
        uploaded_by_user_id=user.id,
    )
    doc_repo.create(doc)
    return doc


def list_documents(
    *,
    task_id: str,
    user: User,
    repo: PostgresTaskRepository,
    doc_repo: PostgresComplianceDocumentRepository,
    not_found_message: str,
) -> list[ComplianceDocument]:
    if repo.get(task_id, user.id) is None:
        raise NotFoundError(not_found_message)
    return doc_repo.list_for_item(task_id)


def _owned_document(
    document_id: str,
    user: User,
    repo: PostgresTaskRepository,
    doc_repo: PostgresComplianceDocumentRepository,
) -> ComplianceDocument:
    doc = doc_repo.get(document_id)
    if doc is None:
        raise NotFoundError("Document not found")
    if doc.compliance_item_id is not None:
        if repo.get(doc.compliance_item_id, user.id) is None:
            raise NotFoundError("Document not found")
    elif doc.uploaded_by_user_id != user.id:
        # Orphaned doc (no task link): fall back to the uploader check.
        raise NotFoundError("Document not found")
    return doc


def open_document(
    *,
    document_id: str,
    user: User,
    repo: PostgresTaskRepository,
    doc_repo: PostgresComplianceDocumentRepository,
    storage: ComplianceStorageBackend,
) -> tuple[ComplianceDocument, Iterator[bytes]]:
    """The caller's document and a stream of its bytes."""
    doc = _owned_document(document_id, user, repo, doc_repo)
    try:
        stream = storage.get_stream(doc.storage_uri)
    except ComplianceStorageNotConfiguredError as exc:
        raise _not_configured() from exc
    except FileNotFoundError as exc:
        raise NotFoundError("Document file not found in storage") from exc
    return doc, stream


def download_filename(doc: ComplianceDocument) -> str:
    """A filename safe to put in a ``Content-Disposition`` header."""
    return doc.filename.replace("\r", "").replace("\n", "").replace('"', "").strip() or "document"


def delete_document(
    *,
    document_id: str,
    user: User,
    repo: PostgresTaskRepository,
    doc_repo: PostgresComplianceDocumentRepository,
    storage: ComplianceStorageBackend,
) -> None:
    """Remove the stored object (best effort) and the row (always)."""
    doc = _owned_document(document_id, user, repo, doc_repo)
    with contextlib.suppress(ComplianceStorageNotConfiguredError):
        storage.delete(doc.storage_uri)
    doc_repo.delete(document_id)
