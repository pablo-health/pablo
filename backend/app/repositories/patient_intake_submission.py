# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient intake submission repository — written by the patient, as themselves.

Every other per-patient repository here takes a ``user_id`` and asks
``has_patient_access`` whether that clinician holds a grant. This one cannot:
the writer is the patient, who holds no grant and never will. The isolation
is the pair the rest of the patient surface uses — the id comes off the
authenticated principal rather than the request, and the ``app.current_patient_id``
RLS policy on the table refuses a row that names anyone else.

Submissions are immutable once recorded, so there is no update and no delete.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PatientIntakeSubmissionRepository(ABC):
    """Abstract base class for patient intake submission data access.

    Rows are plain ``dict[str, object]`` matching the
    ``PatientIntakeSubmissionRow`` column layout (uuid columns as ``str``,
    JSONB as ``dict``).
    """

    @abstractmethod
    def add_for_patient_principal(self, row: dict[str, object]) -> dict[str, object]:
        """Insert a submission written by the patient it belongs to.

        The caller passes a ``patient_id`` that came from the authenticated
        principal, never from the request body.
        """

    @abstractmethod
    def get_for_patient_principal(
        self, submission_id: str, patient_id: str
    ) -> dict[str, object] | None:
        """One of the calling patient's own submissions, or ``None``.

        The ``patient_id`` predicate is the primary isolation. Naming another
        patient's submission id is a miss, indistinguishable from a typo.
        """


class InMemoryPatientIntakeSubmissionRepository(PatientIntakeSubmissionRepository):
    """In-memory repository for unit tests."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def add_for_patient_principal(self, row: dict[str, object]) -> dict[str, object]:
        self.rows[str(row["id"])] = dict(row)
        return dict(row)

    def get_for_patient_principal(
        self, submission_id: str, patient_id: str
    ) -> dict[str, object] | None:
        row = self.rows.get(submission_id)
        if row is None or str(row["patient_id"]) != patient_id:
            return None
        return dict(row)
