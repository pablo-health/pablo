# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The proposal step, run whenever a note's content changes.

After a draft or a redraft it is split the way a draft is: the chart is read
while the caller holds its connection, the proposal call runs with nothing
checked out, and the result is stored once the note is. After a clinician's
edit is saved only the proposals from the note's own text are recomputed; the
proposal call is not run again. Only the types a practice defines for itself
read the full chart, so only they propose updates to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..notes.chart_context import chart_context_for
from ..notes.registry import is_practice_key
from .drafting import propose_chart_updates
from .service import ChartProposalService

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from ..models import Note, Patient, Transcript
    from ..notes import NoteTypeDefinition
    from ..notes.chart_context import ChartContext
    from ..repositories import ChartHistoryRepository, ChartProposalRepository
    from ..services.note_generation_service import NoteGenerationService
    from .models import DraftedProposal


def proposes_chart_updates(definition: NoteTypeDefinition | None) -> bool:
    """Whether notes of this type propose chart updates: those drafted against the full chart."""
    return definition is not None and definition.reads_chart and is_practice_key(definition.key)


class ChartProposalStep:
    def __init__(self, proposals: ChartProposalRepository, history: ChartHistoryRepository) -> None:
        self._proposals = proposals
        self._history = history

    def chart(self, patient: Patient) -> ChartContext:
        """What the proposals are measured against, for a caller that has no chart yet."""
        return chart_context_for(patient, [], history=self._history.entries(patient.id))

    def draft(
        self,
        generator: NoteGenerationService,
        definition: NoteTypeDefinition | None,
        chart: ChartContext | None,
        transcript: Transcript,
        content: Mapping[str, Any],
    ) -> list[DraftedProposal]:
        """The proposal call. Holds no connection; returns nothing for a type that proposes none."""
        complete = generator.chart_proposal_completion()
        if complete is None or chart is None or not proposes_chart_updates(definition):
            return []
        return propose_chart_updates(complete, chart, transcript, draft=content)

    def store(
        self,
        note: Note,
        definition: NoteTypeDefinition | None,
        chart: ChartContext | None,
        drafted: list[DraftedProposal],
        shown: Mapping[str, Any],
    ) -> None:
        """After a draft or redraft: ``drafted`` replaces what is pending; ``shown`` is the note."""
        if chart is not None and proposes_chart_updates(definition):
            ChartProposalService(self._proposals).refresh(note, chart, shown, drafted)

    def edited(
        self,
        note: Note,
        definition: NoteTypeDefinition | None,
        patient: Patient,
        shown: Mapping[str, Any],
    ) -> None:
        """After a clinician's edit is saved: what the note's own text proposes, again."""
        if proposes_chart_updates(definition):
            ChartProposalService(self._proposals).refresh(note, self.chart(patient), shown)
