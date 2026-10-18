# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request / response models for a note's chart proposals."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — pydantic resolves field types at runtime
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.notes import NoteDiagnosisCode  # noqa: TC001 — pydantic field type
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
    item_id: str | None = None
    """For an intake form's proposal, the question the cited answer was given to."""
    question: str | None = None
    """That question's wording."""


class MedicationChangeResponse(BaseModel):
    action: Literal["start", "stop", "change", "add"]
    drug_name: str
    dose: str | None
    frequency: str | None
    category: str | None
    reason: str | None
    heard_as: str
    name_uncertain: bool


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


class ConsideredResponse(BaseModel):
    """A history proposal the call made and did not offer: its field and why."""

    field_key: str
    reason: str
    """``novelty``, ``transient``, ``interview``, ``present-not-past`` or ``elsewhere``."""


class ProposalRunResponse(BaseModel):
    status: str
    """``ok``, ``failed`` (the note was not checked) or ``skipped``."""
    computed_at: datetime
    retryable: bool
    """Whether the call can be run again: a note drafted from a session's transcript."""
    considered: list[ConsideredResponse]
    """The history proposals the call made and did not offer, each with its reason. The
    text it proposed stays on the run record and is not sent."""


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
        considered=[
            ConsideredResponse(field_key=c.field_key, reason=c.reason) for c in run.considered
        ],
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
            EvidenceResponse(
                segment_id=e.segment_id, text=e.text, item_id=e.item_id, question=e.question
            )
            for e in proposal.evidence
        ],
        origin=proposal.origin,
        decision=proposal.decision,
        decided_text=proposal.decided_text,
        decided_by=proposal.decided_by,
        decided_at=proposal.decided_at,
        created_at=proposal.created_at,
    )


class NoteChartProposalGroup(BaseModel):
    """One note's pending proposals, with enough about the note to head them."""

    note_id: str
    note_type: str
    session_id: str | None
    #: The visit the note records (its session's date, or an imported note's visit
    #: date), else when the note was written.
    date: datetime
    #: Where an archive import brought the note from; ``None`` otherwise.
    source_system: str | None
    #: A reference copy of a note signed elsewhere.
    imported: bool
    #: The diagnosis codes an imported note carried from its system, offered to
    #: the problem list beside the proposals; empty for a note written here.
    source_diagnoses: list[NoteDiagnosisCode] = Field(default_factory=list)
    proposals: list[ChartProposalResponse]


class IntakeChartProposalGroup(BaseModel):
    """One accepted intake form's pending proposals, with enough about the form to
    head them."""

    assignment_id: str
    packet_name: str
    #: When the form was handed in, else when it was accepted.
    date: datetime
    proposals: list[ChartProposalResponse]


class PatientChartProposalsResponse(BaseModel):
    """A client's pending chart proposals, grouped by the note that made them,
    newest note first, and by the intake form that made them, newest first.
    Decisions go through the note's own route, or the form's."""

    groups: list[NoteChartProposalGroup]
    intakes: list[IntakeChartProposalGroup] = Field(default_factory=list)
    pending: int
