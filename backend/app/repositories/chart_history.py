# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart-history repository.

Callers reach a patient's history only after loading the patient through
``PatientRepository.get``, which is the access check; the row policy
(``has_patient_access``) backs it at the database. Revisions are only ever
appended.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..chart_history.models import HistoryEntry, HistoryRevision


class ChartHistoryRepository(ABC):
    @abstractmethod
    def entries(self, patient_id: str) -> list[HistoryEntry]:
        """Every field this patient has a row for, removed values included."""

    @abstractmethod
    def revisions(self, patient_id: str) -> list[HistoryRevision]:
        """Every value replaced on this patient's chart, most recently replaced first."""

    @abstractmethod
    def write(self, entry: HistoryEntry, replaced: HistoryRevision | None) -> None:
        """Store ``entry`` as the field's value and append the value it replaced."""


class InMemoryChartHistoryRepository(ChartHistoryRepository):
    """For unit tests."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], HistoryEntry] = {}
        self._revisions: list[HistoryRevision] = []

    def entries(self, patient_id: str) -> list[HistoryEntry]:
        return [replace(e) for (pid, _), e in self._entries.items() if pid == patient_id]

    def revisions(self, patient_id: str) -> list[HistoryRevision]:
        mine = [r for r in self._revisions if r.patient_id == patient_id]
        return sorted(mine, key=lambda r: r.replaced_at, reverse=True)

    def write(self, entry: HistoryEntry, replaced: HistoryRevision | None) -> None:
        self._entries[(entry.patient_id, entry.field_key)] = replace(entry)
        if replaced is not None:
            self._revisions.append(replaced)
