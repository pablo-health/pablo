# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Remembered answers to "which patient does this outside identifier mean?"

A source (an EHR calendar feed, a calendar import) names a client its own
way: initials like "J.A.", a code like "SH00001", a calendar series id. Once
the clinician has said which patient that is, the answer is kept here so the
next record from the same source is matched without asking again. Read and
written through ``app.patients.matching``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class PatientSourceMapping:
    """One source identifier, and the patient it means."""

    user_id: str
    source: str
    source_identifier: str
    patient_id: str
    created_at: datetime | None = None

    @property
    def doc_id(self) -> str:
        return f"{self.user_id}_{self.source}_{self.source_identifier}"


class PatientSourceMappingRepository(ABC):
    """Storage for remembered source identifiers, per clinician."""

    @abstractmethod
    def get(self, user_id: str, source: str, source_identifier: str) -> PatientSourceMapping | None:
        pass

    @abstractmethod
    def list_by_source(self, user_id: str, source: str) -> list[PatientSourceMapping]:
        pass

    @abstractmethod
    def save(self, mapping: PatientSourceMapping) -> None:
        """Insert, or replace the patient on the mapping with the same doc_id."""


class InMemoryPatientSourceMappingRepository(PatientSourceMappingRepository):
    """In-memory implementation for tests."""

    def __init__(self) -> None:
        self._mappings: dict[str, PatientSourceMapping] = {}

    def get(self, user_id: str, source: str, source_identifier: str) -> PatientSourceMapping | None:
        return self._mappings.get(f"{user_id}_{source}_{source_identifier}")

    def list_by_source(self, user_id: str, source: str) -> list[PatientSourceMapping]:
        return [m for m in self._mappings.values() if m.user_id == user_id and m.source == source]

    def save(self, mapping: PatientSourceMapping) -> None:
        self._mappings[mapping.doc_id] = mapping
