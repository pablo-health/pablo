# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake assignment repository — the form somebody was asked, and their answers.

Three tables in one repository, because an answer is meaningless outside the
assignment it answers, an assignment with no answers is a question nobody has
started, and a review event is a statement about both. Splitting them would
buy three interfaces that are always used together and a caller responsible
for keeping them consistent.

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
    from collections.abc import Sequence
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

#: Every kind the review-event CHECK constraint admits.
#:
#: Three are the practice's: corrections asked for, a value entered for the
#: patient, the form accepted. ``corrected`` is the patient's — it is written
#: when they hand a reopened form back in — and it is the only one their row
#: policy admits.
REVIEW_EVENT_KINDS: tuple[str, ...] = (
    "correction_requested",
    "corrected",
    "accepted",
    "clinician_entered",
)

#: The one kind a patient principal may write. Mirrors
#: ``PATIENT_WRITE_NARROWING`` in ``app.db``; a test pins the two together.
PATIENT_REVIEW_EVENT_KIND: str = "corrected"

#: Where an answer came from. ``patient`` on everything the portal saved,
#: ``clinician`` on a value entered with the patient in the room.
RESPONSE_PROVENANCE: tuple[str, ...] = ("patient", "clinician")


class ReceiptCollisionError(RuntimeError):
    """The receipt code offered for a submission is already in use here.

    Raised by :meth:`PatientIntakeAssignmentRepository.mark_submitted` and
    handled by generating another one. A separate type rather than letting
    the database's own error out, because the caller's response to it is
    "try again", which is not the response to any other integrity failure
    on this table.
    """


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
    def list_responses_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> list[dict[str, object]]:
        """What the patient answered on one assignment, read by a clinician.

        An empty list when *user_id* holds no grant on the patient — the
        same answer an assignment with nothing saved against it gives, so
        the shape of the response tells a caller without access nothing.
        """

    @abstractmethod
    def set_status_for_clinician(
        self, assignment_id: str, user_id: str, *, status: str, now: datetime
    ) -> dict[str, object] | None:
        """Move an assignment to *status*, stamping the matching column."""

    @abstractmethod
    def get_live_response_for_clinician(
        self, assignment_id: str, user_id: str, item_id: str
    ) -> dict[str, object] | None:
        """This question's live answer, read through the clinician's grant.

        What a clinician entry supersedes. ``None`` when nobody has answered
        the question yet, which is the ordinary case for an entry taken in
        the room, and also what a caller with no grant gets.
        """

    @abstractmethod
    def add_clinician_response(
        self, row: dict[str, object], user_id: str, *, supersedes: str | None
    ) -> dict[str, object] | None:
        """Record an answer a clinician entered, superseding what it replaces.

        Returns ``None`` when *user_id* holds no grant on the patient, so a
        caller with no access cannot write a row it could not then read.

        Never an update in place: *supersedes* names the row this one
        replaces, and that row keeps its value and its ``draft`` flag. What
        changes on it is ``superseded_by``, which is the whole record of the
        replacement having happened.
        """

    @abstractmethod
    def add_review_event(self, row: dict[str, object], user_id: str) -> dict[str, object] | None:
        """Record something the practice did with a form.

        Returns ``None`` when *user_id* holds no grant on the patient.
        """

    @abstractmethod
    def list_review_events_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> list[dict[str, object]]:
        """Everything done with one form, oldest first, read by a clinician.

        An empty list when *user_id* holds no grant — the same answer a form
        nobody has reviewed gives, so the shape reveals nothing.
        """

    @abstractmethod
    def count_superseded_responses_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> dict[str, int]:
        """How many replaced answers each question on this form has.

        Keyed by item id, absent where there are none. What the review view
        needs to offer "show earlier answers" without reading the earlier
        answers themselves.
        """

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
    def list_live_responses(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        """Every answer that has not been superseded, draft or handed in.

        Wider than :meth:`list_draft_responses` by exactly the rows a submit
        froze. That is what makes it the read a finished form needs: after
        submission there are no drafts left, so the narrower method would
        report an answered form as empty.
        """

    @abstractmethod
    def get_live_response(
        self, assignment_id: str, patient_id: str, item_id: str
    ) -> dict[str, object] | None:
        """This question's live answer on this assignment, whatever its state.

        Answers the one question :meth:`save_draft_response` cannot ask for
        itself: is the row that is already there still a draft? A frozen one
        is a submitted answer, and changing it in place would rewrite what
        somebody handed in.
        """

    @abstractmethod
    def save_draft_response(self, row: dict[str, object]) -> dict[str, object]:
        """Record an answer, replacing this question's live draft if there is one.

        An upsert keyed on ``(assignment_id, item_id)`` among live drafts —
        the same key the partial unique index enforces — so answering a
        question twice updates one row rather than accumulating two. The
        ``id`` on *row* is used only when the row is new.
        """

    @abstractmethod
    def add_successor_response(
        self, row: dict[str, object], *, supersedes: str
    ) -> dict[str, object]:
        """Record a patient's corrected answer beside the one it replaces.

        The counterpart of :meth:`save_draft_response` for a question that
        already holds a handed-in answer. That answer is not edited: it keeps
        its value and its frozen flag, and gains a ``superseded_by`` pointing
        at the new row, so the form still reads back as it was handed in.
        """

    @abstractmethod
    def add_patient_review_event(self, row: dict[str, object]) -> dict[str, object]:
        """Record that the calling patient handed a reopened form back in.

        The only kind of review event a patient writes, and their row policy
        admits no other — see :data:`PATIENT_REVIEW_EVENT_KIND`.
        """

    @abstractmethod
    def list_review_events_for_patient_principal(
        self, assignment_id: str, patient_id: str
    ) -> list[dict[str, object]]:
        """Everything done with one of the calling patient's own forms.

        Oldest first. What the portal reads to show the note asking for a
        correction and which questions it names.
        """

    @abstractmethod
    def retire_draft_responses(
        self,
        assignment_id: str,
        patient_id: str,
        item_ids: Sequence[str],
        now: datetime,
    ) -> list[str]:
        """Take the live drafts for *item_ids* out of the record.

        Returns the item ids that had one, so a caller can say how many
        answers this did something to without reading them back.

        What it writes is ``superseded_by``, pointed at the row itself:
        retired, with nothing in its place. That is the one transition this
        table already has for "no longer the live answer", and every read —
        the patient's, the clinician's, the scorer's — already asks for
        rows that have not been superseded, so nothing has to learn a new
        exception. The row stays exactly as it was written, which is the
        point: an answer somebody gave is never deleted, it stops counting.

        Only drafts. An answer that has already been handed in is part of a
        record somebody has read, and retiring one would be rewriting that
        record rather than declining to add to it.
        """

    @abstractmethod
    def freeze_draft_responses(self, assignment_id: str, patient_id: str, now: datetime) -> int:
        """Turn every live draft on this assignment into a submitted answer.

        Returns how many rows were frozen. One statement rather than a row
        at a time, so a form is never half handed in: the whole set stops
        being editable at the same instant the assignment does.
        """

    @abstractmethod
    def mark_submitted(
        self, assignment_id: str, patient_id: str, *, now: datetime, receipt_code: str
    ) -> dict[str, object] | None:
        """Record that the patient handed this form in, with their receipt.

        Only moves a row the patient may still submit — the statuses in
        :data:`WRITABLE_STATUSES`. A second submit therefore comes back
        ``None`` rather than overwriting the first one's timestamp and
        receipt, which is what lets the caller answer 409 without a
        separate read that another request could race.

        Raises :class:`ReceiptCollisionError` when *receipt_code* is already
        spoken for in this practice, leaving the transaction usable so the
        caller can try another one. Only the unique index can decide that:
        two submissions can pick the same code in the same instant, and a
        look-before-you-write check would let both through.
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
        self.review_events: dict[str, dict[str, object]] = {}
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

    def list_responses_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> list[dict[str, object]]:
        row = self.assignments.get(assignment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return []
        return self._live(assignment_id, str(row["patient_id"]), drafts_only=False)

    def set_status_for_clinician(
        self, assignment_id: str, user_id: str, *, status: str, now: datetime
    ) -> dict[str, object] | None:
        row = self.assignments.get(assignment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return None
        _apply_status(row, status, now)
        return dict(row)

    def get_live_response_for_clinician(
        self, assignment_id: str, user_id: str, item_id: str
    ) -> dict[str, object] | None:
        row = self.assignments.get(assignment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return None
        return self.get_live_response(assignment_id, str(row["patient_id"]), item_id)

    def add_clinician_response(
        self, row: dict[str, object], user_id: str, *, supersedes: str | None
    ) -> dict[str, object] | None:
        if not self._can_access(str(row["patient_id"]), user_id):
            return None
        return self._write_successor(row, supersedes)

    def add_review_event(self, row: dict[str, object], user_id: str) -> dict[str, object] | None:
        if not self._can_access(str(row["patient_id"]), user_id):
            return None
        self.review_events[str(row["id"])] = dict(row)
        return dict(row)

    def list_review_events_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> list[dict[str, object]]:
        row = self.assignments.get(assignment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return []
        return self._events(assignment_id, str(row["patient_id"]))

    def count_superseded_responses_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> dict[str, int]:
        row = self.assignments.get(assignment_id)
        if row is None or not self._can_access(str(row["patient_id"]), user_id):
            return {}
        counts: dict[str, int] = {}
        for response in self.responses.values():
            if (
                str(response["assignment_id"]) == assignment_id
                and response["superseded_by"] is not None
            ):
                item_id = str(response["item_id"])
                counts[item_id] = counts.get(item_id, 0) + 1
        return counts

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
        return self._live(assignment_id, patient_id, drafts_only=True)

    def list_live_responses(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        return self._live(assignment_id, patient_id, drafts_only=False)

    def get_live_response(
        self, assignment_id: str, patient_id: str, item_id: str
    ) -> dict[str, object] | None:
        rows = [
            row
            for row in self._live(assignment_id, patient_id, drafts_only=False)
            if str(row["item_id"]) == item_id
        ]
        # A draft first when there is one, as the Postgres sibling does.
        rows.sort(key=lambda r: (not r["draft"], r["updated_at"]))  # type: ignore[return-value]
        return rows[0] if rows else None

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

    def add_successor_response(
        self, row: dict[str, object], *, supersedes: str
    ) -> dict[str, object]:
        return self._write_successor(row, supersedes)

    def add_patient_review_event(self, row: dict[str, object]) -> dict[str, object]:
        self.review_events[str(row["id"])] = dict(row)
        return dict(row)

    def list_review_events_for_patient_principal(
        self, assignment_id: str, patient_id: str
    ) -> list[dict[str, object]]:
        return self._events(assignment_id, patient_id)

    def retire_draft_responses(
        self,
        assignment_id: str,
        patient_id: str,
        item_ids: Sequence[str],
        now: datetime,
    ) -> list[str]:
        wanted = set(item_ids)
        retired: list[str] = []
        for row in self.responses.values():
            if (
                str(row["assignment_id"]) == assignment_id
                and str(row["patient_id"]) == patient_id
                and str(row["item_id"]) in wanted
                and row["draft"]
                and row["superseded_by"] is None
            ):
                row["superseded_by"] = row["id"]
                row["updated_at"] = now
                retired.append(str(row["item_id"]))
        return retired

    def freeze_draft_responses(self, assignment_id: str, patient_id: str, now: datetime) -> int:
        frozen = 0
        for row in self.responses.values():
            if (
                str(row["assignment_id"]) == assignment_id
                and str(row["patient_id"]) == patient_id
                and row["draft"]
                and row["superseded_by"] is None
            ):
                row["draft"] = False
                row["updated_at"] = now
                frozen += 1
        return frozen

    def mark_submitted(
        self, assignment_id: str, patient_id: str, *, now: datetime, receipt_code: str
    ) -> dict[str, object] | None:
        row = self.assignments.get(assignment_id)
        if row is None or str(row["patient_id"]) != patient_id:
            return None
        if str(row["status"]) not in WRITABLE_STATUSES:
            return None
        if any(
            other["receipt_code"] == receipt_code
            for other in self.assignments.values()
            if other.get("receipt_code") is not None
        ):
            raise ReceiptCollisionError(receipt_code)
        row["receipt_code"] = receipt_code
        _apply_status(row, "submitted", now)
        return dict(row)

    # --- helpers ---

    def _write_successor(self, row: dict[str, object], supersedes: str | None) -> dict[str, object]:
        """Store *row*, then point the row it replaces at it.

        This order, not the other one: the pointer names an id, so the row
        it names has to exist first. The replaced row is otherwise
        untouched — same value, same ``draft`` flag, same timestamps.
        """
        self.responses[str(row["id"])] = dict(row)
        if supersedes is not None:
            previous = self.responses.get(supersedes)
            if previous is not None:
                previous["superseded_by"] = str(row["id"])
        return dict(row)

    def _events(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        rows = [
            dict(r)
            for r in self.review_events.values()
            if str(r["assignment_id"]) == assignment_id and str(r["patient_id"]) == patient_id
        ]
        rows.sort(key=lambda r: (r["created_at"], str(r["id"])))  # type: ignore[index]
        return rows

    def _live(
        self, assignment_id: str, patient_id: str, *, drafts_only: bool
    ) -> list[dict[str, object]]:
        rows = [
            dict(r)
            for r in self.responses.values()
            if str(r["assignment_id"]) == assignment_id
            and str(r["patient_id"]) == patient_id
            and r["superseded_by"] is None
            and (r["draft"] or not drafts_only)
        ]
        rows.sort(key=lambda r: str(r["item_id"]))
        return rows

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
    "PATIENT_REVIEW_EVENT_KIND",
    "RESPONSE_PROVENANCE",
    "REVIEW_EVENT_KINDS",
    "STATUS_TIMESTAMP_COLUMN",
    "WRITABLE_STATUSES",
    "InMemoryPatientIntakeAssignmentRepository",
    "PatientIntakeAssignmentRepository",
    "ReceiptCollisionError",
]
