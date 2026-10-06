# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Problem-list repository.

Callers reach a patient's problems only after loading the patient through
``PatientRepository.get``, which is the access check; the row policy
(``has_patient_access``) backs it at the database. Reads exclude removed rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace

from ..problems.models import Problem, derived_diagnosis, sort_problems


class PatientProblemRepository(ABC):
    @abstractmethod
    def list_by_patient(self, patient_id: str) -> list[Problem]:
        """Live problems in list order: active, rule-out, resolved; each by position."""

    @abstractmethod
    def get(self, patient_id: str, problem_id: str) -> Problem | None:
        """A live problem of this patient, or ``None``."""

    @abstractmethod
    def save(self, problem: Problem) -> Problem:
        """Insert or replace one problem."""

    @abstractmethod
    def set_positions(self, patient_id: str, ordered_ids: list[str]) -> None:
        """Number the given problems 0..n-1 in the order given."""

    @abstractmethod
    def sync_patient_diagnosis(self, patient_id: str) -> None:
        """Rewrite ``patients.diagnosis`` from the active problems."""


class InMemoryPatientProblemRepository(PatientProblemRepository):
    """For unit tests. ``derived`` holds what the diagnosis line would read."""

    def __init__(self) -> None:
        self._rows: dict[str, Problem] = {}
        self.derived: dict[str, str | None] = {}

    def list_by_patient(self, patient_id: str) -> list[Problem]:
        return sort_problems(
            replace(p)
            for p in self._rows.values()
            if p.patient_id == patient_id and p.deleted_at is None
        )

    def get(self, patient_id: str, problem_id: str) -> Problem | None:
        row = self._rows.get(problem_id)
        if row is None or row.patient_id != patient_id or row.deleted_at is not None:
            return None
        return replace(row)

    def save(self, problem: Problem) -> Problem:
        self._rows[problem.id] = replace(problem)
        return problem

    def set_positions(self, patient_id: str, ordered_ids: list[str]) -> None:
        for position, problem_id in enumerate(ordered_ids):
            row = self._rows[problem_id]
            if row.patient_id == patient_id:
                row.position = position

    def sync_patient_diagnosis(self, patient_id: str) -> None:
        self.derived[patient_id] = derived_diagnosis(self.list_by_patient(patient_id))
