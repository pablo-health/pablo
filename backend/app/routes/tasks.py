# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``/api/tasks`` — what the clinician has to do, usually by a date.

Licence renewals and the other compliance reminders are tasks with
``category = 'compliance'``; the clinician's own to-dos sit beside them in
the other categories. A task can be a step of another (``parent_task_id``),
can repeat, and can carry an opaque ``goal_key`` linking it to a goal kept
elsewhere.

These are the clinician's own obligations, not patient records. A task can
name the client it is about (``patient_id``), though. Naming one needs the
caller's access to that client, checked by the same lookup the chart uses; an
id the caller cannot open is "not found", never "forbidden", so the answer
does not confirm the client exists. A task stays its owner's whatever it
names. If the owner later loses access to the client, the task still lists,
without the client: the link is kept, and shown again if access returns. A
response that names a client is a view of them and is audited as one; the
routes that return no task (templates, delete, documents) are not.

A compliance task follows a template from :mod:`app.compliance`, and its
``item_type`` must be one this deployment and this clinician can see; any
other task's ``item_type`` is ``task``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..api_errors import NotFoundError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user, get_tenant_context
from ..compliance import list_templates_for_edition
from ..models import AuditAction, Patient, User
from ..repositories import (
    PatientRepository,
    get_compliance_document_repository,
    get_task_repository,
)
from ..repositories.postgres.compliance_document import PostgresComplianceDocumentRepository
from ..repositories.postgres.task import PostgresTaskRepository, Task, TaskFilter
from ..services import AuditService, get_audit_service
from ..services import tasks as task_service
from ..services.compliance_storage import ComplianceStorageBackend
from ..services.tasks import get_compliance_documents_storage
from ..settings import Settings, get_settings
from ..utcnow import utc_now
from ._deps import patient_not_found, visible_patient
from .compliance import (
    ComplianceDocumentResponse,
    ComplianceTemplateResponse,
    doc_to_response,
    template_to_response,
)
from .patients import get_patient_repository

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
#: Where a task may have come from. ``session`` is filed only by accepting
#: something the clinician said they would do in a session, never posted here.
ListedTaskSource = Literal["manual", "reminder", "plan", "import", "session"]
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
    #: The client the task is about; the caller must have access to them.
    patient_id: str | None = None


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
    #: The client the task is about, when the caller can open them; ``None``
    #: for a task about nobody, or about a client the caller no longer sees.
    patient_id: str | None = None
    patient_name: str | None = None


def _to_response(task: Task, clients: Mapping[str, Patient] | None = None) -> TaskResponse:
    client = (clients or {}).get(task.patient_id) if task.patient_id is not None else None
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
        patient_id=client.id if client is not None else None,
        patient_name=client.display_name if client is not None else None,
    )


def _clients(patients: PatientRepository, user_id: str, tasks: list[Task]) -> dict[str, Patient]:
    """The clients these tasks name that ``user_id`` can open, by id."""
    ids = sorted({t.patient_id for t in tasks if t.patient_id is not None})
    return patients.get_multiple(ids, user_id) if ids else {}


def _log_views(
    audit: AuditService, user: User, request: Request, clients: Mapping[str, Patient]
) -> None:
    """A response that names a client shows who they are: one view of each, the
    same entry the client list writes. Repeats within the read window coalesce."""
    for client in clients.values():
        audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request, client)


def _linked_client(
    patients: PatientRepository, user_id: str, wanted: str | None, existing: str | None
) -> str | None:
    """The client a written task names: ``wanted``, once the caller is shown to have
    access to them. A link the caller can no longer see is kept as it is, since they
    cannot have meant to remove what they were never shown."""
    if wanted is not None:
        if wanted != existing:
            visible_patient(patients, wanted, user_id, patient_not_found)
        return wanted
    if existing is not None and patients.get(existing, user_id) is None:
        return existing
    return None


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
PatientsDep = Annotated[PatientRepository, Depends(get_patient_repository)]


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
    request: Request,
    user: UserDep,
    repo: RepoDep,
    patients: PatientsDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
    _exempt: SubscriptionExemptDep,
    category: Annotated[TaskCategory | None, Query()] = None,
    parent_task_id: Annotated[str | None, Query()] = None,
    goal_key: Annotated[str | None, Query()] = None,
    source: Annotated[ListedTaskSource | None, Query()] = None,
    patient_id: Annotated[str | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
) -> list[TaskResponse]:
    """The caller's tasks, oldest first, narrowed by any filter given.

    ``patient_id`` lists the caller's tasks about one client, which needs access
    to that client; ``open_only`` leaves out the ones already done."""
    if patient_id is not None:
        visible_patient(patients, patient_id, user.id, patient_not_found)
    filters = TaskFilter(
        category=category,
        parent_task_id=parent_task_id,
        goal_key=goal_key,
        source=source,
        patient_id=patient_id,
        open_only=open_only,
    )
    tasks = repo.list_by_user(user.id, filters)
    clients = _clients(patients, user.id, tasks)
    with audit.batched():
        _log_views(audit, user, request, clients)
    return [_to_response(t, clients) for t in tasks]


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    request: Request,
    user: UserDep,
    repo: RepoDep,
    patients: PatientsDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
    _exempt: SubscriptionExemptDep,
) -> TaskResponse:
    task = repo.get(task_id, user.id)
    if task is None:
        raise NotFoundError(_NOT_FOUND)
    clients = _clients(patients, user.id, [task])
    with audit.batched():
        _log_views(audit, user, request, clients)
    return _to_response(task, clients)


@router.post("", response_model=TaskResponse, status_code=201)
def create_task(
    payload: TaskPayload,
    request: Request,
    user: UserDep,
    repo: RepoDep,
    patients: PatientsDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
    _exempt: SubscriptionExemptDep,
) -> TaskResponse:
    item_type = _item_type_for(payload, user)
    task_service.validate_parent(repo, user.id, None, payload.parent_task_id)
    patient_id = _linked_client(patients, user.id, payload.patient_id, None)
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
        patient_id=patient_id,
    )
    created = repo.create(task)
    clients = _clients(patients, user.id, [created])
    with audit.batched():
        _log_views(audit, user, request, clients)
    return _to_response(created, clients)


@router.put("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    payload: TaskPayload,
    request: Request,
    user: UserDep,
    repo: RepoDep,
    patients: PatientsDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
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
    existing.patient_id = _linked_client(patients, user.id, payload.patient_id, existing.patient_id)
    existing.updated_at = utc_now()
    updated = repo.update(existing)
    clients = _clients(patients, user.id, [updated])
    with audit.batched():
        _log_views(audit, user, request, clients)
    return _to_response(updated, clients)


@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(
    task_id: str,
    request: Request,
    user: UserDep,
    repo: RepoDep,
    patients: PatientsDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
    _exempt: SubscriptionExemptDep,
) -> TaskResponse:
    """Mark a task done. A dated repeating task files its next occurrence."""
    existing = repo.get(task_id, user.id)
    if existing is None:
        raise NotFoundError(_NOT_FOUND)
    done = task_service.complete_task(repo, existing)
    clients = _clients(patients, user.id, [done])
    with audit.batched():
        _log_views(audit, user, request, clients)
    return _to_response(done, clients)


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
