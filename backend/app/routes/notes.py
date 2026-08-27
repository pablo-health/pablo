# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Notes API routes (pa-0nx.2 + pa-0nx.3).

Notes are first-class clinical artifacts. These endpoints expose the
note lifecycle (read / edit / finalize) directly,
rather than going through the session route. The session route still
embeds the note for backward compatibility — see ``routes/sessions.py``.

The ``patient_notes_router`` adds the standalone-note creation path —
``POST /api/patients/{patient_id}/notes`` — for notes a clinician
authors without an associated recorded session.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_baa_acceptance,
    require_cloud_tasks_invoker,
)
from ..db import arm_current_user_id, get_db_session, release_db_connection, set_tenant_schema
from ..jobs.task_queue import enqueue
from ..models import (
    AuditAction,
    CreateStandaloneNoteRequest,
    FinalizeNoteRequest,
    NoteResponse,
    PatientNotesListResponse,
    Transcript,
    TranscriptModel,
    UpdateNoteEditsRequest,
    User,
)
from ..notes import (
    NoteTypeAuthorizer,
    NoteTypeRegistry,
    get_default_registry,
    get_note_type_authorizer,
)
from ..repositories import NotesRepository, PatientRepository, UserRepository, get_user_repository
from ..repositories import (
    get_appointment_repository as _appt_repo_factory,
)
from ..repositories import (
    get_notes_repository as _notes_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..scheduling_engine.exceptions import AppointmentNotFoundError
from ..scheduling_engine.services.scheduling import SchedulingService
from ..services import (
    AuditService,
    NoteAlreadyFinalizedError,
    NoteGenerationService,
    NoteNotFoundError,
    NoteService,
    RegistryNoteGenerationService,
    get_audit_service,
)
from ..services.note_generation_service import TransientNoteGenerationError
from ..services.session_generation_worker import resolve_tenant_schema_for_user
from ..settings import get_settings
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notes", tags=["notes"])
patient_notes_router = APIRouter(prefix="/api/patients", tags=["notes"])
internal_jobs_router = APIRouter(tags=["notes"])


def get_notes_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> NotesRepository:
    """Get notes repository scoped to the tenant's database."""
    return _notes_repo_factory()


def get_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """Get patient repository scoped to the tenant's database."""
    return _patient_repo_factory()


def get_note_service(
    notes_repo: NotesRepository = Depends(get_notes_repository),
) -> NoteService:
    """Get note service instance."""
    return NoteService(notes_repo)


def get_note_generation_service() -> NoteGenerationService:
    """Get note generation service for standalone-note dictation flows."""
    return RegistryNoteGenerationService()


def get_appointment_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentRepository:
    """Get appointment repository scoped to the tenant's database."""
    return _appt_repo_factory()


def get_scheduling_service(
    repo: AppointmentRepository = Depends(get_appointment_repository),
) -> SchedulingService:
    """Get scheduling service.

    Used to write visit billing codes entered at note-creation time onto
    the same appointment record the standalone visit-edit surface writes to.
    """
    return SchedulingService(repo)


def get_registry() -> NoteTypeRegistry:
    """Indirection so tests can swap the registry per-request."""
    return get_default_registry()


def get_worker_note_service() -> NoteService:
    """NoteService for the off-request Cloud Tasks worker.

    Built from the raw repository factory — NOT the ``get_tenant_context``-
    scoped ``get_note_service`` above. The worker authenticates as the
    Cloud-Tasks service account, which has no Firebase user session, so it
    must not depend on ``get_tenant_context``. The route handler arms the
    request-scoped DB session's tenant schema (resolved from the job's
    ``user_id``) before this service's repository does any reads or writes —
    same pattern as ``sessions.get_worker_session_service``.
    """
    return NoteService(_notes_repo_factory())


def get_worker_patient_repository() -> PatientRepository:
    """PatientRepository for the off-request Cloud Tasks worker (see get_worker_note_service)."""
    return _patient_repo_factory()


@router.get("/{note_id}")
def get_note(
    note_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Fetch a single note by id."""
    try:
        note = note_service.get_note(note_id, user.id)
    except NoteNotFoundError as exc:
        raise NotFoundError("Note not found", {"note_id": note_id}) from exc

    audit.log_note_action(
        action=AuditAction.SESSION_VIEWED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
    )
    return NoteResponse.from_note(note)


@router.patch("/{note_id}")
def update_note(
    note_id: str,
    http_request: Request,
    request: UpdateNoteEditsRequest,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Persist clinician edits to a note's content."""
    try:
        note = note_service.update_note_edits(note_id, request.content_edited, user.id)
    except NoteNotFoundError as exc:
        raise NotFoundError("Note not found", {"note_id": note_id}) from exc

    audit.log_note_action(
        action=AuditAction.SESSION_UPDATED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes={"changed_fields": ["content_edited"]},
    )
    return NoteResponse.from_note(note)


@router.post("/{note_id}/finalize")
def finalize_note(
    note_id: str,
    http_request: Request,
    request: FinalizeNoteRequest,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Finalize a note — record quality rating + finalized_at."""
    try:
        note = note_service.finalize_note(
            note_id,
            quality_rating=request.quality_rating,
            quality_rating_reason=request.quality_rating_reason,
            quality_rating_sections=(
                [s.value for s in request.quality_rating_sections]
                if request.quality_rating_sections
                else None
            ),
            user_id=user.id,
        )
    except NoteNotFoundError as exc:
        raise NotFoundError("Note not found", {"note_id": note_id}) from exc
    except NoteAlreadyFinalizedError as exc:
        raise ConflictError(
            "Note is already finalized",
            {"note_id": note_id},
            code="NOTE_ALREADY_FINALIZED",
        ) from exc

    audit.log_note_action(
        action=AuditAction.SESSION_FINALIZED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes={"quality_rating": request.quality_rating},
    )
    return NoteResponse.from_note(note)


@patient_notes_router.get(
    "/{patient_id}/notes",
    response_model=PatientNotesListResponse,
)
def list_patient_notes(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientNotesListResponse:
    """List all notes (session-bound + standalone) for a patient."""
    patient = patient_repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    notes = note_service.list_notes_for_patient(patient.id, user.id)
    notes.sort(
        key=lambda n: n.finalized_at or n.created_at,
        reverse=True,
    )
    # This endpoint returns the full SOAP body of every note for the patient,
    # so it is a bulk per-record content read. Emit one note-scoped viewed
    # event per note (resource_type=session, the clinical-artifact family) so
    # the audit-of-record is consistent with how single notes are surfaced via
    # the session detail view. The read-coalescing gate keeps repeats cheap.
    for n in notes:
        audit.log_note_action(
            action=AuditAction.SESSION_VIEWED,
            user=user,
            request=http_request,
            note_id=n.id,
            patient_id=patient.id,
            session_id=n.session_id,
        )
    return PatientNotesListResponse(
        data=[NoteResponse.from_note(n) for n in notes],
        total=len(notes),
    )


@patient_notes_router.post(
    "/{patient_id}/notes",
    status_code=status.HTTP_201_CREATED,
    response_model=NoteResponse,
)
def create_standalone_note(
    patient_id: str,
    http_request: Request,
    http_response: Response,
    request: CreateStandaloneNoteRequest,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    registry: NoteTypeRegistry = Depends(get_registry),
    authorizer: NoteTypeAuthorizer = Depends(get_note_type_authorizer),
    scheduling_service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Create a patient-owned note with no associated recording session.

    This is the original pa-0nx feature: clinicians may author a note
    without ever recording or transcribing a session. If a dictation
    transcript is provided, the note skeleton is persisted as
    ``processing`` and returned with ``202`` — the same generation
    pipeline used by the session-upload path runs off-request on a Cloud
    Tasks worker (``/api/internal/jobs/generate-standalone-note``); poll
    ``GET /api/notes/{id}`` for ``status``. With no dictation, the note is
    stored empty (``201``) for the clinician to fill via PATCH.

    If ``appointment_id`` and any billing-code field are supplied, this is
    also where a clinician codes the visit — the primary coding surface,
    since the session duration and clinical picture are both on screen
    here. The codes are written to the appointment, not the note; omitting
    them leaves the visit's codes exactly as they were.
    """
    if not registry.has(request.note_type):
        raise BadRequestError(
            f"Note type {request.note_type!r} is not registered",
            {"note_type": request.note_type},
            code="UNKNOWN_NOTE_TYPE",
        )

    definition = registry.get(request.note_type)
    if definition.context != "session":
        raise BadRequestError(
            (
                f"Note type {request.note_type!r} has context "
                f"{definition.context!r}; only 'session'-context types "
                "may be created via this endpoint"
            ),
            {"note_type": request.note_type, "context": definition.context},
            code="NOTE_CONTEXT_NOT_ALLOWED",
        )

    if not authorizer.is_allowed(user, request.note_type):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Note type {request.note_type!r} not allowed for this subscription",
        )

    patient = patient_repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    appointment: Appointment | None = None
    if request.appointment_id is not None:
        try:
            appointment = scheduling_service.get_appointment(request.appointment_id, user.id)
        except AppointmentNotFoundError as exc:
            raise NotFoundError(
                "Appointment not found", {"appointment_id": request.appointment_id}
            ) from exc
        if appointment.patient_id != patient.id:
            raise BadRequestError(
                "Appointment does not belong to this patient",
                {"appointment_id": request.appointment_id},
            )

    note = note_service.create_standalone_note(
        patient_id=patient.id,
        note_type=request.note_type,
        content=None,
        content_edited=request.content_edited,
        status="processing" if request.dictation_transcript is not None else "complete",
        user_id=user.id,
    )

    audit.log_note_action(
        action=AuditAction.SESSION_CREATED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=None,
        changes={"note_type": note.note_type, "standalone": True},
    )

    if request.dictation_transcript is not None:
        enqueue(
            get_settings().soap_generation_task_queue,
            "/api/internal/jobs/generate-standalone-note",
            {
                "note_id": note.id,
                "user_id": user.id,
                "note_type": note.note_type,
                "transcript": {
                    "format": request.dictation_transcript.format.value,
                    "content": request.dictation_transcript.content,
                },
            },
            dedup_key=note.id,
        )
        http_response.status_code = status.HTTP_202_ACCEPTED

    if appointment is not None:
        visit_codes = request.model_dump(
            include={
                "service_code",
                "modifiers",
                "unit_count",
                "place_of_service",
                "diagnosis_codes",
            },
            exclude_none=True,
        )
        if visit_codes:
            scheduling_service.update_appointment(appointment.id, user.id, **visit_codes)
            audit.log_appointment_action(
                AuditAction.APPOINTMENT_UPDATED,
                user,
                http_request,
                appointment.id,
                patient_id=appointment.patient_id,
                changes={"changed_fields": sorted(visit_codes.keys())},
            )

    return NoteResponse.from_note(note)


class GenerateStandaloneNoteJob(BaseModel):
    """Cloud Tasks payload for off-request standalone-note generation.

    The dictation transcript travels here because nothing else about the
    note persists it. No tenant schema name (it can identify a tenant);
    the worker re-resolves the schema from ``user_id`` server-side.
    """

    note_id: str
    user_id: str
    note_type: str
    transcript: TranscriptModel


def _is_final_note_generation_attempt(request: Request) -> bool:
    """Whether this is the last Cloud Tasks delivery for a standalone-note job.

    Mirrors ``sessions._is_final_soap_attempt`` — this job runs on the same
    queue (``soap_generation_task_queue``), so it shares that queue's retry
    budget.
    """
    raw = request.headers.get("X-CloudTasks-TaskRetryCount")
    try:
        retry_count = int(raw) if raw is not None else 0
    except ValueError:
        retry_count = 0
    return retry_count >= get_settings().soap_generation_max_attempts - 1


@internal_jobs_router.post(
    "/api/internal/jobs/generate-standalone-note", status_code=status.HTTP_200_OK
)
def generate_standalone_note_job(
    payload: GenerateStandaloneNoteJob,
    http_request: Request,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    note_service: NoteService = Depends(get_worker_note_service),
    patient_repo: PatientRepository = Depends(get_worker_patient_repository),
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: generate content for a ``processing`` standalone note.

    Mirrors ``sessions.generate_soap_job``. Invoked only by Cloud Tasks
    (service-account OIDC, enforced by ``require_cloud_tasks_invoker``), so
    it scopes itself to the job's tenant from ``user_id`` before touching
    any tenant-scoped row, and runs the LLM off the create-note request
    thread.

    Answers ``200`` once the job is accounted for — a success, the
    non-retryable outcomes (unknown tenant, vanished note/patient), and a
    recorded *deterministic* generation failure (the note is durably marked
    ``failed``). A *transient* failure (e.g. an LLM 429) instead returns
    ``503`` so the queue retries with backoff, until the final attempt — at
    which point it is recorded as ``failed`` and answered ``200``.
    """
    schema = resolve_tenant_schema_for_user(payload.user_id)
    if schema is None:
        logger.warning(
            "generate-standalone-note job: no active tenant for user %s — dropping (non-retryable)",
            payload.user_id,
        )
        return {"status": "unknown_tenant"}

    db_session = get_db_session()
    set_tenant_schema(db_session, schema)
    arm_current_user_id(db_session, payload.user_id)

    try:
        note = note_service.get_note(payload.note_id, payload.user_id)
    except NoteNotFoundError:
        logger.warning(
            "generate-standalone-note job: note %s not found — dropping (non-retryable)",
            payload.note_id,
        )
        return {"status": "not_found"}

    patient = patient_repo.get(note.patient_id, payload.user_id)
    if patient is None:
        logger.warning(
            "generate-standalone-note job: patient not found for note %s — dropping "
            "(non-retryable)",
            payload.note_id,
        )
        return {"status": "not_found"}

    transcript = Transcript(
        format=payload.transcript.format.value,
        content=payload.transcript.content,
    )
    # Release the pooled connection before the multi-second LLM call — same
    # seam ``upload_session`` and the old inline dictation path used.
    release_db_connection()
    try:
        generated = note_generation_service.generate_note(
            payload.note_type,
            transcript,
            patient,
            utc_now(),
        )
    except TransientNoteGenerationError:
        if not _is_final_note_generation_attempt(http_request):
            logger.warning(
                "generate-standalone-note job: transient failure for note %s — retrying via queue",
                payload.note_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Note generation temporarily unavailable; retrying.",
            ) from None
        logger.warning(
            "generate-standalone-note job: transient failure for note %s on the final "
            "attempt; marking failed",
            payload.note_id,
        )
        note_service.fail_generation(payload.note_id, payload.user_id)
        return {"status": "failed"}
    except (ValueError, KeyError):
        logger.exception(
            "generate-standalone-note job: generation failed for note %s", payload.note_id
        )
        note_service.fail_generation(payload.note_id, payload.user_id)
        return {"status": "failed"}

    note = note_service.complete_generation(payload.note_id, generated.content, payload.user_id)

    owner = user_repo.get(payload.user_id)
    if owner is not None:
        audit.log_note_action(
            action=AuditAction.SESSION_NOTE_GENERATED,
            user=owner,
            request=http_request,
            note_id=note.id,
            patient_id=note.patient_id,
            session_id=None,
        )
    else:
        logger.warning(
            "generate-standalone-note job: owner %s not found for audit on note %s",
            payload.user_id,
            note.id,
        )
    return {"status": "ok"}
