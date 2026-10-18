# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``/api/tasks`` — what the clinician has to do, usually by a date.

Licence renewals and the other compliance reminders are tasks with
``category = 'compliance'``; the clinician's own to-dos sit beside them in
the other categories. A task can be a step of another (``parent_task_id``),
can repeat, and can carry an opaque ``goal_key`` linking it to a goal kept
elsewhere.

These are the clinician's own obligations, not patient records, so like the
compliance routes they do not feed the audit log.

A compliance task follows a template from :mod:`app.compliance`, and its
``item_type`` must be one this deployment and this clinician can see; any
other task's ``item_type`` is ``task``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..api_errors import NotFoundError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user, get_tenant_context
from ..compliance import list_templates_for_edition
from ..models import User
from ..repositories import get_compliance_document_repository, get_task_repository
from ..repositories.postgres.compliance_document import PostgresComplianceDocumentRepository
from ..repositories.postgres.task import PostgresTaskRepository, Task, TaskFilter
from ..services import tasks as task_service
from ..services.compliance_storage import ComplianceStorageBackend
from ..services.tasks import get_compliance_documents_storage
from ..settings import Settings, get_settings
from ..utcnow import utc_now
from .compliance import (
    ComplianceDocumentResponse,
    ComplianceTemplateResponse,
    doc_to_response,
    template_to_response,
)

# Arms ``app.current_user_id`` before any handler touches the table; without it
# the row policy fail-closes every query to zero rows. See the compliance
# router for the same dependency and why it is router-level.
router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_tenant_context)],
)

TaskCategory = Literal["compliance", "practice", "other"]
TaskSource = Literal["manual", "reminder", "plan", "import"]
RepeatRule = Literal["weekly", "monthly", "quarterly", "yearly"]

_NOT_FOUND = "Task not found"


class TaskPayload(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    category: TaskCategory
    #: The compliance template a compliance task follows; ``custom`` when
    #: omitted. Ignored for any other category, whose tasks follow none.
    item_type: str | None = Field(default=None, min_length=1, max_length=50)
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    repeat_rule: RepeatRule | None = None
    parent_task_id: str | None = None
    goal_key: str | None = Field(default=None, min_length=1, max_length=128)
    source: TaskSource = "manual"


class TaskResponse(BaseModel):
    id: str
    label: str
    category: str
    item_type: str
    due_date: date | None
    notes: str | None
    repeat_rule: str | None
    parent_task_id: str | None
    goal_key: str | None
    source: str
    completed_at: str | None
    created_at: str
    updated_at: str


def _to_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        label=task.label,
        category=task.category,
        item_type=task.item_type,
        due_date=task.due_date,
        notes=task.notes,
        repeat_rule=task.repeat_rule,
        parent_task_id=task.parent_task_id,
        goal_key=task.goal_key,
        source=task.source,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        created_at=task.created_at.isoformat(),
        updated_at=task.updated_at.isoformat(),
    )


def _item_type_for(payload: TaskPayload, user: User) -> str:
    if payload.category != "compliance":
        return task_service.PLAIN_TASK_TYPE
    item_type = payload.item_type or task_service.CUSTOM_COMPLIANCE_TYPE
    task_service.validate_compliance_type(item_type, user)
    return item_type


RepoDep = Annotated[PostgresTaskRepository, Depends(get_task_repository)]
DocRepoDep = Annotated[
    PostgresComplianceDocumentRepository, Depends(get_compliance_document_repository)
]
UserDep = Annotated[User, Depends(get_current_user)]
SubscriptionExemptDep = Annotated[None, Depends(subscription_exempt)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[ComplianceStorageBackend, Depends(get_compliance_documents_storage)]


@router.get("/templates", response_model=list[ComplianceTemplateResponse])
def list_task_templates(
    user: UserDep,
    _exempt: SubscriptionExemptDep,
) -> list[ComplianceTemplateResponse]:
    """The compliance templates a compliance task may follow, for this edition and user."""
    edition = task_service.current_edition()
    return [
        template_to_response(t)
        for t in list_templates_for_edition(edition, provider_type=user.provider_type)
    ]


@router.get("", response_model=list[TaskResponse])
def list_tasks(
    user: UserDep,
    repo: RepoDep,
    _exempt: SubscriptionExemptDep,
    category: Annotated[TaskCategory | None, Query()] = None,
    parent_task_id: Annotated[str | None, Query()] = None,
    goal_key: Annotated[str | None, Query()] = None,
    source: Annotated[TaskSource | None, Query()] = None,
) -> list[TaskResponse]:
    """The caller's tasks, oldest first, narrowed by any filter given."""
    filters = TaskFilter(
        category=category, parent_task_id=parent_task_id, goal_key=goal_key, source=source
    )
    return [_to_response(t) for t in repo.list_by_user(user.id, filters)]


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str, user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep
) -> TaskResponse:
    task = repo.get(task_id, user.id)
    if task is None:
        raise NotFoundError(_NOT_FOUND)
    return _to_response(task)


