# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A proposed chart update, and the clinician's decision on it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

Decision = Literal["pending", "accepted", "edited", "discarded"]
Origin = Literal["transcript", "document", "note"]
"""``transcript``: drafted from what was said. ``document``: drafted from an imported
note's document, citing its paragraphs. ``note``: the note's own text, for a field the
chart had nothing for."""

#: What a proposal from the note's own text says changed: the chart had nothing.
RECORDED_THIS_VISIT = "Recorded this visit"


RunStatus = Literal["ok", "failed", "skipped"]

MedicationAction = Literal["start", "stop", "change", "add"]
"""``start``: a medication the clinician started this visit. ``stop``, ``change``: a listed
medication stopped or taken differently now, as the clinician decided or the client says.
``add``: a medication the client takes now that the list lacks (one another prescriber
started)."""


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


@dataclass(frozen=True)
class ProposalRun:
    """How a note's proposal call last ended.

    ``failed`` means the call raised and the note has no proposals from it,
    which is not the same as nothing having changed. ``skipped`` is a note
    type the call does not run on. ``error_class`` is the exception's type
    name, never its message.
    """

    note_id: str
    patient_id: str
    status: RunStatus
    computed_at: datetime
    error_class: str | None = None


@dataclass(frozen=True)
class MedicationKept:
    """A listed medication the call was asked to decide on and proposed no change to."""

    drug_name: str
    reason: str
    """Why the visit leaves it as listed, in the call's words; empty when it gave none."""


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


@dataclass(frozen=True)
class Evidence:
    """A transcript segment a proposal cites, with its text as the transcript has it."""

    segment_id: int
    text: str


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
    id: str
    note_id: str
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

    @property
    def pending(self) -> bool:
        return self.decision == "pending"
