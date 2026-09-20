# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake assignments — asking somebody to fill a form in, and their answers.

The repository knows how to read and write two tables. This is what knows
the rules between them, and there are four.

**A form is only ever sent frozen.** A version has to be published before it
can be assigned, because the whole promise of the model is that the
questions somebody answered can be read back exactly as they were asked.
Assigning a draft would put a patient's answers under questions that could
still change underneath them.

**Asking twice is asking once.** A clinician who clicks send again, or
reissues portal access after a link expired, gets the assignment that is
already live rather than a second copy of the same form. The partial unique
index underneath says the same thing, so a race loses at the database rather
than leaving the patient with two.

**Completion is computed, never stored.** :meth:`progress` walks the
version's items against the patient's saved answers every time it is asked,
which is why no client is allowed to decide for itself whether a form is
done. A stored flag would be a second copy of a fact the rows already carry.

**A saved answer is checked against the question it answers.** The value
goes through the same validator the eventual submit will use, so a draft
cannot quietly hold something that will be refused later. What the route
layer adds on top is which rows a patient may touch at all. One kind of
question is refused here outright: a consent document's answer names a
signature row, so it is written by the signing route and by nothing else
(:data:`~app.intake.answers.SIGNED_ITEM_TYPES`).

**What was handed in is never edited.** Submitting freezes every answer,
and no path here updates the value on a frozen row. A correction writes a
successor instead and points the old row at it, so the form still reads back
as it was handed in. The guard is here rather than only on the assignment's
status because the two are different statements: the status says the form is
closed, and this says that even an open form cannot rewrite an answer that
has already been read. See :meth:`IntakeAssignmentService.save_answer`.

**A reopened form is open only where the practice said.** A clinician asking
for corrections names the questions, and while the form sits in
``needs_correction`` those are the only ones the patient may touch — every
other question is refused, because the practice has already read and kept
the rest. The scope lives on the review event rather than in this service's
memory, which is what makes it survive the patient closing the tab.

**A form only hands in the questions it ended up asking.** On a form that
branches, a patient can answer a question and then take back the answer
that opened it — said yes, answered what appeared, went back and said no.
Submitting retires what they put into the questions they are no longer
shown instead of filing it, so the chart never holds an answer to a
question this patient was not asked. The receipt says how many, which is
the only place anybody is told.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..intake.answers import (
    ROUTE_WRITTEN_ITEM_TYPES,
    SIGNED_ITEM_TYPES,
    AnswerError,
    validate_answer,
)
from ..intake.completion import Completion, CompletionItem, assess
from ..intake.items import (
    InstrumentConfig,
    ItemConfigError,
    stored_config,
    validate_item_config,
)
from ..intake.receipts import new_receipt_code, withheld_answers_note
from ..repositories.patient_intake_assignment import (
    PATIENT_REVIEW_EVENT_KIND,
    WRITABLE_STATUSES,
    ReceiptCollisionError,
)
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..intake.items import ItemConfig
    from ..outcome_measures.schemas import OutcomeMeasureResponse
    from ..outcome_measures.service import OutcomeMeasureService
    from ..repositories.intake_packet import IntakePacketRepository
    from ..repositories.patient_intake_assignment import PatientIntakeAssignmentRepository

#: How long a burst of saves counts as one visit for the audit log. A
#: patient works through a form a question at a time, and a row per
#: keystroke-sized save would bury the disclosures that carry weight. See
#: :meth:`IntakeAssignmentService.save_answer`.
AUDIT_COALESCE_SECONDS = 900

#: How many receipt codes to try before giving up. A collision needs two
#: submissions in one practice to draw the same one out of thirty to the
#: eighth, so reaching two is already a signal that something other than
#: chance is going on — and a bounded loop cannot spin.
RECEIPT_ATTEMPTS = 5


class UnpublishedVersionError(RuntimeError):
    """An attempt to send a version of a form that is still a draft."""


class AssignmentClosedError(RuntimeError):
    """An attempt to answer a form that is no longer the patient's to fill in."""


class FrozenResponseError(RuntimeError):
    """An attempt to change an answer that has already been handed in."""


