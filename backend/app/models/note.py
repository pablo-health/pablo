# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note domain dataclass.

A Note is the durable clinical artifact (SOAP, DAP, narrative, ...) owned by
a patient. It may or may not be tied to a recorded session — see pa-0nx for
the architectural split. Field shape mirrors the JSONB columns on
:class:`app.db.models.NoteRow`; the note-type registry validates ``content``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class Note:
    """Patient-owned clinical note."""

    id: str
    patient_id: str
    note_type: str
    created_at: datetime
    updated_at: datetime
    session_id: str | None = None
    # Version of a practice-defined note type; None for built-in types.
    note_type_version: int | None = None
    # Values supplied for the note type's declared inputs.
    note_inputs: dict[str, str] | None = None
    # Proposed and confirmed psychotherapy time; see app.notes.visit_times.
    psychotherapy_window: dict[str, Any] | None = None
    content: dict[str, Any] | None = None
    content_edited: dict[str, Any] | None = None
    finalized_at: datetime | None = None
    quality_rating: int | None = None
    quality_rating_reason: str | None = None
    quality_rating_sections: list[str] | None = None
    # Lifecycle of the standalone-note dictation path: 'processing' from the
    # moment the skeleton is persisted, until the Cloud Tasks worker writes
    # 'complete' (with content) or 'failed'. Every note created any other
    # way (no dictation, session-derived) starts 'complete'. A session note
    # being drafted again is 'processing' until the redraft lands, and
    # 'failed' — with its content untouched — if it didn't
    # (app.services.note_redraft).
    status: str = "complete"
    redacted_content: dict[str, Any] | None = None
    naturalized_content: dict[str, Any] | None = None
    # Who wrote the note; None on rows that predate the column.
    author_user_id: str | None = None
    # Readable by its author alone. Stamped from the note type's definition
    # at creation; the row policy keys on it.
    restricted: bool = False
