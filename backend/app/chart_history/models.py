# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart history: a field's current value, and the values it replaced."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class HistoryEntry:
    """One field's current value. ``text`` is ``None`` once a value is removed."""

    id: str
    patient_id: str
    field_key: str
    text: str | None
    updated_at: datetime
    updated_by: str | None
    source_note_id: str | None = None
    source_note_date: datetime | None = None
    """When the source note's visit took place, read alongside; not stored."""


@dataclass(frozen=True)
class HistoryRevision:
    """A value the field held: who wrote it and when, and who replaced it and when."""

    id: str
    patient_id: str
    field_key: str
    text: str | None
    written_at: datetime
    written_by: str | None
    replaced_at: datetime
    replaced_by: str
    source_note_id: str | None = None
