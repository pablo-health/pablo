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
class Drafted:
    """The proposal call's answer: what it proposed, or the error it failed with."""

    proposals: list[DraftedProposal]
    error_class: str | None = None


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

    @property
    def pending(self) -> bool:
        return self.decision == "pending"