class CorrectionScopeError(RuntimeError):
    """An attempt to change a question the correction request did not name.

    Raised only while a form sits in ``needs_correction``. The practice has
    read and kept every other answer on it, so reopening one question is not
    reopening the form.
    """


class CorrectionOutstandingError(RuntimeError):
    """A reopened form handed back in with a named question still unredone.

    Carries the item ids, in the order the form asks them, so the portal can
    send somebody to the first one rather than telling them to look.
    """

    def __init__(self, outstanding: list[str]) -> None:
        super().__init__(f"{len(outstanding)} correction(s) outstanding")
        self.outstanding = outstanding


class IncompleteFormError(RuntimeError):
    """A form handed in with required questions still unanswered.

    Carries the item ids, in the order the form asks them, so the portal can
    send somebody to the first one rather than telling them to look.
    """

    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"{len(missing)} question(s) outstanding")
        self.missing = missing


@dataclass(frozen=True)
class Submission:
    """What handing a form in produced.

    A shape rather than a tuple because the third member is the reason it
    exists: ``notes`` is what the receipt tells the patient beyond their
    code, and a caller unpacking two values would have dropped it silently.
    """

    assignment: dict[str, object]
    measures: list[OutcomeMeasureResponse]
    #: Sentences for the receipt screen, in the order they should be read.
    #: Empty on the ordinary submission, which has nothing to add.
    notes: list[str]


class ReceiptUnavailableError(RuntimeError):
    """Every receipt code offered was already in use.

    Practically unreachable — see :data:`RECEIPT_ATTEMPTS`. It exists so the
    loop that generates them has an end, and so the one condition under
    which a submit cannot be completed has a name rather than a silent
    fall-through.
    """


