# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a clinician dictated about a session after its recording stopped.

Field shape mirrors :class:`app.db.models.SessionDictationRow`; see it for
what each field means and why dictation is kept apart from the session's
own transcript and timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DictationStatus = Literal["transcribing", "transcribed", "failed"]
DictationUse = Literal["redraft", "addendum"]


@dataclass
class SessionDictation:
    id: str
    session_id: str
    note_id: str
    patient_id: str
    author_user_id: str
    audio_path: str
    content_type: str
    status: DictationStatus
    created_at: datetime
    duration_seconds: int | None = None
    transcript: str | None = None
    used_as: DictationUse | None = None
    addendum_id: str | None = None
    transcribed_at: datetime | None = None


class SessionDictationResponse(BaseModel):
    """A dictation as the session page shows it.

    ``draft_addendum`` is the dictation, lightly cleaned, while it waits to be
    reviewed and signed as an addendum to the signed note; it is ``None``
    once signed, and for a dictation that went into a redraft.
    """

    id: str
    session_id: str
    note_id: str
    status: DictationStatus
    used_as: DictationUse | None = None
    duration_seconds: int | None = None
    transcript: str | None = None
    draft_addendum: str | None = None
    addendum_id: str | None = None
    created_at: datetime
    transcribed_at: datetime | None = None


class SessionDictationListResponse(BaseModel):
    data: list[SessionDictationResponse]
