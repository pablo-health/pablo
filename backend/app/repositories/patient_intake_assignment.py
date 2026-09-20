# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake assignment repository — the form somebody was asked, and their answers.

Two tables in one repository, because an answer is meaningless outside the
assignment it answers and an assignment with no answers is a question
nobody has started. Splitting them would buy two interfaces that are always
used together and a caller responsible for keeping them consistent.

**Every method names which principal is asking, and they are never the same
method.** A patient reaches their own rows through the
``app.current_patient_id`` policy, with the id taken off the authenticated
principal; a clinician reaches the same rows through the
``has_patient_access`` grant that scopes the rest of the chart. Keeping
them apart means neither can be called with the other's notion of who is
asking, which is the mistake a single ``get`` with an optional user id
invites.

The patient-principal writes take no ``user_id`` and ask for no grant, the
way the submission repository's do: the writer is the patient, who holds no
grant and never will.

Rows are plain ``dict[str, object]`` matching the column layout, the same
shape the other repositories in this package hand back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

#: Statuses an assignment can be in while it is still somebody's to finish.
#: The partial unique index on ``(patient_id, version_id)`` is defined
#: against the complement of this set, so the two have to say the same
#: thing: a test pins them together.
ACTIVE_STATUSES: tuple[str, ...] = (
    "assigned",
    "in_progress",
    "submitted",
    "needs_correction",
)

#: Every status the CHECK constraint admits.
ASSIGNMENT_STATUSES: tuple[str, ...] = (*ACTIVE_STATUSES, "accepted", "withdrawn")

#: Statuses in which the patient may still save an answer. ``submitted``
#: is not one: an assignment the patient has handed in stops being theirs
#: to edit until somebody asks for a correction.
WRITABLE_STATUSES: frozenset[str] = frozenset({"assigned", "in_progress", "needs_correction"})


