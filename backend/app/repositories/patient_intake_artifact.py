# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake artifact repository — which file answers which question.

One table, and a narrow interface: an artifact is attached, listed, and —
while the form is still open — removed. There is no update, and that is the
model rather than an omission. A patient who photographed the back of their
card badly does not edit the row to point at a different file; the row goes
and a new one arrives, so the artifact and the document it names can never
drift apart.

Like the repositories beside it, every method names which principal is
asking, so neither can be called with the other's notion of who is asking.
The patient methods reach their own rows through the
``app.current_patient_id`` policy, with the id taken off the authenticated
principal; the clinician method reaches the same rows through the
``has_patient_access`` grant the rest of the chart uses.

Rows are plain ``dict[str, object]`` matching the column layout, the same
shape the other repositories in this package hand back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PatientIntakeArtifactRepository(ABC):
    """Abstract base class for intake artifact data access."""

    # --- patient side ---

    @abstractmethod
    def add(self, row: dict[str, object]) -> dict[str, object]:
        """Attach one document to one question.

        The caller has already checked everything it can: that the form is
        the patient's and still open, that the question asks for a file, and
        that the document is one this patient uploaded. What it cannot check
        is a simultaneous second attempt on the same side of the same card —
        the partial unique index arbitrates that, and the caller turns the
        integrity error into a 409.
        """

    @abstractmethod
    def list_for_assignment(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        """The calling patient's artifacts on one of their own forms.

        Oldest first, so the front of a card reads before the back when both
        were sent in that order.
        """

    @abstractmethod
    def get(self, artifact_id: str, patient_id: str) -> dict[str, object] | None:
        """One of the calling patient's own artifacts, or ``None``.

        Another patient's id and an id that never existed answer the same
        way, so nothing here says which artifacts exist.
        """

    @abstractmethod
    def delete(self, artifact_id: str, patient_id: str) -> bool:
        """Remove one of the calling patient's own artifacts.

        Returns whether a row went. The document it pointed at is tombstoned
        by the caller — the two are one act, and doing it here would put a
        second table's lifecycle inside this one's repository.
        """

    # --- clinician side ---

    @abstractmethod
    def list_for_clinician(self, assignment_id: str, user_id: str) -> list[dict[str, object]]:
        """The same artifacts, reached through the clinician's grant instead."""


class InMemoryPatientIntakeArtifactRepository(PatientIntakeArtifactRepository):
    """In-memory repository for unit tests.

    The two unique constraints have counterparts here, so a unit test meets
    the same refusals the database gives: :meth:`add` raises
    :class:`ArtifactSlotTakenError` on a second row for the same side of the
    same question, and :class:`DocumentAlreadyAttachedError` on a document
    that already answers something.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        #: Which patients a clinician holds a grant on, for the clinician
        #: read. Mirrors ``patient_clinicians`` without the table.
        self.grants: dict[str, set[str]] = {}

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self.grants.setdefault(user_id, set()).add(patient_id)

    # --- patient side ---

    def add(self, row: dict[str, object]) -> dict[str, object]:
        document_id = str(row["document_id"])
        if any(str(r["document_id"]) == document_id for r in self.rows.values()):
            raise DocumentAlreadyAttachedError(document_id)
        side = row.get("side")
        if side is not None and self._slot(
            str(row["assignment_id"]), str(row["item_id"]), str(side)
        ):
            raise ArtifactSlotTakenError(str(side))
        self.rows[str(row["id"])] = dict(row)
        return dict(row)

    def list_for_assignment(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        rows = [
            dict(row)
            for row in self.rows.values()
            if str(row["assignment_id"]) == assignment_id and str(row["patient_id"]) == patient_id
        ]
        rows.sort(key=lambda row: (row["created_at"], str(row["id"])))  # type: ignore[index]
        return rows

    def get(self, artifact_id: str, patient_id: str) -> dict[str, object] | None:
        row = self.rows.get(artifact_id)
        if row is None or str(row["patient_id"]) != patient_id:
            return None
        return dict(row)

    def delete(self, artifact_id: str, patient_id: str) -> bool:
        if self.get(artifact_id, patient_id) is None:
            return False
        del self.rows[artifact_id]
        return True

    # --- clinician side ---

    def list_for_clinician(self, assignment_id: str, user_id: str) -> list[dict[str, object]]:
        allowed = self.grants.get(user_id, set())
        rows = [
            dict(row)
            for row in self.rows.values()
            if str(row["assignment_id"]) == assignment_id and str(row["patient_id"]) in allowed
        ]
        rows.sort(key=lambda row: (row["created_at"], str(row["id"])))  # type: ignore[index]
        return rows

    # --- helpers ---

    def _slot(self, assignment_id: str, item_id: str, side: str) -> bool:
        return any(
            str(row["assignment_id"]) == assignment_id
            and str(row["item_id"]) == item_id
            and row.get("side") == side
            for row in self.rows.values()
        )


class ArtifactSlotTakenError(RuntimeError):
    """A card already has a photograph of this side on this form.

    Raised by the repository rather than surfaced as an integrity error,
    because the caller's response to it is a 409 with a sentence the patient
    reads — which is not the response to any other failure on this table.
    """


class DocumentAlreadyAttachedError(RuntimeError):
    """This document already answers a question.

    One file answers one question: the same photograph cannot be both the
    front and the back of a card. Like the error above, it has its own name
    because the caller answers it with its own sentence.
    """


__all__ = [
    "ArtifactSlotTakenError",
    "DocumentAlreadyAttachedError",
    "InMemoryPatientIntakeArtifactRepository",
    "PatientIntakeArtifactRepository",
]
