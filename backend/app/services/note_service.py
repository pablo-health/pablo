# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note business logic service.

Owns the lifecycle of clinical notes (create-from-generation, edit,
finalize, export submission). Notes are first-class and patient-owned;
:class:`SessionService` delegates note-flavored operations here so the
session row stays focused on recording metadata. See pa-0nx.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ..api_errors import APIError, BadRequestError, ConflictError, NotFoundError
from ..models import Note
from ..repositories import NotesRepository  # noqa: TC001 — runtime DI type
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

EXPORT_STATUS_NOT_QUEUED = "not_queued"
EXPORT_STATUS_QUEUED = "queued"


class NoteServiceError(APIError):
    """Base exception for note service errors."""


class NoteNotFoundError(NotFoundError):
    """Raised when a note is not found."""


class NoteAlreadyFinalizedError(ConflictError):
    """Raised when finalizing a note that is already finalized."""

    code = "NOTE_ALREADY_FINALIZED"


class NoteNotFinalizedError(BadRequestError):
    """Raised when an operation requires the note to be finalized first."""

    code = "NOTE_NOT_FINALIZED"


class NoteService:
    """Lifecycle operations for clinical notes."""

    def __init__(self, notes_repo: NotesRepository) -> None:
        self._notes = notes_repo

    # --- Read ---

    def get_note(self, note_id: str) -> Note:
        note = self._notes.get(note_id)
        if note is None:
            raise NoteNotFoundError(f"Note {note_id} not found")
        return note

    def get_note_by_session_id(self, session_id: str) -> Note | None:
        return self._notes.get_by_session_id(session_id)

    def list_notes_for_patient(self, patient_id: str) -> list[Note]:
        return self._notes.list_by_patient(patient_id)

    # --- Generation pipeline ---

    def create_or_update_for_session(
        self,
        *,
        session_id: str,
        patient_id: str,
        note_type: str,
        content: dict[str, Any] | None,
    ) -> Note:
        """Persist a note tied to a session.

        Insert a new row if none exists for this session; otherwise update
        the existing row's ``content`` and ``note_type`` (and clear
        ``content_edited`` since regenerated content supersedes prior
        in-progress edits). ``content`` may be ``None`` to pre-allocate
        a row for a scheduled session whose note has not been generated
        yet — a placeholder so the requested ``note_type`` survives until
        generation.
        """
        existing = self._notes.get_by_session_id(session_id)
        now = utc_now()
        if existing is not None:
            existing.note_type = note_type
            if content is not None:
                existing.content = content
                existing.content_edited = None
            existing.updated_at = now
            return self._notes.update(existing)

        note = Note(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            session_id=session_id,
            note_type=note_type,
            content=content,
            created_at=now,
            updated_at=now,
        )
        return self._notes.add(note)

    # --- Edits ---

    def update_note_edits(self, note_id: str, content_edited: dict[str, Any]) -> Note:
        """Persist clinician edits to a note's content."""
        note = self.get_note(note_id)
        note.content_edited = content_edited
        note.updated_at = utc_now()
        return self._notes.update(note)

    # --- Finalization ---

    def finalize_note(
        self,
        note_id: str,
        *,
        quality_rating: int,
        quality_rating_reason: str | None = None,
        quality_rating_sections: list[str] | None = None,
        finalized_at: datetime | None = None,
    ) -> Note:
        """Finalize a note (record quality rating + finalized_at)."""
        note = self.get_note(note_id)
        if note.finalized_at is not None:
            raise NoteAlreadyFinalizedError(
                f"Note {note_id} is already finalized",
                {"note_id": note_id},
            )
        note.quality_rating = quality_rating
        note.quality_rating_reason = quality_rating_reason
        note.quality_rating_sections = quality_rating_sections
        note.finalized_at = finalized_at or utc_now()
        note.updated_at = utc_now()
        return self._notes.update(note)

    def update_quality_rating(
        self,
        note_id: str,
        *,
        quality_rating: int,
        quality_rating_reason: str | None = None,
        quality_rating_sections: list[str] | None = None,
    ) -> tuple[Note, int | None]:
        """Update an already-finalized note's quality rating.

        Returns ``(note, old_rating)``. Old rating is whatever was on the
        note before this call — useful for audit logging.
        """
        note = self.get_note(note_id)
        if note.finalized_at is None:
            raise NoteNotFinalizedError(
                f"Note {note_id} is not finalized",
                {"note_id": note_id},
            )
        old_rating = note.quality_rating
        note.quality_rating = quality_rating
        note.quality_rating_reason = quality_rating_reason
        note.quality_rating_sections = quality_rating_sections
        note.updated_at = utc_now()
        return self._notes.update(note), old_rating

    # --- Export ---

    def submit_note_for_export(self, note_id: str) -> Note:
        """Queue a finalized note for clinician/eval export."""
        note = self.get_note(note_id)
        if note.finalized_at is None:
            raise NoteNotFinalizedError(
                f"Note {note_id} must be finalized before submitting for export",
                {"note_id": note_id},
            )
        note.export_status = EXPORT_STATUS_QUEUED
        note.export_queued_at = utc_now()
        note.updated_at = utc_now()
        return self._notes.update(note)
