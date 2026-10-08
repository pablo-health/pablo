# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Dictate more for a session's note after the recording has stopped.

``POST /api/sessions/{id}/dictations`` takes a short clip and answers ``202``;
transcribing it and applying it run on the generation queue
(``/api/internal/jobs/session-dictation``). An unsigned note is redrafted with
it (poll the session for the note's ``status``); a signed note gets a draft
addendum (poll ``GET /api/sessions/{id}/dictations`` for ``draft_addendum``),
which the clinician signs through ``POST /api/notes/{id}/addenda`` with the
dictation's id.
"""

from __future__ import annotations

import contextvars
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel

from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_baa_acceptance,
    require_cloud_tasks_invoker,
)
from ..chart_proposals.step import ChartProposalStep  # noqa: TC001 — runtime annotation
from ..db import arm_current_user_id, get_db_session, set_tenant_schema
from ..db.tenant_session import tenant_db_session
from ..jobs.task_queue import enqueue
from ..models import AuditAction, User
from ..models.notes import RedraftEdits  # noqa: TC001 — runtime annotation
from ..models.session_dictation import (
    SessionDictation,
    SessionDictationListResponse,
    SessionDictationResponse,
)
from ..rate_limit import get_audio_upload_limiter
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
    UserRepository,
    get_user_repository,
)
from ..repositories import get_notes_repository as _notes_repo_factory
from ..repositories import get_patient_repository as _patient_repo_factory
from ..repositories import get_session_dictation_repository as _dictation_repo_factory
from ..repositories import get_session_repository as _session_repo_factory
from ..repositories.session_dictation import (  # noqa: TC001 — runtime annotation
    SessionDictationRepository,
)
from ..services import (
    AuditService,
    NoteGenerationService,
    NoteService,
    SOAPGenerationFailedError,
    TransientSOAPGenerationError,
    get_audit_service,
)
from ..services.dictation_transcription import get_dictation_transcriber
from ..services.file_storage import file_storage_from_settings
from ..services.note_redraft import NoteRedraftService
from ..services.session_dictation_service import (
    MAX_DICTATION_BYTES,
    MAX_DICTATION_SECONDS,
    DictationNotPendingError,
    SessionDictationService,
    draft_addendum,
)
from ..services.session_generation_worker import resolve_tenant_schema_for_user
from ..settings import get_settings
from .notes import get_note_generation_service, get_worker_proposal_step
from .sessions import (
    _ALLOWED_AUDIO_TYPES,
    _reject_if_not_audio,
    get_notes_repository,
    get_patient_repository,
    get_session_repository,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])

_JOB_PATH = "/api/internal/jobs/session-dictation"


def get_dictation_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> SessionDictationRepository:
    """Session dictations, scoped to the tenant's database."""
    return _dictation_repo_factory()


def _service(
    session_repo: TherapySessionRepository,
    patient_repo: PatientRepository,
    notes_repo: NotesRepository,
    dictation_repo: SessionDictationRepository,
    note_generation_service: NoteGenerationService,
    proposal_step: ChartProposalStep | None = None,
) -> SessionDictationService:
    settings = get_settings()
    note_service = NoteService(notes_repo)
    return SessionDictationService(
        session_repo=session_repo,
        note_service=note_service,
        dictation_repo=dictation_repo,
        redraft_service=NoteRedraftService(
            session_repo,
            patient_repo,
            note_service,
            note_generation_service,
            dictation_repo,
            proposal_step,
        ),
        storage=file_storage_from_settings(settings),
        bucket=settings.transcription_audio_bucket,
        transcriber=get_dictation_transcriber(settings),
    )


def get_session_dictation_service(
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    dictation_repo: SessionDictationRepository = Depends(get_dictation_repository),
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
) -> SessionDictationService:
    return _service(session_repo, patient_repo, notes_repo, dictation_repo, note_generation_service)


def get_worker_session_dictation_service(
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
    proposal_step: ChartProposalStep = Depends(get_worker_proposal_step),
) -> SessionDictationService:
    """The service for the queue worker, which arms its own tenant scope."""
    return _service(
        _session_repo_factory(),
        _patient_repo_factory(),
        _notes_repo_factory(),
        _dictation_repo_factory(),
        note_generation_service,
        proposal_step,
    )


def _response(dictation: SessionDictation) -> SessionDictationResponse:
    return SessionDictationResponse(
        id=dictation.id,
        session_id=dictation.session_id,
        note_id=dictation.note_id,
        status=dictation.status,
        used_as=dictation.used_as,
        duration_seconds=dictation.duration_seconds,
        transcript=dictation.transcript,
        draft_addendum=draft_addendum(dictation),
        addendum_id=dictation.addendum_id,
        created_at=dictation.created_at,
        transcribed_at=dictation.transcribed_at,
    )


class SessionDictationJob(BaseModel):
    """Cloud Tasks payload: opaque ids only, no tenant name and no PHI."""

    dictation_id: str
    user_id: str
    keep_edits: bool


@router.post("/api/sessions/{session_id}/dictations", status_code=status.HTTP_202_ACCEPTED)
async def add_session_dictation(
    session_id: str,
    audio: UploadFile,
    http_request: Request,
    background: BackgroundTasks,
    duration_seconds: int | None = Form(default=None, ge=0, le=MAX_DICTATION_SECONDS + 60),
    edits: RedraftEdits | None = Form(default=None),
    user: User = Depends(require_baa_acceptance),
    dictation_service: SessionDictationService = Depends(get_session_dictation_service),
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionDictationResponse:
    """Add a dictated clip to the session's note.

    ``edits`` says what a redraft of an unsigned, edited note does with the
    edits; it defaults to keeping them. ``501 DICTATION_UNAVAILABLE`` where
    the deployment has nothing to transcribe with.
    """
    get_audio_upload_limiter().check(user.id)
    content_type = (audio.content_type or "application/octet-stream").split(";", 1)[0].strip()
    if content_type not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio type: {content_type}",
        )
    await _reject_if_not_audio(audio, "audio")
    data = await audio.read(MAX_DICTATION_BYTES + 1)
    if len(data) > MAX_DICTATION_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The dictation is too long.",
        )

    dictation, note, keep_edits = dictation_service.start(
        session_id,
        user.id,
        audio=data,
        content_type=content_type,
        duration_seconds=duration_seconds,
        edits=edits,
    )
    audit.log_note_action(
        action=AuditAction.SESSION_DICTATION_ADDED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=session_id,
        changes={"dictation_id": dictation.id, "duration_seconds": duration_seconds},
    )
    job = SessionDictationJob(dictation_id=dictation.id, user_id=user.id, keep_edits=keep_edits)
    settings = get_settings()
    enqueue(settings.soap_generation_task_queue, _JOB_PATH, job.model_dump())
    # No queue on the end-to-end stack: run the job here, after the response.
    if settings.dictation_transcription_base_url:
        background.add_task(
            contextvars.Context().run,
            _run_in_process,
            job,
            http_request,
            note_generation_service,
        )
    return _response(dictation)


