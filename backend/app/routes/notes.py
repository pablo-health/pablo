# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Notes API routes (pa-0nx.2).

Notes are first-class clinical artifacts. These endpoints expose the
note lifecycle (read / edit / finalize / submit-for-export) directly,
rather than going through the session route. The session route still
embeds the note for backward compatibility — see ``routes/sessions.py``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    FinalizeNoteRequest,
    NoteResponse,
    UpdateNoteEditsRequest,
    User,
)
from ..repositories import NotesRepository
from ..repositories import (
    get_notes_repository as _notes_repo_factory,
)
from ..services import (
    AuditService,
    NoteAlreadyFinalizedError,
    NoteNotFinalizedError,
    NoteNotFoundError,
    NoteService,
    get_audit_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notes", tags=["notes"])


def get_notes_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> NotesRepository:
    """Get notes repository scoped to the tenant's database."""
    return _notes_repo_factory()


def get_note_service(
    notes_repo: NotesRepository = Depends(get_notes_repository),
) -> NoteService:
    """Get note service instance."""
    return NoteService(notes_repo)


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
