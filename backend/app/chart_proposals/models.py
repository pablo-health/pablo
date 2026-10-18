# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A proposed chart update, and the clinician's decision on it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

Decision = Literal["pending", "accepted", "edited", "discarded"]
Origin = Literal["transcript", "document", "note", "intake", "homework"]
"""``transcript``: drafted from what was said. ``document``: drafted from an imported
note's document, citing its paragraphs. ``note``: the note's own text, for a field the
chart had nothing for. ``intake``: an accepted intake form's answers, citing the
questions they answer. ``homework``: a homework suggestion read from the note's own
plan (:mod:`app.homework.proposals`), kept current by its own refresh, never ``refresh``'s."""

#: What a proposal from the note's own text says changed: the chart had nothing.
RECORDED_THIS_VISIT = "Recorded this visit"

#: What a proposal taken as written from an intake answer says changed.
ANSWERED_ON_INTAKE = "Answered on the intake"


RunStatus = Literal["ok", "failed", "skipped"]

MedicationAction = Literal["start", "stop", "change", "add"]
"""``start``: a medication the clinician started this visit. ``stop``, ``change``: a listed
medication the clinician stopped or changed (or acknowledged as stopped or changed), or one
another prescriber stopped or changed as the client reports. ``add``: a medication the client
takes now that the list lacks (one another prescriber started)."""


@dataclass(frozen=True)
class MedicationChange:
    """A change to the medication list, as the visit stated it.

    For ``change``, ``dose`` and ``frequency`` are the new values; ``None``
    leaves that value as it is. ``reason`` is why a medication was stopped.
    """

    action: MedicationAction
    drug_name: str
    dose: str | None = None
    frequency: str | None = None
    category: str | None = None
    reason: str | None = None
    heard_as: str = ""
    """The medication words copied exactly from the cited source."""
    name_uncertain: bool = False
    """Whether the heard name is unknown or does not support ``drug_name``."""


ConsideredReason = Literal["novelty", "transient", "interview", "present-not-past", "elsewhere"]
"""Why a history proposal was not offered (``materiality``): it says nothing the chart
does not; it is a passing event, or a lasting change nobody said; it rests on the
clinician's questions alone; it puts the present visit in a field about the past; or
the chart keeps it in another field."""


@dataclass(frozen=True)
class Considered:
    """A history proposal the call made that was considered and not offered."""

    field_key: str
    proposed_text: str
    what_changed: str
    evidence_segment_ids: tuple[int, ...]
    origin: Origin
    reason: ConsideredReason


@dataclass(frozen=True)
class ProposalRun:
    """How a note's proposal call last ended.

    ``failed`` means the call raised and the note has no proposals from it,
    which is not the same as nothing having changed. ``skipped`` is a note
    type the call does not run on. ``error_class`` is the exception's type
    name, never its message. ``considered`` are the history proposals the call
    made that were not offered, each with its reason.
    """

    note_id: str
    patient_id: str
    status: RunStatus
    computed_at: datetime
    error_class: str | None = None
    considered: tuple[Considered, ...] = ()


@dataclass(frozen=True)
class MedicationKept:
    """A listed medication the call was asked to decide on and proposed no change to."""

    drug_name: str
    reason: str
    """Why the visit leaves it as listed, one of the call's fixed reasons
    (``medication_mentions.KEPT_REASONS``); empty when it gave none."""
    note: str = ""
    """What the call said beside the reason, in its words."""


@dataclass(frozen=True)
class Drafted:
    """The proposal call's answer: what it proposed, or the error it failed with.

    ``to_decide`` are the listed medications the visit names near a different
    dose or a word saying they were stopped, which the call was asked to decide
    on; ``kept`` are those of them it proposed no change to, each with its
    reason. Neither is stored: they are what an evaluation reads.
    """

    proposals: list[DraftedProposal]
    error_class: str | None = None
    to_decide: tuple[str, ...] = ()
    kept: tuple[MedicationKept, ...] = ()
    considered: tuple[Considered, ...] = ()
    """The history proposals not offered, with their reasons; stored on the run."""
    homework: tuple[DraftedProposal, ...] | None = None
    """The homework read from the draft's prose plan fields (``app.homework.proposals``),
    beside the call; ``None`` when there was no such read or it failed."""


@dataclass(frozen=True)
class Evidence:
    """A transcript segment a proposal cites, with its text as the transcript has it.

    For an intake, the answer it cites: ``segment_id`` is the question's position on
    the form, ``text`` the answer as written, ``item_id`` the question and
    ``question`` its wording, so the chart can open the form at that answer."""

    segment_id: int
    text: str
    item_id: str | None = None
    question: str | None = None


@dataclass(frozen=True)
class DraftedProposal:
    """One change the proposal call found, its evidence already checked."""

    field_key: str
    proposed_text: str
    what_changed: str
    evidence: tuple[Evidence, ...]
    item_key: str = ""
    """The entry within a list field (an allergy's substance); empty for free text."""
    origin: Origin = "transcript"
    change: MedicationChange | None = None
    """The structured change, for a family whose proposals are actions (the medication list)."""


@dataclass(frozen=True)
class ChartProposal:
    """A proposed update, from a note (``note_id``) or an accepted intake form
    (``intake_assignment_id``): exactly one of the two is set."""

    id: str
    note_id: str | None
    patient_id: str
    field_key: str
    item_key: str
    proposed_text: str
    what_changed: str
    evidence: tuple[Evidence, ...]
    origin: Origin
    created_at: datetime
    decision: Decision = "pending"
    decided_text: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    change: MedicationChange | None = None
    intake_assignment_id: str | None = None

    @property
    def pending(self) -> bool:
        return self.decision == "pending"
