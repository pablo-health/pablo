# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the practice does with a form once the patient has handed it in.

Three acts, and each one is a row on the review log beside whatever it
changed:

**Asking for corrections.** The clinician names the questions and writes a
sentence the patient will read. The form goes back to ``needs_correction``,
and the named questions — only those — become the patient's to answer
again. Everything else on the form has been read and kept.

**Entering a value for the patient.** Intake does not always happen in a
browser. Somebody sitting in the room says the thing the form asks, and the
clinician types it in. The answer lands as a successor with its provenance
recorded, never over the top of what the patient wrote, because "the
patient said this" and "we wrote this down for them" are different claims
and a chart that cannot tell them apart is making the stronger one.

**Accepting.** The clinician has read the form and is done with it. The
assignment leaves the active set, which is what lets the same form be sent
again later.

Two rules run through all three.

**The state machine is checked here, against the row.** A correction can
only be asked for on a form that was handed in, and a form can only be
accepted from the same place. Anything else is a conflict, decided by
reading the assignment rather than by trusting what a screen believed when
it rendered.

**Nothing is edited in place.** Every act appends: a review event, and for
a clinician entry a new response row with the old one pointed at it. What
the patient handed in stays exactly as they handed it in, which is the
whole reason a correction is a request rather than a pencil.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..repositories.patient_intake_assignment import ACTIVE_STATUSES
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories.intake_packet import IntakePacketRepository
    from ..repositories.patient_intake_assignment import PatientIntakeAssignmentRepository

#: The only status a form can be reopened from, or accepted from. Both acts
#: are answers to having read what the patient sent, so both need there to
#: be something sent.
REVIEWABLE_STATUS = "submitted"


class ReviewStateError(RuntimeError):
    """The form is not in a state this act can be applied to."""


class UnknownItemError(RuntimeError):
    """A question named that is not on the form's version."""


class AlreadyAcceptedError(RuntimeError):
    """An attempt to write to a form the practice has already accepted."""


