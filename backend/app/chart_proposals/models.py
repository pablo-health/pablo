# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A proposed chart update, and the clinician's decision on it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

Decision = Literal["pending", "accepted", "edited", "discarded"]
Origin = Literal["transcript", "note"]
"""``transcript``: drafted from what was said. ``note``: the note's own text, for a
field the chart had nothing for."""

#: What a proposal from the note's own text says changed: the chart had nothing.
RECORDED_THIS_VISIT = "Recorded this visit"


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