class PatientIntakeAssignmentRepository(ABC):
    """Abstract base class for intake assignment and response data access."""

    # --- clinician side ---

    @abstractmethod
    def add_assignment(self, row: dict[str, object], user_id: str) -> dict[str, object] | None:
        """Ask a patient to fill in a version of a form.

        Returns ``None`` when *user_id* holds no grant on the patient, so a
        caller with no access cannot create a row it could not then read.
        """

    @abstractmethod
    def list_assignments_for_clinician(
        self, patient_id: str, user_id: str
    ) -> list[dict[str, object]]:
        """A patient's assignments, newest first, read by a clinician.

        An empty list when *user_id* holds no grant — the same answer a
        patient with no assignments gives, so the shape of the response
        reveals nothing.
        """

    @abstractmethod
    def get_assignment_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> dict[str, object] | None:
        """One assignment, or ``None`` when there is no such id or no grant."""

    @abstractmethod
    def find_active_assignment(
        self, patient_id: str, version_id: str, user_id: str
    ) -> dict[str, object] | None:
        """The live assignment for this patient and version, if there is one.

        "Live" is :data:`ACTIVE_STATUSES` — the same set the partial unique
        index is built on, which is what makes at most one row come back.
        """

    @abstractmethod
    def set_status_for_clinician(
        self, assignment_id: str, user_id: str, *, status: str, now: datetime
    ) -> dict[str, object] | None:
        """Move an assignment to *status*, stamping the matching column."""

    # --- patient side ---

    @abstractmethod
    def list_assignments_for_patient_principal(self, patient_id: str) -> list[dict[str, object]]:
        """The calling patient's own assignments, newest first."""

    @abstractmethod
    def get_assignment_for_patient_principal(
        self, assignment_id: str, patient_id: str
    ) -> dict[str, object] | None:
        """One of the calling patient's own assignments, or ``None``.

        The ``patient_id`` predicate is the primary isolation. Naming
        another patient's assignment is a miss, indistinguishable from a
        typo.
        """

    @abstractmethod
    def record_save(
        self, assignment_id: str, patient_id: str, now: datetime
    ) -> dict[str, object] | None:
        """Note that the patient just saved something on their own assignment.

        Always bumps ``updated_at``, which is what makes "when did they last
        work on this" answerable without a second table. Moves ``assigned``
        to ``in_progress`` on the way, and leaves every other status alone —
        the form advancing past "sent" happens once.
        """

    @abstractmethod
    def list_draft_responses(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        """The live draft answers on one of the calling patient's assignments."""

    @abstractmethod
    def save_draft_response(self, row: dict[str, object]) -> dict[str, object]:
        """Record an answer, replacing this question's live draft if there is one.

        An upsert keyed on ``(assignment_id, item_id)`` among live drafts —
        the same key the partial unique index enforces — so answering a
        question twice updates one row rather than accumulating two. The
        ``id`` on *row* is used only when the row is new.
        """


class InMemoryPatientIntakeAssignmentRepository(PatientIntakeAssignmentRepository):
    """In-memory repository for unit tests.

    Clinician access is governed by a ``(patient_id, user_id)`` set
    populated via :meth:`grant_access`, mirroring the submission
    repository. Call :meth:`grant_all_access` in tests that do not
    exercise access control.
    """

    def __init__(self) -> None:
        self.assignments: dict[str, dict[str, object]] = {}
        self.responses: dict[str, dict[str, object]] = {}
        self._access: set[tuple[str, str]] = set()
        self._allow_all = False

    # --- test setup helpers ---

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self._access.add((patient_id, user_id))

    def grant_all_access(self) -> None:
        self._allow_all = True

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return self._allow_all or (patient_id, user_id) in self._access

    # --- clinician side ---

    def add_assignment(self, row: dict[str, object], user_id: str) -> dict[str, object] | None:
        if not self._can_access(str(row["patient_id"]), user_id):
            return None
        self.assignments[str(row["id"])] = dict(row)
        return dict(row)

    def list_assignments_for_clinician(
        self, patient_id: str, user_id: str
    ) -> list[dict[str, object]]:
        if not self._can_access(patient_id, user_id):
            return []
        return self._for_patient(patient_id)

    def get_assignment_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> dict[str, object] | None:
        row = self.assignments.get(assignment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return None
        return dict(row)

    def find_active_assignment(
        self, patient_id: str, version_id: str, user_id: str
    ) -> dict[str, object] | None:
        if not self._can_access(patient_id, user_id):
            return None
        for row in self._for_patient(patient_id):
            if str(row["version_id"]) == version_id and row["status"] in ACTIVE_STATUSES:
                return dict(row)
        return None

    def set_status_for_clinician(
        self, assignment_id: str, user_id: str, *, status: str, now: datetime
    ) -> dict[str, object] | None:
        row = self.assignments.get(assignment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return None
        _apply_status(row, status, now)
        return dict(row)

    # --- patient side ---

    def list_assignments_for_patient_principal(self, patient_id: str) -> list[dict[str, object]]:
        return self._for_patient(patient_id)

    def get_assignment_for_patient_principal(
        self, assignment_id: str, patient_id: str
    ) -> dict[str, object] | None:
        row = self.assignments.get(assignment_id)
        if row is None or str(row["patient_id"]) != patient_id:
            return None
        return dict(row)

    def record_save(
        self, assignment_id: str, patient_id: str, now: datetime
    ) -> dict[str, object] | None:
        row = self.assignments.get(assignment_id)
        if row is None or str(row["patient_id"]) != patient_id:
            return None
        if row["status"] == "assigned":
            row["status"] = "in_progress"
        row["updated_at"] = now
        return dict(row)

    def list_draft_responses(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        rows = [
            dict(r)
            for r in self.responses.values()
            if str(r["assignment_id"]) == assignment_id
            and str(r["patient_id"]) == patient_id
            and r["draft"]
            and r["superseded_by"] is None
        ]
        rows.sort(key=lambda r: str(r["item_id"]))
        return rows

    def save_draft_response(self, row: dict[str, object]) -> dict[str, object]:
        existing = next(
            (
                r
                for r in self.responses.values()
                if str(r["assignment_id"]) == str(row["assignment_id"])
                and str(r["item_id"]) == str(row["item_id"])
                and r["draft"]
                and r["superseded_by"] is None
            ),
            None,
        )
        if existing is not None:
            existing["value"] = row["value"]
            existing["updated_at"] = row["updated_at"]
            return dict(existing)
        self.responses[str(row["id"])] = dict(row)
        return dict(row)

    # --- helpers ---

    def _for_patient(self, patient_id: str) -> list[dict[str, object]]:
        rows = [dict(r) for r in self.assignments.values() if str(r["patient_id"]) == patient_id]
        rows.sort(key=lambda r: (r["assigned_at"], str(r["id"])), reverse=True)  # type: ignore[index]
        return rows


def _apply_status(row: dict[str, object], status: str, now: datetime) -> None:
    """Set *status* and stamp whichever column records reaching it.

    Shared by the in-memory repository and its Postgres sibling so the two
    cannot disagree about which timestamp a status writes.
    """
    row["status"] = status
    row["updated_at"] = now
    stamped = STATUS_TIMESTAMP_COLUMN.get(status)
    if stamped is not None:
        row[stamped] = now


#: The column each terminal status stamps. ``assigned`` and ``in_progress``
#: are absent on purpose: the first is stamped when the row is created and
#: the second is a state rather than an event, so ``updated_at`` already
#: carries when it was reached.
STATUS_TIMESTAMP_COLUMN: dict[str, str] = {
    "submitted": "submitted_at",
    "accepted": "accepted_at",
    "withdrawn": "withdrawn_at",
}


__all__ = [
    "ACTIVE_STATUSES",
    "ASSIGNMENT_STATUSES",
    "STATUS_TIMESTAMP_COLUMN",
    "WRITABLE_STATUSES",
    "InMemoryPatientIntakeAssignmentRepository",
    "PatientIntakeAssignmentRepository",
]
