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
    redacted_content: dict[str, Any] | None = None
    naturalized_content: dict[str, Any] | None = None
    redacted_export_payload: dict[str, Any] | None = None
