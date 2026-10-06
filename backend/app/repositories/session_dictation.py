# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session dictations: add one, record its transcript once, read them back.

There is no delete and no general update. A dictation's transcript is
written once by the transcription worker, and signing the draft addendum it
became records that addendum's id; nothing else changes a row.

Access is the caller's to check before it gets here (the routes 404 a
session the clinician cannot see), and the table's row policy keeps each row
readable exactly when its note is.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.session_dictation import DictationStatus, DictationUse, SessionDictation


class SessionDictationRepository(ABC):
    @abstractmethod
    def add(self, dictation: SessionDictation) -> SessionDictation:
        """Write a new dictation."""

    @abstractmethod
    def get(self, dictation_id: str) -> SessionDictation | None:
        """One dictation, or ``None`` when it doesn't exist or isn't visible."""

    @abstractmethod
    def list_for_session(self, session_id: str) -> list[SessionDictation]:
        """Every dictation for the session, oldest first."""

    @abstractmethod
    def record_transcript(
        self,
        dictation_id: str,
        *,
        status: DictationStatus,
        transcript: str | None,
        used_as: DictationUse | None,
        transcribed_at: datetime,
    ) -> SessionDictation:
        """Record how transcribing a dictation ended."""

    @abstractmethod
    def link_addendum(self, dictation_id: str, addendum_id: str) -> SessionDictation:
        """Record the signed addendum a dictation became."""


class InMemorySessionDictationRepository(SessionDictationRepository):
    """In-memory repository for unit tests."""

    def __init__(self) -> None:
        self.rows: dict[str, SessionDictation] = {}

    def add(self, dictation: SessionDictation) -> SessionDictation:
        self.rows[dictation.id] = copy.copy(dictation)
        return dictation

    def get(self, dictation_id: str) -> SessionDictation | None:
        row = self.rows.get(dictation_id)
        return copy.copy(row) if row else None

    def list_for_session(self, session_id: str) -> list[SessionDictation]:
        rows = [copy.copy(r) for r in self.rows.values() if r.session_id == session_id]
        return sorted(rows, key=lambda r: r.created_at)

    def record_transcript(
        self,
        dictation_id: str,
        *,
        status: DictationStatus,
        transcript: str | None,
        used_as: DictationUse | None,
        transcribed_at: datetime,
    ) -> SessionDictation:
        row = self.rows[dictation_id]
        row.status = status
        row.transcript = transcript
        row.used_as = used_as
        row.transcribed_at = transcribed_at
        return copy.copy(row)

    def link_addendum(self, dictation_id: str, addendum_id: str) -> SessionDictation:
        row = self.rows[dictation_id]
        row.addendum_id = addendum_id
        return copy.copy(row)
