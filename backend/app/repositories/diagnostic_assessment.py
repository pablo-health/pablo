# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Diagnostic assessment repository — patient-access-scoped reads & writes.

Mirrors the outcome-measure repository: every method takes the requesting
clinician's ``user_id``; reads return ``None``/empty when there's no grant in
``patient_clinicians`` (so the API can't be used as an existence oracle), and
writes raise :class:`PatientDiagnosticAccessDeniedError`. The Postgres
implementation delegates the check to the ``has_patient_access`` SQL function.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PatientDiagnosticAccessDeniedError(Exception):
    """Raised when a write touches a patient the user has no grant for."""

    def __init__(self, patient_id: str, user_id: str) -> None:
        super().__init__(f"user {user_id!r} has no access grant for patient {patient_id!r}")
        self.patient_id = patient_id
        self.user_id = user_id


class DiagnosticAssessmentRepository(ABC):
    """Abstract base class for diagnostic assessment data access.

    Rows are plain ``dict[str, object]`` matching the
    ``DiagnosticAssessmentRow`` column layout (uuid columns as ``str``, JSONB
    as ``dict``).
    """

    @abstractmethod
    def get(self, assessment_id: str, user_id: str) -> dict[str, object] | None:
        """Get an assessment by id, or ``None`` if absent or inaccessible."""

    @abstractmethod
    def list_by_patient(
        self,
        patient_id: str,
        user_id: str,
        *,
        instrument: str | None = None,
    ) -> list[dict[str, object]]:
        """List assessments for a patient (including soft-deleted). ``[]`` if denied."""

    @abstractmethod
    def add(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        """Insert a new row. Raises :class:`PatientDiagnosticAccessDeniedError` if blocked."""

    @abstractmethod
    def update(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        """Full-replacement update; raises ``PatientDiagnosticAccessDeniedError`` if blocked."""


_TEST_DEFAULT_USER = "__inmemory_test_default__"


class InMemoryDiagnosticAssessmentRepository(DiagnosticAssessmentRepository):
    """In-memory repository for unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, object]] = {}
        self._access: set[tuple[str, str]] = set()
        self._allow_all = False

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self._access.add((patient_id, user_id))

    def grant_all_access(self) -> None:
        self._allow_all = True

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return self._allow_all or (patient_id, user_id) in self._access

    def get(
        self, assessment_id: str, user_id: str = _TEST_DEFAULT_USER
    ) -> dict[str, object] | None:
        row = self._rows.get(assessment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return None
        return dict(row)

    def list_by_patient(
        self,
        patient_id: str,
        user_id: str = _TEST_DEFAULT_USER,
        *,
        instrument: str | None = None,
    ) -> list[dict[str, object]]:
        if not self._can_access(patient_id, user_id):
            return []
        return [
            dict(r)
            for r in self._rows.values()
            if str(r["patient_id"]) == patient_id
            and (instrument is None or str(r["instrument"]) == instrument)
        ]

    def add(
        self, row: dict[str, object], user_id: str = _TEST_DEFAULT_USER
    ) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._can_access(patient_id, user_id):
            raise PatientDiagnosticAccessDeniedError(patient_id, user_id)
        self._rows[str(row["id"])] = dict(row)
        return dict(row)

    def update(
        self, row: dict[str, object], user_id: str = _TEST_DEFAULT_USER
    ) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._can_access(patient_id, user_id):
            raise PatientDiagnosticAccessDeniedError(patient_id, user_id)
        self._rows[str(row["id"])] = dict(row)
        return dict(row)
