# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pydantic API models for the notes split (pa-0nx.2).

Notes are first-class clinical artifacts. Their on-disk shape lives on
:class:`app.models.note.Note`; this module provides request/response
models for the ``/api/notes`` surface and for embedding in
``SessionResponse``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# Pydantic needs both at runtime: SOAPSection appears in a list[...] field
# annotation it must validate, and Note is used by ``NoteResponse.from_note``.
from .enums import SOAPSection  # noqa: TC001
from .note import Note  # noqa: TC001
from .transcript import TranscriptModel  # noqa: TC001 — runtime Pydantic field


class NoteResponse(BaseModel):
    """API response shape for a clinical note."""

    id: str
    patient_id: str
    session_id: str | None = None
    note_type: str
    content: dict[str, Any] | None = None
    content_edited: dict[str, Any] | None = None
    finalized_at: datetime | None = None
    quality_rating: int | None = None
    quality_rating_reason: str | None = None
    quality_rating_sections: list[str] | None = None
    export_status: str = "not_queued"
    export_queued_at: datetime | None = None
    export_reviewed_at: datetime | None = None
    export_reviewed_by: str | None = None
    exported_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_note(note: Note) -> NoteResponse:
        return NoteResponse(
            id=note.id,
            patient_id=note.patient_id,
            session_id=note.session_id,
            note_type=note.note_type,
            content=note.content,
            content_edited=note.content_edited,
            finalized_at=note.finalized_at,
            quality_rating=note.quality_rating,
            quality_rating_reason=note.quality_rating_reason,
            quality_rating_sections=note.quality_rating_sections,
            export_status=note.export_status,
            export_queued_at=note.export_queued_at,
            export_reviewed_at=note.export_reviewed_at,
            export_reviewed_by=note.export_reviewed_by,
            exported_at=note.exported_at,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class UpdateNoteEditsRequest(BaseModel):
    """Request body for ``PATCH /api/notes/{id}`` — clinician edits."""

    content_edited: dict[str, Any]


class FinalizeNoteRequest(BaseModel):
    """Request body for ``POST /api/notes/{id}/finalize``."""

    quality_rating: int = Field(ge=1, le=5)
    quality_rating_reason: str | None = None
    quality_rating_sections: list[SOAPSection] | None = None


class CreateStandaloneNoteRequest(BaseModel):
    """Request body for ``POST /api/patients/{patient_id}/notes``.

    Creates a patient-owned note without an associated recorded session.
    If ``dictation_transcript`` is supplied, the same generation pipeline
    used for session uploads runs against it; otherwise the note is
    persisted with empty content for the clinician to fill via PATCH.
    """

    note_type: str
    content_edited: dict[str, Any] | None = None
    dictation_transcript: TranscriptModel | None = None