class IntakeAssignmentService:
    """Assignments and draft answers, with the four rules enforced."""

    def __init__(
        self,
        repo: PatientIntakeAssignmentRepository,
        packets: IntakePacketRepository,
    ) -> None:
        self._repo = repo
        self._packets = packets

    # --- the clinician's side ---

    def assign(
        self, patient_id: str, version_id: str, assigned_by: str
    ) -> tuple[dict[str, object], bool]:
        """Ask *patient_id* to fill in *version_id*.

        Returns the assignment and whether it was created now. An existing
        live assignment for the same version comes back with ``False``, so
        the caller can answer 200 rather than 201 and skip the audit row
        for a request that changed nothing.

        Raises :class:`UnpublishedVersionError` on a draft version, and
        ``LookupError`` when there is no such version or no grant on the
        patient.
        """
        version = self._packets.get_version(version_id)
        if version is None:
            raise LookupError(version_id)
        if version["published_at"] is None:
            raise UnpublishedVersionError(version_id)

        existing = self._repo.find_active_assignment(patient_id, version_id, assigned_by)
        if existing is not None:
            return existing, False

        now = utc_now()
        created = self._repo.add_assignment(
            {
                "id": str(uuid.uuid4()),
                "patient_id": patient_id,
                "version_id": version_id,
                "status": "assigned",
                "assigned_by": assigned_by,
                "assigned_at": now,
                # Spelled out rather than left absent: a row handed back by
                # a repository has every column, and an in-memory one that
                # stored only what it was given would hand back a narrower
                # dict than its Postgres sibling.
                "submitted_at": None,
                "accepted_at": None,
                "withdrawn_at": None,
                "receipt_code": None,
                "legacy_submission_id": None,
                "updated_at": now,
            },
            assigned_by,
        )
        if created is None:
            raise LookupError(patient_id)
        return created, True

    def list_for_clinician(self, patient_id: str, user_id: str) -> list[dict[str, object]]:
        return self._repo.list_assignments_for_clinician(patient_id, user_id)

    def withdraw(self, assignment_id: str, user_id: str) -> dict[str, object] | None:
        """Take back a request. Returns ``None`` if there is no such assignment.

        Never a delete: what the patient already answered stays readable,
        and the row records that the practice stopped asking.
        """
        return self._repo.set_status_for_clinician(
            assignment_id, user_id, status="withdrawn", now=utc_now()
        )

    # --- the patient's side ---

    def list_for_patient(self, patient_id: str) -> list[dict[str, object]]:
        return self._repo.list_assignments_for_patient_principal(patient_id)

    def get_for_patient(self, assignment_id: str, patient_id: str) -> dict[str, object] | None:
        return self._repo.get_assignment_for_patient_principal(assignment_id, patient_id)

    def items(self, version_id: str) -> list[dict[str, object]]:
        """The questions on a version, in the order the patient reads them."""
        return self._packets.list_items(version_id)

    def version(self, version_id: str) -> dict[str, object] | None:
        """One version of a form, whoever is asking.

        Reading a version needs no grant of its own: a form is the
        practice's own paperwork and the tenant schema is its boundary.
        What is scoped is the assignment that points at it, which every
        caller has already been through.
        """
        return self._packets.get_version(version_id)

    def template(self, template_id: str) -> dict[str, object] | None:
        """The form a version belongs to, for its name."""
        return self._packets.get_template(template_id)

    def answers(self, assignment_id: str, patient_id: str) -> dict[str, dict[str, object]]:
        """The patient's answers so far, keyed by the item's id.

        Live rows, draft or handed in. Reading only the drafts would report
        a submitted form as empty, because submitting is exactly what stops
        its answers being drafts.
        """
        return {
            str(row["item_id"]): _as_mapping(row["value"])
            for row in self._repo.list_live_responses(assignment_id, patient_id)
        }

    def answers_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> dict[str, dict[str, object]]:
        """The same answers, reached through the clinician's grant instead."""
        return {
            str(row["item_id"]): _as_mapping(row["value"])
            for row in self._repo.list_responses_for_clinician(assignment_id, user_id)
        }

    def all_responses_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> list[dict[str, object]]:
        """Every answer ever written on one form, oldest first.

        Replaced and retired rows as well as live ones. What the export
        reads, because a chart copy carries the corrections rather than a
        count of them.
        """
        return self._repo.list_all_responses_for_clinician(assignment_id, user_id)

    def get_for_clinician(self, assignment_id: str, user_id: str) -> dict[str, object] | None:
        return self._repo.get_assignment_for_clinician(assignment_id, user_id)

    def live_response_for_clinician(
        self, assignment_id: str, user_id: str, item_id: str
    ) -> dict[str, object] | None:
        """One question's current answer, read through the clinician's grant."""
        return self._repo.get_live_response_for_clinician(assignment_id, user_id, item_id)

    def config_for(self, row: dict[str, object]) -> ItemConfig | None:
        """One stored question's configuration, or ``None`` if it no longer parses.

        Public because the review surface validates a clinician's entry
        against the same configuration the portal's save uses. Two parsers
        would be two opinions about what a question accepts.
        """
        return _parse(row)

    def progress(self, assignment: dict[str, object], patient_id: str) -> Completion:
        """Whether this form can be handed in, and what is still missing.

        Computed from the version's items and the patient's saved answers
        every time it is asked. An item whose stored configuration no longer
        parses is treated as unanswerable and so as missing — the form is
        not finishable until somebody fixes it, which is the truthful answer
        rather than a cheerful one.
        """
        saved = self.answers(str(assignment["id"]), patient_id)
        items: list[CompletionItem] = []
        by_key: dict[str, object] = {}

        for row in self._packets.list_items(str(assignment["version_id"])):
            items.append(
                CompletionItem(
                    item_id=str(row["id"]),
                    key=str(row["key"]),
                    required=bool(row["required"]),
                    config=_parse(row),
                )
            )
            value = saved.get(str(row["id"]))
            if value is not None:
                by_key[str(row["key"])] = value

        return assess(items, by_key)

    def save_answer(
        self,
        assignment: dict[str, object],
        patient_id: str,
        item_id: str,
        value: object,
    ) -> tuple[dict[str, object], bool]:
        """Record one answer on one of the patient's own assignments.

        Returns the stored response and whether this save should be written
        to the audit log. A patient fills a form in over a sitting, and one
        row per saved question would bury the events that carry forensic
        weight — so a save is recorded when it follows a gap of
        :data:`AUDIT_COALESCE_SECONDS` since the last write to this
        assignment, and the saves inside a burst ride on that one row. The
        gap is measured from the assignment's own ``updated_at``, which
        every save bumps, so nothing new has to be stored to know it.

        Raises :class:`AssignmentClosedError` when the form is no longer the
        patient's to fill in, :class:`CorrectionScopeError` when the form is
        reopened and this is not one of the questions it was reopened for,
        :class:`FrozenResponseError` when this question's answer has already
        been handed in and nothing reopened it, ``LookupError`` when the item
        is not on its version, and :class:`AnswerError` when the value does
        not fit the question.
        """
        if str(assignment["status"]) not in WRITABLE_STATUSES:
            raise AssignmentClosedError(str(assignment["id"]))

        reopened = self.open_correction(assignment, patient_id)
        if reopened is not None and item_id not in event_item_ids(reopened):
            raise CorrectionScopeError(item_id)

        row = next(
            (
                item
                for item in self._packets.list_items(str(assignment["version_id"]))
                if str(item["id"]) == item_id
            ),
            None,
        )
        if row is None:
            raise LookupError(item_id)

        config = _parse(row)
        if config is None:
            raise AnswerError("This question cannot be answered as it is set up.")
        if config.item_type in ROUTE_WRITTEN_ITEM_TYPES:
            # These answers are references to rows: a signature that was
            # taken, the documents that arrived. The only thing allowed to
            # write one is the route that also writes the row it points at.
            # Letting this path through would let a patient assert a
            # signature nobody took, or attach a document id that is not
            # theirs — and completion, which reads the answer rather than
            # the rows, would believe either.
            if config.item_type in SIGNED_ITEM_TYPES:
                raise AnswerError("This document is signed rather than answered here.")
            raise AnswerError("This question is answered by sending a file.")
        validate_answer(config, value)

        # The immutability invariant, checked against the row rather than
        # against the form's status. A form can be writable and still hold
        # an answer somebody has already read — because it was reopened for
        # corrections, or because a clinician entered the value in the room.
        # Neither is edited in place: a reopened question gets a successor,
        # and anything else is refused.
        existing = self._repo.get_live_response(str(assignment["id"]), patient_id, item_id)
        frozen = existing is not None and not existing["draft"]
        if frozen and reopened is None:
            raise FrozenResponseError(str(existing["id"]))  # type: ignore[index]

        now = utc_now()
        worth_auditing = _starts_a_visit(assignment, now)
        row_to_store: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "assignment_id": str(assignment["id"]),
            "patient_id": patient_id,
            "item_id": item_id,
            "value": value,
            "draft": True,
            "provenance": "patient",
            "superseded_by": None,
            "created_at": now,
            "updated_at": now,
        }
        if frozen:
            stored = self._repo.add_successor_response(
                row_to_store,
                supersedes=str(existing["id"]),  # type: ignore[index]
            )
        else:
            stored = self._repo.save_draft_response(row_to_store)
        self._repo.record_save(str(assignment["id"]), patient_id, now)
        return stored, worth_auditing

    # --- the review cycle, from the patient's side ---

    def review_events_for_patient(
        self, assignment_id: str, patient_id: str
    ) -> list[dict[str, object]]:
        """Everything done with one of this patient's own forms, oldest first."""
        return self._repo.list_review_events_for_patient_principal(assignment_id, patient_id)

    def open_correction(
        self, assignment: dict[str, object], patient_id: str
    ) -> dict[str, object] | None:
        """The correction request this form is currently reopened for.

        ``None`` unless the form sits in ``needs_correction``, which is what
        makes the status and the scope one answer rather than two that can
        disagree. When it does, the newest ``correction_requested`` event is
        the live one: handing the form back in moves the status off
        ``needs_correction``, so an older request cannot still be open.
        """
        if str(assignment["status"]) != "needs_correction":
            return None
        events = self.review_events_for_patient(str(assignment["id"]), patient_id)
        requests = [row for row in events if str(row["kind"]) == "correction_requested"]
        return requests[-1] if requests else None

    def outstanding_corrections(
        self, assignment: dict[str, object], patient_id: str, correction: dict[str, object]
    ) -> list[str]:
        """Which named questions have not been answered again yet.

        A question counts as redone when its live answer was written after
        the correction was asked for. Comparing timestamps rather than
        looking for a ``superseded_by`` covers the question that had no
        answer to supersede — a correction can name one the patient skipped.
        """
        asked_at = correction["created_at"]
        live = {
            str(row["item_id"]): row
            for row in self._repo.list_live_responses(str(assignment["id"]), patient_id)
        }
        outstanding: list[str] = []
        for item_id in event_item_ids(correction):
            row = live.get(item_id)
            if row is None or not _written_after(row, asked_at):
                outstanding.append(item_id)
        return outstanding

    def submit(
        self,
        assignment: dict[str, object],
        patient_id: str,
        measures: OutcomeMeasureService,
    ) -> Submission:
        """Hand a form in. Returns the submitted assignment and what it scored.

        Five things happen, in an order chosen so that nothing is written
        until everything that could refuse the submission has.

        1. **Re-check every required question**, against the same
           validators the saves went through. A draft can go stale — the
           practice may have published nothing, but a date question with a
           past-only bound judges a different day today than it did
           yesterday, and a rule can stop holding because an earlier answer
           changed — so the answer to "is this finished" is computed here
           rather than trusted from the last save's response. An
           unfinished form raises :class:`IncompleteFormError` and nothing
           has been touched.
        2. **Retire the answers to questions this patient is no longer
           shown.** Before the freeze, so a value the form stopped asking
           for is never part of what was handed in. See the module
           docstring for why that is a retirement rather than a deletion.
        3. **Freeze the answers.** Every live draft stops being one, which
           is what makes "what was submitted" a stable record.
        4. **Record the submission and its receipt**, on a row that is
           still the patient's to submit — so a second submit finds nothing
           to move and raises :class:`AssignmentClosedError` rather than
           overwriting the first receipt.
        5. **Score every measure on the form** through the same
           ``create_self_report`` the fixed intake form uses. Reused rather
           than reimplemented: a second scorer is a second set of bands to
           drift, and the chart reads these rows from one place. A measure
           whose question is hidden has been retired by then, so a branch
           that was opened and closed again scores nothing.

        A form that was reopened for corrections is checked first, before
        any of that: every question the practice named has to have been
        answered again, or this is :class:`CorrectionOutstandingError` and
        nothing is touched. Finishing the form is not the same as doing what
        was asked — the rest of the answers were already complete, which is
        how the form got handed in the first time.

        The whole thing rides the request's transaction, so a failure
        anywhere leaves the form exactly as unfinished as it was.
        """
        if str(assignment["status"]) not in WRITABLE_STATUSES:
            raise AssignmentClosedError(str(assignment["id"]))

        assignment_id = str(assignment["id"])
        reopened = self.open_correction(assignment, patient_id)
        if reopened is not None:
            outstanding = self.outstanding_corrections(assignment, patient_id, reopened)
            if outstanding:
                raise CorrectionOutstandingError(outstanding)

        completion = self.progress(assignment, patient_id)
        if not completion.complete:
            raise IncompleteFormError(completion.missing)

        now = utc_now()
        withheld = self._repo.retire_draft_responses(
            assignment_id, patient_id, completion.hidden, now
        )
        self._repo.freeze_draft_responses(assignment_id, patient_id, now)
        submitted = self._submit_with_receipt(assignment_id, patient_id, now)
        if submitted is None:
            raise AssignmentClosedError(assignment_id)

        if reopened is not None:
            # Written beside the status change rather than after it, on the
            # same transaction: "the form came back" and "the corrections
            # were made" are one fact, and a record that can hold one
            # without the other is a record that will.
            self._repo.add_patient_review_event(
                {
                    "id": str(uuid.uuid4()),
                    "assignment_id": assignment_id,
                    "patient_id": patient_id,
                    "kind": PATIENT_REVIEW_EVENT_KIND,
                    "item_ids": event_item_ids(reopened),
                    "note_to_patient": None,
                    "created_by": patient_id,
                    "created_at": now,
                }
            )

        recorded = [
            measures.create_self_report(
                patient_id=patient_id,
                instrument=code,
                item_scores=scores,
                administered_at=now,
            )
            for code, scores in self._instrument_answers(assignment, patient_id)
        ]
        note = withheld_answers_note(len(withheld))
        return Submission(
            assignment=submitted,
            measures=recorded,
            notes=[] if note is None else [note],
        )

    def _submit_with_receipt(
        self, assignment_id: str, patient_id: str, now: datetime
    ) -> dict[str, object] | None:
        """Stamp the submission, trying another code if one is taken."""
        for _ in range(RECEIPT_ATTEMPTS):
            try:
                return self._repo.mark_submitted(
                    assignment_id, patient_id, now=now, receipt_code=new_receipt_code()
                )
            except ReceiptCollisionError:
                continue
        raise ReceiptUnavailableError(assignment_id)

    def _instrument_answers(
        self, assignment: dict[str, object], patient_id: str
    ) -> list[tuple[str, dict[str, int]]]:
        """Each measure on the form and the item scores answered against it.

        In the order the form asks them, so the outcome-measure rows land
        in the order the patient answered rather than in whatever order a
        dictionary happened to hold.
        """
        saved = self.answers(str(assignment["id"]), patient_id)
        found: list[tuple[str, dict[str, int]]] = []
        for row in self._packets.list_items(str(assignment["version_id"])):
            config = _parse(row)
            if not isinstance(config, InstrumentConfig):
                continue
            scores = saved.get(str(row["id"]), {}).get("item_scores")
            if isinstance(scores, dict):
                found.append((config.code, {str(k): int(v) for k, v in scores.items()}))
        return found


