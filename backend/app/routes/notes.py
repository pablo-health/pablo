# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Notes API routes (pa-0nx.2 + pa-0nx.3).

Notes are first-class clinical artifacts. These endpoints expose the
note lifecycle (read / edit / finalize / submit-for-export) directly,
rather than going through the session route. The session route still
embeds the note for backward compatibility — see ``routes/sessions.py``.

The ``patient_notes_router`` adds the standalone-note creation path —
``POST /api/patients/{patient_id}/notes`` — for notes a clinician
authors without an associated recorded session.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    CreateStandaloneNoteRequest,
    FinalizeNoteRequest,
    NoteResponse,
    Transcript,
    UpdateNoteEditsRequest,
    User,
)
from ..notes import (
    NoteTypeAuthorizer,
    NoteTypeRegistry,
    get_default_registry,
    get_note_type_authorizer,
)
from ..repositories import NotesRepository, PatientRepository
from ..repositories import (
    get_notes_repository as _notes_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..services import (
    AuditService,
    MeetingTranscriptionNoteService,
    NoteAlreadyFinalizedError,
    NoteGenerationService,
    NoteNotFinalizedError,
    NoteNotFoundError,
    NoteService,
    get_audit_service,
)
from ..utcnow import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notes", tags=["notes"])
patient_notes_router = APIRouter(prefix="/api/patients", tags=["notes"])


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
    return MeetingTranscriptionNoteService()


def get_registry() -> NoteTypeRegistry:
    """Indirection so tests can swap the registry per-request."""
    return get_default_registry()


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
        note = note_service.get_note(note_id)
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
        note = note_service.update_note_edits(note_id, request.content_edited)
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


@router.post("/{note_id}/submit-export", status_code=status.HTTP_200_OK)
def submit_note_for_export(
    note_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Queue a finalized note for export."""
    try:
        note = note_service.submit_note_for_export(note_id)
    except NoteNotFoundError as exc:
        raise NotFoundError("Note not found", {"note_id": note_id}) from exc
    except NoteNotFinalizedError as exc:
        raise BadRequestError(
            "Note must be finalized before submitting for export",
            {"note_id": note_id},
            code="NOTE_NOT_FINALIZED",
        ) from exc

    audit.log_note_action(
        action=AuditAction.EXPORT_ACTION_TAKEN,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes={"export_status": note.export_status},
    )
    return NoteResponse.from_note(note)


@patient_notes_router.post(
    "/{patient_id}/notes",
    status_code=status.HTTP_201_CREATED,
    response_model=NoteResponse,
)
def create_standalone_note(
    patient_id: str,
    http_request: Request,
    request: CreateStandaloneNoteRequest,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    registry: NoteTypeRegistry = Depends(get_registry),
    authorizer: NoteTypeAuthorizer = Depends(get_note_type_authorizer),
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Create a patient-owned note with no associated recording session.

    This is the original pa-0nx feature: clinicians may author a note
    without ever recording or transcribing a session. If a dictation
    transcript is provided, the same generation pipeline used by the
    session-upload path runs against it; otherwise the note is stored
    empty for the clinician to fill via PATCH.
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

    content: dict[str, object] | None = None
    if request.dictation_transcript is not None:
        transcript = Transcript(
            format=request.dictation_transcript.format.value,
            content=request.dictation_transcript.content,
        )
        try:
            generated = note_generation_service.generate_note(
                request.note_type,
                transcript,
                patient,
                utc_now(),
            )
        except (ValueError, KeyError) as exc:
            logger.exception(
                "Standalone note generation failed for note_type=%s",
                request.note_type,
            )
            raise BadRequestError(
                "Note generation failed",
                {"note_type": request.note_type},
                code="NOTE_GENERATION_FAILED",
            ) from exc
        content = generated.content

    note = note_service.create_standalone_note(
        patient_id=patient.id,
        note_type=request.note_type,
        content=content,
        content_edited=request.content_edited,
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
    return NoteResponse.from_note(note)
