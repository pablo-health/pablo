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
layer adds on top is which rows a patient may touch at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from ..intake.answers import AnswerError, validate_answer
from ..intake.completion import Completion, CompletionItem, assess
from ..intake.items import ItemConfigError, stored_config, validate_item_config
from ..repositories.patient_intake_assignment import WRITABLE_STATUSES
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..intake.items import ItemConfig
    from ..repositories.intake_packet import IntakePacketRepository
    from ..repositories.patient_intake_assignment import PatientIntakeAssignmentRepository

#: How long a burst of saves counts as one visit for the audit log. A
#: patient works through a form a question at a time, and a row per
#: keystroke-sized save would bury the disclosures that carry weight. See
#: :meth:`IntakeAssignmentService.save_answer`.
AUDIT_COALESCE_SECONDS = 900


class UnpublishedVersionError(RuntimeError):
    """An attempt to send a version of a form that is still a draft."""


class AssignmentClosedError(RuntimeError):
    """An attempt to answer a form that is no longer the patient's to fill in."""


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
        """The patient's live draft answers, keyed by the item's id."""
        return {
            str(row["item_id"]): _as_mapping(row["value"])
            for row in self._repo.list_draft_responses(assignment_id, patient_id)
        }

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
        patient's to fill in, ``LookupError`` when the item is not on its
        version, and :class:`AnswerError` when the value does not fit the
        question.
        """
        if str(assignment["status"]) not in WRITABLE_STATUSES:
            raise AssignmentClosedError(str(assignment["id"]))

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
        validate_answer(config, value)

        now = utc_now()
        worth_auditing = _starts_a_visit(assignment, now)
        stored = self._repo.save_draft_response(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": str(assignment["id"]),
                "patient_id": patient_id,
                "item_id": item_id,
                "value": value,
                "draft": True,
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        self._repo.record_save(str(assignment["id"]), patient_id, now)
        return stored, worth_auditing


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
    "AnswerError",
    "AssignmentClosedError",
    "IntakeAssignmentService",
    "UnpublishedVersionError",
]