class IntakeReviewService:
    """The clinician's half of the review cycle."""

    def __init__(
        self,
        repo: PatientIntakeAssignmentRepository,
        packets: IntakePacketRepository,
    ) -> None:
        self._repo = repo
        self._packets = packets

    # --- reads ---

    def events(self, assignment_id: str, user_id: str) -> list[dict[str, object]]:
        """Everything done with this form, oldest first."""
        return self._repo.list_review_events_for_clinician(assignment_id, user_id)

    def superseded_counts(self, assignment_id: str, user_id: str) -> dict[str, int]:
        """How many replaced answers each question has, keyed by item id."""
        return self._repo.count_superseded_responses_for_clinician(assignment_id, user_id)

    # --- acts ---

    def request_correction(
        self,
        assignment: dict[str, object],
        user_id: str,
        *,
        item_ids: list[str],
        note: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Reopen named questions on a form, with a note the patient reads.

        Returns the assignment as it now stands and the event that was
        written.

        Raises :class:`ReviewStateError` unless the form was handed in, and
        :class:`UnknownItemError` when a named question is not on the form's
        version — which is the check that stops a reopened form naming ids
        no screen will ever show, leaving a patient with a form they cannot
        finish.
        """
        if str(assignment["status"]) != REVIEWABLE_STATUS:
            raise ReviewStateError(str(assignment["status"]))

        named = self._known_items(assignment, item_ids)
        assignment_id = str(assignment["id"])
        patient_id = str(assignment["patient_id"])
        now = utc_now()

        event = self._repo.add_review_event(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": assignment_id,
                "patient_id": patient_id,
                "kind": "correction_requested",
                "item_ids": named,
                "note_to_patient": note,
                "created_by": user_id,
                "created_at": now,
            },
            user_id,
        )
        if event is None:  # pragma: no cover — the caller read the row already
            raise ReviewStateError(assignment_id)

        moved = self._repo.set_status_for_clinician(
            assignment_id, user_id, status="needs_correction", now=now
        )
        if moved is None:  # pragma: no cover — same grant, same row
            raise ReviewStateError(assignment_id)
        return moved, event

    def accept(
        self, assignment: dict[str, object], user_id: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Close a form off. Returns the assignment and the event written.

        Raises :class:`ReviewStateError` unless the form was handed in. A
        form reopened for corrections and sent back in is at ``submitted``
        again, so it is acceptable by the same rule rather than a second
        one.
        """
        if str(assignment["status"]) != REVIEWABLE_STATUS:
            raise ReviewStateError(str(assignment["status"]))

        assignment_id = str(assignment["id"])
        now = utc_now()
        event = self._repo.add_review_event(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": assignment_id,
                "patient_id": str(assignment["patient_id"]),
                "kind": "accepted",
                "item_ids": [],
                "note_to_patient": None,
                "created_by": user_id,
                "created_at": now,
            },
            user_id,
        )
        if event is None:  # pragma: no cover — the caller read the row already
            raise ReviewStateError(assignment_id)

        moved = self._repo.set_status_for_clinician(
            assignment_id, user_id, status="accepted", now=now
        )
        if moved is None:  # pragma: no cover — same grant, same row
            raise ReviewStateError(assignment_id)
        return moved, event

    def enter_for_patient(
        self,
        assignment: dict[str, object],
        user_id: str,
        *,
        item_id: str,
        value: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Record an answer the clinician took from the patient in the room.

        Returns the stored response and the event written.

        Allowed while the form is still live — anything in
        :data:`~app.repositories.patient_intake_assignment.ACTIVE_STATUSES`.
        An accepted form is closed and a withdrawn one is a question nobody
        is asking any more; both raise :class:`AlreadyAcceptedError`, and
        both for the same reason, which is that writing an answer into
        either one would put a fact under a request that is over.

        The answer is frozen rather than saved as a draft. It is not the
        patient's working copy, and letting a later portal save overwrite it
        in place would lose who said what — which is the one thing
        ``provenance`` exists to record. A patient who needs it changed gets
        the question reopened, and their answer then supersedes this one the
        same way this one supersedes theirs.
        """
        if str(assignment["status"]) not in ACTIVE_STATUSES:
            raise AlreadyAcceptedError(str(assignment["status"]))

        self._known_items(assignment, [item_id])
        assignment_id = str(assignment["id"])
        patient_id = str(assignment["patient_id"])
        now = utc_now()

        previous = self._repo.get_live_response_for_clinician(assignment_id, user_id, item_id)
        stored = self._repo.add_clinician_response(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": assignment_id,
                "patient_id": patient_id,
                "item_id": item_id,
                "value": value,
                "draft": False,
                "provenance": "clinician",
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
            },
            user_id,
            supersedes=None if previous is None else str(previous["id"]),
        )
        if stored is None:  # pragma: no cover — the caller read the row already
            raise ReviewStateError(assignment_id)

        event = self._repo.add_review_event(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": assignment_id,
                "patient_id": patient_id,
                "kind": "clinician_entered",
                "item_ids": [item_id],
                "note_to_patient": None,
                "created_by": user_id,
                "created_at": now,
            },
            user_id,
        )
        if event is None:  # pragma: no cover — same grant, same row
            raise ReviewStateError(assignment_id)
        return stored, event

    # --- helpers ---

    def _known_items(self, assignment: dict[str, object], item_ids: list[str]) -> list[str]:
        """The named questions, in the order the form asks them.

        Reordered rather than taken as sent, so ``item_ids`` on the event
        reads the way the form does — the portal walks it to send somebody
        to the first outstanding correction, and "first" should mean first
        on the form rather than first in whatever order a screen collected
        checkboxes.
        """
        wanted = set(item_ids)
        on_form = [
            str(row["id"])
            for row in self._packets.list_items(str(assignment["version_id"]))
            if str(row["id"]) in wanted
        ]
        unknown = wanted - set(on_form)
        if unknown:
            raise UnknownItemError(sorted(unknown)[0])
        return on_form


__all__ = [
    "REVIEWABLE_STATUS",
    "AlreadyAcceptedError",
    "IntakeReviewService",
    "ReviewStateError",
    "UnknownItemError",
]
