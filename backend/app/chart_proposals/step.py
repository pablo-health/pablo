# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The proposal step, run whenever a note's content changes.

After a draft, a redraft, a retry or an import it is split the way a draft is: the
chart is read while the caller holds its connection, the proposal call runs
with nothing checked out, and the result is stored once the note is, with a
record of how the call ended. After a clinician's edit is saved only the
proposals from the note's own text are recomputed; the proposal call is not
run again. Only the types drafted against the full chart propose updates to
it; for the rest the run is ``skipped``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..notes.chart_context import chart_context_for
from ..utcnow import utc_now
from .drafting import propose_chart_updates, propose_from_document
from .models import ProposalRun, RunStatus
from .service import ChartProposalService

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from ..models import Note, Patient, Transcript
    from ..notes import NoteTypeDefinition
    from ..notes.chart_context import ChartContext
    from ..repositories import ChartHistoryRepository, ChartProposalRepository
    from ..services.note_generation_service import NoteGenerationService
    from .models import Drafted


def proposes_chart_updates(definition: NoteTypeDefinition | None) -> bool:
    """Whether notes of this type propose chart updates: those drafted against the full chart."""
    return definition is not None and definition.reads_chart and definition.full_chart


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
    ) -> Drafted | None:
        """The proposal call, holding no connection. ``None`` when it does not run."""
        complete = generator.chart_proposal_completion()
        if complete is None or chart is None or not proposes_chart_updates(definition):
            return None
        return propose_chart_updates(complete, chart, transcript, draft=content)

    def draft_from_document(
        self,
        generator: NoteGenerationService,
        definition: NoteTypeDefinition | None,
        chart: ChartContext | None,
        document: str,
    ) -> Drafted | None:
        """The proposal call for an imported note: its document, read a paragraph at a time."""
        complete = generator.chart_proposal_completion()
        if complete is None or chart is None or not proposes_chart_updates(definition):
            return None
        return propose_from_document(complete, chart, document)

    def store(
        self,
        note: Note,
        definition: NoteTypeDefinition | None,
        chart: ChartContext | None,
        drafted: Drafted | None,
        shown: Mapping[str, Any],
    ) -> None:
        """After a draft, redraft or retry: ``drafted`` replaces what is pending, and how
        the call ended is recorded. ``shown`` is the note as it reads."""
        status: RunStatus = "skipped"
        if drafted is not None:
            status = "failed" if drafted.error_class else "ok"
        self._proposals.record_run(
            ProposalRun(
                note_id=note.id,
                patient_id=note.patient_id,
                status=status,
                computed_at=utc_now(),
                error_class=drafted.error_class if drafted is not None else None,
            )
        )
        if chart is not None and proposes_chart_updates(definition):
            # A failed call keeps what was pending from the last call that ran.
            answered = drafted is not None and not drafted.error_class
            proposals = drafted.proposals if drafted is not None and answered else None
            ChartProposalService(self._proposals).refresh(note, chart, shown, proposals)

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
