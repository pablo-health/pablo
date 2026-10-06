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
from .note_signing import (
    MAX_SIGNER_FIELD_LEN,
    NoteAddendum,
    NoteSignature,
)
from .scheduling import VisitCodingFields
from .transcript import TranscriptModel  # noqa: TC001 — runtime Pydantic field


class NoteResponse(BaseModel):
    """API response shape for a clinical note."""

    id: str
    patient_id: str
    session_id: str | None = None
    note_type: str
    #: Version of a practice-defined note type; ``None`` for built-ins.
    note_type_version: int | None = None
    note_inputs: dict[str, str] | None = None
    content: dict[str, Any] | None = None
    content_edited: dict[str, Any] | None = None
    finalized_at: datetime | None = None
    quality_rating: int | None = None
    quality_rating_reason: str | None = None
    quality_rating_sections: list[str] | None = None
    status: str = "complete"
    #: Who wrote the note; ``None`` on rows that predate the column.
    author_user_id: str | None = None
    #: Readable by its author alone (a psychotherapy note).
    restricted: bool = False
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_note(note: Note) -> NoteResponse:
        return NoteResponse(
            id=note.id,
            patient_id=note.patient_id,
            session_id=note.session_id,
            note_type=note.note_type,
            note_type_version=note.note_type_version,
            note_inputs=note.note_inputs,
            content=note.content,
            content_edited=note.content_edited,
            finalized_at=note.finalized_at,
            quality_rating=note.quality_rating,
            quality_rating_reason=note.quality_rating_reason,
            quality_rating_sections=note.quality_rating_sections,
            status=note.status,
            author_user_id=note.author_user_id,
            restricted=note.restricted,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class PatientNotesListResponse(BaseModel):
    """Response model for ``GET /api/patients/{patient_id}/notes``."""

    data: list[NoteResponse]
    total: int


class UpdateNoteEditsRequest(BaseModel):
    """Request body for ``PATCH /api/notes/{id}`` — clinician edits."""

    content_edited: dict[str, Any]


class FinalizeNoteRequest(BaseModel):
    """Request body for ``POST /api/notes/{id}/finalize``.

    ``quality_rating`` is optional: AI-generated session notes carry a
    clinician rating of the model's draft, while manually-authored notes
    have nothing to score and finalize without one.
    """

    quality_rating: int | None = Field(default=None, ge=1, le=5)
    quality_rating_reason: str | None = None
    quality_rating_sections: list[SOAPSection] | None = None


class CreateStandaloneNoteRequest(VisitCodingFields):
    """Request body for ``POST /api/patients/{patient_id}/notes``.

    Creates a patient-owned note without an associated recorded session.
    If ``dictation_transcript`` is supplied, the same generation pipeline
    used for session uploads runs against it; otherwise the note is
    persisted with empty content for the clinician to fill via PATCH.

    ``appointment_id`` is optional — when the note is being authored for a
    specific visit, this is the primary place to code it (the session
    duration and clinical picture are both on screen). The billing-code
    fields inherited from :class:`VisitCodingFields` are written to that
    appointment, not to the note itself: a visit can carry several notes
    or none, so the appointment is the single place a receipt reads from.
    Omitting them leaves the visit's codes exactly as they were.
    """

    note_type: str
    #: Values for the note type's declared inputs.
    note_inputs: dict[str, str] | None = None
    content_edited: dict[str, Any] | None = None
    dictation_transcript: TranscriptModel | None = None
    appointment_id: str | None = None


class NoteSignerFields(BaseModel):
    """A signature as the clinician entered it for this one signing.

    Prefilled from the clinician's profile and editable; what is stored is
    exactly what was submitted, never re-derived later.
    """

    signer_name: str = Field(max_length=MAX_SIGNER_FIELD_LEN)
    signer_credentials: str | None = Field(default=None, max_length=MAX_SIGNER_FIELD_LEN)


class SignNoteRequest(NoteSignerFields):
    """Request body for ``POST /api/notes/{id}/sign`` — sign and lock.

    The optional rating is the same review of the AI draft finalizing
    records; a note written by hand has nothing to rate.
    """

    quality_rating: int | None = Field(default=None, ge=1, le=5)
    quality_rating_reason: str | None = None
    quality_rating_sections: list[SOAPSection] | None = None


class UnlockNoteRequest(BaseModel):
    """Request body for ``POST /api/notes/{id}/unlock``.

    ``reason`` is required; a blank one is refused with 400 by the service
    (a validation error here would be 422).
    """

    reason: str = Field(max_length=2000)


class CreateNoteAddendumRequest(NoteSignerFields):
    """Request body for ``POST /api/notes/{id}/addenda``."""

    text: str = Field(max_length=20000)


class NoteSignatureResponse(BaseModel):
    """One signed version of a note, as its signature block shows it."""

    id: str
    version: int
    signed_by: str
    signer_name: str
    signer_credentials: str | None = None
    signed_at: datetime
    unlocked_at: datetime | None = None
    unlocked_by: str | None = None
    unlock_reason: str | None = None
    note_type: str
    note_type_version: int | None = None
    #: The body as it was signed.
    content: dict[str, Any] | None = None
    content_edited: dict[str, Any] | None = None

    @staticmethod
    def from_signature(signature: NoteSignature) -> NoteSignatureResponse:
        return NoteSignatureResponse(
            id=signature.id,
            version=signature.version,
            signed_by=signature.signed_by,
            signer_name=signature.signer_name,
            signer_credentials=signature.signer_credentials,
            signed_at=signature.signed_at,
            unlocked_at=signature.unlocked_at,
            unlocked_by=signature.unlocked_by,
            unlock_reason=signature.unlock_reason,
            note_type=signature.note_type,
            note_type_version=signature.note_type_version,
            content=signature.content,
            content_edited=signature.content_edited,
        )


class NoteAddendumResponse(BaseModel):
    """An addendum with its own signature."""

    id: str
    text: str
    signer_name: str
    signer_credentials: str | None = None
    created_by: str
    created_at: datetime

    @staticmethod
    def from_addendum(addendum: NoteAddendum) -> NoteAddendumResponse:
        return NoteAddendumResponse(
            id=addendum.id,
            text=addendum.text,
            signer_name=addendum.signer_name,
            signer_credentials=addendum.signer_credentials,
            created_by=addendum.created_by,
            created_at=addendum.created_at,
        )


class NoteSigningRecordResponse(BaseModel):
    """Response for ``GET /api/notes/{id}/signing`` — what a signature block shows.

    ``signature`` is the version the note stands on now (``None`` when the
    note is unsigned, unlocked, or was finalized before signatures existed).
    ``versions`` is every signed version oldest first, superseded ones
    carrying their unlock reason. Addenda belong to the note, not a version.
    """

    note_id: str
    finalized_at: datetime | None = None
    signature: NoteSignatureResponse | None = None
    versions: list[NoteSignatureResponse]
    addenda: list[NoteAddendumResponse]