@router.post("", response_model=TaskResponse, status_code=201)
def create_task(
    payload: TaskPayload, user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep
) -> TaskResponse:
    item_type = _item_type_for(payload, user)
    task_service.validate_parent(repo, user.id, None, payload.parent_task_id)
    now = utc_now()
    task = Task(
        id=str(uuid.uuid4()),
        user_id=user.id,
        item_type=item_type,
        label=payload.label,
        due_date=payload.due_date,
        notes=payload.notes,
        completed_at=None,
        created_at=now,
        updated_at=now,
        category=payload.category,
        source=payload.source,
        parent_task_id=payload.parent_task_id,
        goal_key=payload.goal_key,
        repeat_rule=payload.repeat_rule,
    )
    return _to_response(repo.create(task))


@router.put("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    payload: TaskPayload,
    user: UserDep,
    repo: RepoDep,
    _exempt: SubscriptionExemptDep,
) -> TaskResponse:
    """Replace a task's editable fields. Where it came from (``source``) is kept."""
    existing = repo.get(task_id, user.id)
    if existing is None:
        raise NotFoundError(_NOT_FOUND)
    existing.item_type = _item_type_for(payload, user)
    task_service.validate_parent(repo, user.id, task_id, payload.parent_task_id)
    existing.label = payload.label
    existing.category = payload.category
    existing.due_date = payload.due_date
    existing.notes = payload.notes
    existing.repeat_rule = payload.repeat_rule
    existing.parent_task_id = payload.parent_task_id
    existing.goal_key = payload.goal_key
    existing.updated_at = utc_now()
    return _to_response(repo.update(existing))


@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(
    task_id: str, user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep
) -> TaskResponse:
    """Mark a task done. A dated repeating task files its next occurrence."""
    existing = repo.get(task_id, user.id)
    if existing is None:
        raise NotFoundError(_NOT_FOUND)
    return _to_response(task_service.complete_task(repo, existing))


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str, user: UserDep, repo: RepoDep, _exempt: SubscriptionExemptDep) -> None:
    """Delete a task, and with it any steps it has."""
    if not repo.delete(task_id, user.id):
        raise NotFoundError(_NOT_FOUND)


# ---------------------------------------------------------------------------
# Evidence documents — the same rail the compliance router serves
# ---------------------------------------------------------------------------


@router.post("/{task_id}/documents", response_model=ComplianceDocumentResponse, status_code=201)
async def upload_task_document(
    task_id: str,
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
    """Attach an evidence document (PDF, PNG or JPEG) to one of the caller's tasks."""
    doc = await task_service.upload_document(
        task_id=task_id,
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


@router.get("/{task_id}/documents", response_model=list[ComplianceDocumentResponse])
def list_task_documents(
    task_id: str,
    user: UserDep,
    repo: RepoDep,
    doc_repo: DocRepoDep,
    _exempt: SubscriptionExemptDep,
) -> list[ComplianceDocumentResponse]:
    """Evidence documents on the caller's task, newest first."""
    docs = task_service.list_documents(
        task_id=task_id, user=user, repo=repo, doc_repo=doc_repo, not_found_message=_NOT_FOUND
    )
    return [doc_to_response(d) for d in docs]


@router.get("/documents/{document_id}/file")
def download_task_document(
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
def delete_task_document(
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
