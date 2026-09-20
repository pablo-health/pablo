# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient intake submission repository — written by the patient, read by both.

The write side cannot take a ``user_id`` and ask ``has_patient_access``
whether that clinician holds a grant, the way every other per-patient
repository here does: the writer is the patient, who holds no grant and
never will. The isolation is the pair the rest of the patient surface uses
— the id comes off the authenticated principal rather than the request, and
the ``app.current_patient_id`` RLS policy on the table refuses a row that
names anyone else.

The read side has two callers and so two methods. A patient reads one of
their own by id, on the ``app.current_patient_id`` arm. A clinician lists a
patient's submissions, on the ordinary grant check every other chart table
uses — same table, different principal, different predicate. Keeping them
as separate methods means neither can be called with the other's notion of
who is asking.

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

    @abstractmethod
    def list_for_clinician(self, patient_id: str, user_id: str) -> list[dict[str, object]]:
        """A patient's submissions, newest first, read by a clinician.

        Returns an empty list when ``user_id`` holds no grant on the
        patient — the same answer a patient with no submissions gives, so a
        caller learns nothing from the shape of the response.
        """


class InMemoryPatientIntakeSubmissionRepository(PatientIntakeSubmissionRepository):
    """In-memory repository for unit tests.

    Clinician access is governed by a ``(patient_id, user_id)`` set
    populated via :meth:`grant_access`, mirroring the outcome-measure
    repository. Call :meth:`grant_all_access` in tests that do not
    exercise access control.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self._access: set[tuple[str, str]] = set()
        self._allow_all = False

    # --- test setup helpers ---

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self._access.add((patient_id, user_id))

    def grant_all_access(self) -> None:
        self._allow_all = True

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return self._allow_all or (patient_id, user_id) in self._access

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

    def list_for_clinician(self, patient_id: str, user_id: str) -> list[dict[str, object]]:
        if not self._can_access(patient_id, user_id):
            return []
        rows = [dict(r) for r in self.rows.values() if str(r["patient_id"]) == patient_id]
        rows.sort(key=lambda r: r["submitted_at"], reverse=True)  # type: ignore[arg-type,return-value]
        return rows
