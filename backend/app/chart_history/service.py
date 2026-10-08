# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart-history operations.

Every write keeps the value it replaces as a revision: who wrote it, when,
from which note, and who replaced it when. Removing a value is such a write
too, leaving the field empty with what it said kept in its history. Nothing
is erased.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .fields import is_history_key
from .models import HistoryEntry, HistoryRevision

if TYPE_CHECKING:
    from ..repositories.chart_history import ChartHistoryRepository


class UnknownHistoryFieldError(LookupError):
    """No chart-history field has that key."""


class HistoryFieldEmptyError(LookupError):
    """The field holds no value to remove."""


class ChartHistoryService:
    def __init__(self, repo: ChartHistoryRepository) -> None:
        self._repo = repo

    def entries(self, patient_id: str) -> dict[str, HistoryEntry]:
        return {e.field_key: e for e in self._repo.entries(patient_id)}

    def revisions(self, patient_id: str) -> list[HistoryRevision]:
        return self._repo.revisions(patient_id)

    def set(
        self,
        patient_id: str,
        key: str,
        user_id: str,
        text: str,
        source_note_id: str | None = None,
    ) -> HistoryEntry:
        """Record ``text`` as the field's value. The same text again changes nothing."""
        current = self._entry(patient_id, key)
        if current is not None and current.text == text:
            return current
        entry = HistoryEntry(
            id=current.id if current is not None else str(uuid.uuid4()),
            patient_id=patient_id,
            field_key=key,
            text=text,
            updated_at=utc_now(),
            updated_by=user_id,
            source_note_id=source_note_id,
        )
        self._write(entry, current)
        return entry

    def remove(self, patient_id: str, key: str, user_id: str) -> HistoryEntry:
        """Empty the field, for a value that was never true. Its history keeps it."""
        current = self._entry(patient_id, key)
        if current is None or current.text is None:
            raise HistoryFieldEmptyError(key)
        entry = replace(
            current,
            text=None,
            updated_at=utc_now(),
            updated_by=user_id,
            source_note_id=None,
            source_note_date=None,
        )
        self._write(entry, current)
        return entry

    def _entry(self, patient_id: str, key: str) -> HistoryEntry | None:
        if not is_history_key(key):
            raise UnknownHistoryFieldError(key)
        return self.entries(patient_id).get(key)

    def _write(self, entry: HistoryEntry, current: HistoryEntry | None) -> None:
        """Store ``entry``, keeping the value it replaces with who replaced it, when."""
        replaced = (
            HistoryRevision(
                id=str(uuid.uuid4()),
                patient_id=current.patient_id,
                field_key=current.field_key,
                text=current.text,
                written_at=current.updated_at,
                written_by=current.updated_by,
                replaced_at=entry.updated_at,
                replaced_by=str(entry.updated_by),
                source_note_id=current.source_note_id,
            )
            if current is not None
            else None
        )
        self._repo.write(entry, replaced)
