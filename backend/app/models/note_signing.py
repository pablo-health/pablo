# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signed versions and addenda of a clinical note.

Field shape mirrors :class:`app.db.models.NoteSignatureRow` and
:class:`app.db.models.NoteAddendumRow`; see those for what each row means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Longest name or credentials string a signature carries.
MAX_SIGNER_FIELD_LEN = 200


@dataclass
class NoteSignature:
    """One signed version of a note."""

    id: str
    note_id: str
    patient_id: str
    version: int
    note_type: str
    digest: str
    signed_by: str
    signer_name: str
    signed_at: datetime
    note_type_version: int | None = None
    content: dict[str, Any] | None = None
    content_edited: dict[str, Any] | None = None
    signer_credentials: str | None = None
    unlocked_at: datetime | None = None
    unlocked_by: str | None = None
    unlock_reason: str | None = None


@dataclass
class NoteAddendum:
    """Information added to a signed note, with its own signature."""

    id: str
    note_id: str
    patient_id: str
    text: str
    signer_name: str
    digest: str
    created_by: str
    created_at: datetime
    signer_credentials: str | None = None
    prev_digest: str | None = None
