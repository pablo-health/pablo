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

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..chart_history.fields import is_history_key
from ..notes.chart_context import chart_context_for
from ..notes.chart_fields import rendered_fields
from ..utcnow import utc_now
from .drafting import (
    document_segments,
    propose_chart_updates,
    propose_from_document,
    said_this_visit,
    screened,
    transcript_segments,
)
from .models import ProposalRun, RunStatus
from .service import ChartProposalService

if TYPE_CHECKING:
    from typing import Any

    from ..medications.repository import MedicationRepository
    from ..models import Note, Patient, Transcript
    from ..notes import NoteTypeDefinition
    from ..notes.chart_context import ChartContext
    from ..people_term_lookup import PeopleTermLookup
    from ..repositories import ChartHistoryRepository, ChartProposalRepository
    from ..services.note_generation_service import GeneratedNote, NoteGenerationService
    from .models import Drafted


def proposes_chart_updates(definition: NoteTypeDefinition | None) -> bool:
    """Whether notes of this type propose chart updates: those drafted against the full chart."""
    return definition is not None and definition.reads_chart and definition.full_chart


def extracted_fields(definition: NoteTypeDefinition | None) -> frozenset[str]:
    """The history fields this type prints from the chart, which the extraction beside
    the draft reads the visit for. A type that drafts a history field itself (an
    intake) leaves it out: nothing extracts what was said about it."""
    if definition is None:
        return frozenset()
    return frozenset(r.source for r in rendered_fields(definition) if is_history_key(r.source))


class ChartProposalStep:
    def __init__(
        self,
        proposals: ChartProposalRepository,
        history: ChartHistoryRepository,
        medications: MedicationRepository | None = None,
        people: PeopleTermLookup | None = None,
    ) -> None:
        self._proposals = proposals
        self._history = history
        self._medications = medications
        self._people = people

    def chart(self, patient: Patient, user_id: str | None = None) -> ChartContext:
        """What the proposals are measured against, for a caller that has no chart yet.
        The medication list is read as ``user_id`` sees it, and the chart names the
        person in their word, when given."""
        medications = (
            self._medications.list_by_patient(patient.id, user_id)
            if self._medications is not None and user_id is not None
            else []
        )
        return chart_context_for(
            patient,
            [],
            medications,
            history=self._history.entries(patient.id),
            person=self.person(user_id) if user_id is not None else None,
        )

    def person(self, user_id: str) -> str | None:
        """``user_id``'s word for the person seen, singular; ``None`` when not known here."""
        return self._people.person(user_id) if self._people is not None else None

    def draft(
        self,
        generator: NoteGenerationService,
        definition: NoteTypeDefinition | None,
        chart: ChartContext | None,
        transcript: Transcript,
        note: GeneratedNote | Mapping[str, Any],
    ) -> Drafted | None:
        """The proposal call, holding no connection. ``None`` when it does not run.

        ``note`` is the draft just generated, whose extraction call says what the
        visit said about the chart's fields and on which lines. A note's content
        alone (a retry: the extraction is not kept) is read for what it marks as
        stated this visit instead.

        The reply's history proposals are then checked for materiality: one not worth
        offering is kept on the run as considered, with its reason (:mod:`.materiality`)."""
        complete = generator.chart_proposal_completion()
        if complete is None or chart is None or not proposes_chart_updates(definition):
            return None
        draft, statements = (
            (note, None) if isinstance(note, Mapping) else (note.content, note.chart_statements)
        )
        drafted = propose_chart_updates(
            complete, chart, transcript, draft=draft, statements=statements
        )
        said = said_this_visit(extracted_fields(definition), statements, draft)
        return screened(drafted, chart, transcript_segments(transcript), said)

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
        drafted = propose_from_document(complete, chart, document)
        return screened(drafted, chart, document_segments(document))

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
                considered=drafted.considered if drafted is not None else (),
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
