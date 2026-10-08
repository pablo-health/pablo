# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request / response models for a note's chart proposals."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — pydantic resolves field types at runtime
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .families import family_for

if TYPE_CHECKING:
    from ..models import Note
    from ..notes.chart_context import ChartContext
    from .models import ChartProposal, ProposalRun


class DecideProposalRequest(BaseModel):
    """Body for ``POST /api/notes/{note_id}/chart-proposals/{proposal_id}/decision``.

    ``edit`` records ``text`` instead of the proposed text.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    decision: Literal["accept", "edit", "discard"]
    text: str | None = Field(None, max_length=20_000)

    @model_validator(mode="after")
    def _edit_has_text(self) -> Self:
        if self.decision == "edit" and not self.text:
            raise ValueError("an edit needs the text to record")
        return self


class EvidenceResponse(BaseModel):
    segment_id: int
    text: str


class MedicationChangeResponse(BaseModel):
    action: Literal["start", "stop", "change", "add"]
    drug_name: str
    dose: str | None
    frequency: str | None
    category: str | None
    reason: str | None


class ChartProposalResponse(BaseModel):
    id: str
    field_key: str
    item_key: str
    label: str
    editable: bool
    """Whether the proposed text can be rewritten before it is written; a medication
    change is accepted or discarded."""
    change: MedicationChangeResponse | None
    """The structured change, for a medication proposal; ``None`` for free text."""
    current_text: str | None
    """What the chart says now, ``None`` when nothing is recorded."""
    proposed_text: str
    what_changed: str
    evidence: list[EvidenceResponse]
    origin: str
    decision: str
    decided_text: str | None
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class ProposalRunResponse(BaseModel):
    status: str
    """``ok``, ``failed`` (the note was not checked) or ``skipped``."""
    computed_at: datetime
    retryable: bool
    """Whether the call can be run again: a note drafted from a session's transcript."""


class ChartProposalsResponse(BaseModel):
    data: list[ChartProposalResponse]
    run: ProposalRunResponse | None
    """How the proposal call last ended; ``None`` for a note it never ran on."""


def run_response(run: ProposalRun | None, note: Note) -> ProposalRunResponse | None:
    if run is None:
        return None
    return ProposalRunResponse(
        status=run.status,
        computed_at=run.computed_at,
        retryable=run.status != "skipped" and note.session_id is not None,
    )


def proposal_response(proposal: ChartProposal, chart: ChartContext) -> ChartProposalResponse:
    family = family_for(proposal.field_key)
    if family is None:
        raise KeyError(proposal.field_key)
    return ChartProposalResponse(
        id=proposal.id,
        field_key=proposal.field_key,
        item_key=proposal.item_key,
        label=family.label(proposal),
        editable=family.editable,
        change=(
            MedicationChangeResponse(**asdict(proposal.change))
            if proposal.change is not None
            else None
        ),
        current_text=family.current_text(chart, proposal),
        proposed_text=proposal.proposed_text,
        what_changed=proposal.what_changed,
        evidence=[
            EvidenceResponse(segment_id=e.segment_id, text=e.text) for e in proposal.evidence
        ],
        origin=proposal.origin,
        decision=proposal.decision,
        decided_text=proposal.decided_text,
        decided_by=proposal.decided_by,
        decided_at=proposal.decided_at,
        created_at=proposal.created_at,
    )
