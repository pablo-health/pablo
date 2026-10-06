# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Draft a session's note again, with new inputs or more from the clinician.

The request marks the note as being redrafted and answers ``202``; the model
call runs on the same queue as the first draft
(``/api/internal/jobs/redraft-note``). Poll ``GET /api/sessions/{id}``: the
note's ``status`` is ``processing`` while it runs, ``complete`` with the new
draft after, and ``failed`` (content unchanged) if it didn't finish.
"""

from __future__ import annotations

import contextvars
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..auth.service import require_baa_acceptance, require_cloud_tasks_invoker
from ..db import arm_current_user_id, get_db_session, set_tenant_schema
from ..db.tenant_session import tenant_db_session
from ..jobs.task_queue import enqueue
from ..models import AuditAction, NoteResponse, User
from ..models.notes import RedraftNoteRequest  # noqa: TC001 — runtime annotation
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
    UserRepository,
    get_user_repository,
)
from ..repositories import get_notes_repository as _notes_repo_factory
from ..repositories import get_patient_repository as _patient_repo_factory
from ..repositories import get_session_repository as _session_repo_factory
from ..services import (
    AuditService,
    NoteGenerationService,
    NoteService,
    SessionNotFoundError,
    SOAPGenerationFailedError,
    TransientSOAPGenerationError,
    get_audit_service,
)
from ..services.note_redraft import NoteRedraftService, RedraftNotPendingError
from ..services.session_generation_worker import resolve_tenant_schema_for_user
from ..settings import get_settings
from .notes import get_note_generation_service
from .sessions import (
    get_notes_repository,
    get_patient_repository,
    get_session_repository,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["notes"])

_JOB_PATH = "/api/internal/jobs/redraft-note"


def get_note_redraft_service(
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
) -> NoteRedraftService:
    return NoteRedraftService(
        session_repo, patient_repo, NoteService(notes_repo), note_generation_service
    )


def get_worker_note_redraft_service(
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
) -> NoteRedraftService:
    """The redraft service for the queue worker, which arms its own tenant scope."""
    return NoteRedraftService(
        _session_repo_factory(),
        _patient_repo_factory(),
        NoteService(_notes_repo_factory()),
        note_generation_service,
    )


class RedraftNoteJob(BaseModel):
    """Cloud Tasks payload: opaque ids only, no tenant name and no PHI."""

    session_id: str
    user_id: str
    keep_edits: bool


def enqueue_redraft(
    job: RedraftNoteJob,
    background: BackgroundTasks,
    http_request: Request,
    note_generation_service: NoteGenerationService,
) -> None:
    """Hand the redraft to the queue, or run it here where no queue delivers it.

    The end-to-end stack has no task queue, so where its drafting stand-in is
    configured the job runs after the response, as the first draft does.
    """
    settings = get_settings()
    enqueue(settings.soap_generation_task_queue, _JOB_PATH, job.model_dump())
    if settings.note_generation_base_url:
        background.add_task(
            contextvars.Context().run,
            _run_redraft_in_process,
            job,
            http_request,
            note_generation_service,
        )


@router.post("/api/sessions/{session_id}/note/redraft", status_code=status.HTTP_202_ACCEPTED)
def redraft_session_note(
    session_id: str,
    http_request: Request,
    request: RedraftNoteRequest,
    background: BackgroundTasks,
    user: User = Depends(require_baa_acceptance),
    redraft_service: NoteRedraftService = Depends(get_note_redraft_service),
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Draft the session's note again, optionally with new inputs.

    ``409 NOTE_HAS_EDITS`` when the note has edits and ``edits`` is missing;
    ``409 NOTE_LOCKED`` when it is signed; ``409 NOTE_REDRAFT_IN_PROGRESS``
    while a redraft runs.
    """
    note, keep_edits = redraft_service.start(
        session_id, user.id, note_inputs=request.note_inputs, edits=request.edits
    )
    audit.log_note_action(
        action=AuditAction.NOTE_REDRAFT_REQUESTED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=session_id,
        changes={
            "inputs_changed": request.note_inputs is not None,
            "edits": request.edits.value if request.edits else None,
        },
    )
    enqueue_redraft(
        RedraftNoteJob(session_id=session_id, user_id=user.id, keep_edits=keep_edits),
        background,
        http_request,
        note_generation_service,
    )
    return NoteResponse.from_note(note)


def _is_final_attempt(request: Request) -> bool:
    raw = request.headers.get("X-CloudTasks-TaskRetryCount")
    try:
        retry_count = int(raw) if raw is not None else 0
    except ValueError:
        retry_count = 0
    return retry_count >= get_settings().soap_generation_max_attempts - 1


@router.post(_JOB_PATH, status_code=status.HTTP_200_OK)
def redraft_note_job(
    payload: RedraftNoteJob,
    http_request: Request,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    redraft_service: NoteRedraftService = Depends(get_worker_note_redraft_service),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: run a redraft. Answers like ``generate-soap``: ``503`` retries."""
    schema = resolve_tenant_schema_for_user(payload.user_id)
    if schema is None:
        logger.warning("redraft-note job: no active tenant for session %s", payload.session_id)
        return {"status": "unknown_tenant"}
    db_session = get_db_session()
    set_tenant_schema(db_session, schema)
    arm_current_user_id(db_session, payload.user_id)

    try:
        session, patient, note = redraft_service.run(
            payload.session_id,
            payload.user_id,
            keep_edits=payload.keep_edits,
            transient_is_terminal=_is_final_attempt(http_request),
        )
    except (SessionNotFoundError, RedraftNotPendingError):
        logger.warning("redraft-note job: nothing to redraft for session %s", payload.session_id)
        return {"status": "not_found"}
    except TransientSOAPGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Note generation temporarily unavailable; retrying.",
        ) from exc
    except SOAPGenerationFailedError:
        return {"status": "failed"}

    owner = user_repo.get(payload.user_id)
    if owner is not None:
        audit.log_note_action(
            AuditAction.SESSION_NOTE_GENERATED,
            owner,
            http_request,
            note_id=note.id,
            patient_id=patient.id,
            session_id=session.id,
            changes={"redraft": True, "edits_kept": note.content_edited is not None},
        )
    return {"status": "ok"}


def _run_redraft_in_process(
    job: RedraftNoteJob,
    http_request: Request,
    note_generation_service: NoteGenerationService,
) -> None:
    schema = resolve_tenant_schema_for_user(job.user_id)
    if schema is None:
        logger.warning("in-process redraft: no active tenant for session %s", job.session_id)
        return
    with tenant_db_session(schema, job.user_id):
        redraft_note_job(
            job,
            http_request,
            None,
            get_worker_note_redraft_service(note_generation_service),
            get_user_repository(),
            get_audit_service(),
        )