def _starts_a_visit(assignment: dict[str, object], now: datetime) -> bool:
    """True when this save begins a new sitting rather than continuing one.

    Two ways it can. A form still at ``assigned`` has never been written
    to, so whatever lands first is the visit that opened it — the clock
    cannot say so, because ``updated_at`` was set when the form was sent.
    After that it is the gap: every save bumps ``updated_at``, so a save
    that follows a long enough silence is somebody coming back to it.
    """
    if str(assignment.get("status")) == "assigned":
        return True
    last = assignment.get("updated_at")
    if not isinstance(last, datetime):  # pragma: no cover — the column is NOT NULL
        return True
    return (now - last).total_seconds() >= AUDIT_COALESCE_SECONDS


def event_item_ids(event: dict[str, object]) -> list[str]:
    """The questions a review event names, read defensively.

    JSONB, so what comes back is whatever was stored. A row whose
    ``item_ids`` is not a list reads as naming nothing, which reopens no
    question — the safe direction for a value this module uses to decide
    what a patient may write.

    Public because both route surfaces render the same list, and a second
    reader of a JSON column is a second chance to disagree about what an
    unexpected shape means.
    """
    raw = event.get("item_ids")
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _written_after(row: dict[str, object], moment: object) -> bool:
    """True when this answer was written at or after *moment*."""
    written = row.get("updated_at")
    if not isinstance(written, datetime) or not isinstance(moment, datetime):
        return False
    return written >= moment


def _parse(row: dict[str, object]) -> ItemConfig | None:
    """One stored item's configuration, or ``None`` if it no longer parses."""
    try:
        return validate_item_config(str(row["item_type"]), stored_config(row["config"]))
    except ItemConfigError:
        return None


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "AUDIT_COALESCE_SECONDS",
    "RECEIPT_ATTEMPTS",
    "AnswerError",
    "AssignmentClosedError",
    "CorrectionOutstandingError",
    "CorrectionScopeError",
    "FrozenResponseError",
    "IncompleteFormError",
    "IntakeAssignmentService",
    "ReceiptUnavailableError",
    "Submission",
    "UnpublishedVersionError",
    "event_item_ids",
]
