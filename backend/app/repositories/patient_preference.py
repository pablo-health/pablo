# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-preference repository.

Callers reach a patient's preferences only after loading the patient through
``PatientRepository.get``, which is the access check; the row policy
(``has_patient_access``) backs it at the database, and is the only check on
the many-patient read, which returns nothing for a client the caller cannot
open.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

    from ..preferences.models import Preference


def sort_preferences(rows: list[Preference]) -> list[Preference]:
    """By domain, then kind, then oldest first: the order the chart groups them in."""
    return sorted(rows, key=lambda p: (p.domain, p.kind, p.stated_at, p.id))


class PatientPreferenceRepository(ABC):
    @abstractmethod
    def list_by_patient(self, patient_id: str, *, include_retired: bool) -> list[Preference]:
        """Every preference of this patient, active only unless ``include_retired``."""

    @abstractmethod
    def list_active(self, patient_id: str, kinds: Collection[str]) -> list[Preference]:
        """This patient's active preferences of the given kinds."""

    @abstractmethod
    def list_active_for_patients(
        self, patient_ids: Collection[str], kinds: Collection[str]
    ) -> list[Preference]:
        """Active preferences of the given kinds for many patients, in one query."""

    @abstractmethod
    def get(self, patient_id: str, preference_id: str) -> Preference | None:
        """One preference of this patient, retired or not, or ``None``."""

    @abstractmethod
    def save(self, preference: Preference) -> Preference:
        """Insert or replace one preference."""


class InMemoryPatientPreferenceRepository(PatientPreferenceRepository):
    """For unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, Preference] = {}

    def list_by_patient(self, patient_id: str, *, include_retired: bool) -> list[Preference]:
        return sort_preferences(
            [
                replace(p)
                for p in self._rows.values()
                if p.patient_id == patient_id and (include_retired or p.retired_at is None)
            ]
        )

    def list_active(self, patient_id: str, kinds: Collection[str]) -> list[Preference]:
        return self.list_active_for_patients([patient_id], kinds)

    def list_active_for_patients(
        self, patient_ids: Collection[str], kinds: Collection[str]
    ) -> list[Preference]:
        return sort_preferences(
            [
                replace(p)
                for p in self._rows.values()
                if p.patient_id in patient_ids and p.kind in kinds and p.retired_at is None
            ]
        )

    def get(self, patient_id: str, preference_id: str) -> Preference | None:
        row = self._rows.get(preference_id)
        if row is None or row.patient_id != patient_id:
            return None
        return replace(row)

    def save(self, preference: Preference) -> Preference:
        self._rows[preference.id] = replace(preference)
        return preference