@router.get("/api/sessions/{session_id}/dictations")
def list_session_dictations(
    session_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    dictation_service: SessionDictationService = Depends(get_session_dictation_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionDictationListResponse:
    """Everything dictated for the session, oldest first, with any draft addendum."""
    session, dictations = dictation_service.list_for_session(session_id, user.id)
    audit.log_session_action(
        AuditAction.SESSION_DICTATIONS_VIEWED,
        user,
        http_request,
        session,
        changes={"count": len(dictations)},
    )
    return SessionDictationListResponse(data=[_response(d) for d in dictations])


def _is_final_attempt(request: Request) -> bool:
    raw = request.headers.get("X-CloudTasks-TaskRetryCount")
    try:
        retry_count = int(raw) if raw is not None else 0
    except ValueError:
        retry_count = 0
    return retry_count >= get_settings().soap_generation_max_attempts - 1


@router.post(_JOB_PATH, status_code=status.HTTP_200_OK)
def session_dictation_job(
    payload: SessionDictationJob,
    http_request: Request,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    dictation_service: SessionDictationService = Depends(get_worker_session_dictation_service),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: transcribe a dictation, then redraft or offer a draft addendum."""
    schema = resolve_tenant_schema_for_user(payload.user_id)
    if schema is None:
        logger.warning("dictation job: no active tenant for dictation %s", payload.dictation_id)
        return {"status": "unknown_tenant"}
    db_session = get_db_session()
    set_tenant_schema(db_session, schema)
    arm_current_user_id(db_session, payload.user_id)

    try:
        dictation, note = dictation_service.run(
            payload.dictation_id,
            payload.user_id,
            keep_edits=payload.keep_edits,
            transient_is_terminal=_is_final_attempt(http_request),
        )
    except DictationNotPendingError:
        return {"status": "done"}
    except TransientSOAPGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dictation temporarily unavailable; retrying.",
        ) from exc
    except SOAPGenerationFailedError:
        return {"status": "failed"}

    owner = user_repo.get(payload.user_id)
    if note is not None and owner is not None:
        audit.log_note_action(
            AuditAction.SESSION_NOTE_GENERATED,
            owner,
            http_request,
            note_id=note.id,
            patient_id=note.patient_id,
            session_id=dictation.session_id,
            changes={"redraft": True, "dictation_id": dictation.id},
        )
    return {"status": "ok"}


def _run_in_process(
    job: SessionDictationJob,
    http_request: Request,
    note_generation_service: NoteGenerationService,
) -> None:
    schema = resolve_tenant_schema_for_user(job.user_id)
    if schema is None:
        logger.warning("in-process dictation: no active tenant for %s", job.dictation_id)
        return
    with tenant_db_session(schema, job.user_id):
        session_dictation_job(
            job,
            http_request,
            None,
            get_worker_session_dictation_service(note_generation_service),
            get_user_repository(),
            get_audit_service(),
        )
