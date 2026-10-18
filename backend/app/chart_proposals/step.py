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
from dataclasses import replace
from typing import TYPE_CHECKING

from ..chart_history.fields import is_history_key
from ..notes.chart_context import chart_context_for
from ..notes.chart_fields import rendered_fields
from ..utcnow import utc_now
from .commitments import propose_commitments
from .drafting import (
    document_segments,
    propose_chart_updates,
    propose_from_document,
    said_this_visit,
    screened,
    transcript_segments,
)
from .models import Drafted, ProposalRun, RunStatus
from .service import ChartProposalService

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date
    from typing import Any

    from ..medications.repository import MedicationRepository
    from ..models import Note, Patient, Transcript
    from ..notes import NoteTypeDefinition
    from ..notes.chart_context import ChartContext
    from ..people_term_lookup import PeopleTermLookup
    from ..problems.models import Problem
    from ..repositories import ChartHistoryRepository, ChartProposalRepository
    from ..services.note_generation_service import GeneratedNote, NoteGenerationService
    from .models import DraftedProposal


def proposes_chart_updates(definition: NoteTypeDefinition | None) -> bool:
    """Whether notes of this type propose chart updates: those drafted against the full chart."""
    return definition is not None and definition.reads_chart and definition.full_chart


def proposes_commitments(definition: NoteTypeDefinition | None) -> bool:
    """Whether a session drafted as this type proposes what the clinician said they would
    do: any type with a client in it. Never a restricted type (a psychotherapy note),
    which is written by hand and whose session is not read for it."""
    return definition is not None and definition.reads_chart and not definition.restricted


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

    def chart(
        self,
        patient: Patient,
        user_id: str | None = None,
        problems: Iterable[Problem] = (),
    ) -> ChartContext:
        """What the proposals are measured against, for a caller that has no chart yet.
        The medication list is read as ``user_id`` sees it, and the chart names the
        person in their word, when given. ``problems`` is the problem list, for a
        caller that prints the diagnoses from it; proposals never read it."""
        medications = (
            self._medications.list_by_patient(patient.id, user_id)
            if self._medications is not None and user_id is not None
            else []
        )
        return chart_context_for(
            patient,
            problems,
            medications,
            history=self._history.entries(patient.id),
            person=self.person(user_id) if user_id is not None else None,
        )

    def person(self, user_id: str) -> str | None:
        """``user_id``'s word for the person seen, singular; ``None`` when not known here."""
        return self._people.person(user_id) if self._people is not None else None

    def draft(  # noqa: PLR0913 — visit_date is keyword-only, named at each call site
        self,
        generator: NoteGenerationService,
        definition: NoteTypeDefinition | None,
        chart: ChartContext | None,
        transcript: Transcript,
        note: GeneratedNote | Mapping[str, Any],
        *,
        visit_date: date | None = None,
    ) -> Drafted | None:
        """The proposal calls, holding no connection. ``None`` when neither runs.

        ``note`` is the draft just generated, whose extraction call says what the
        visit said about the chart's fields and on which lines. A note's content
        alone (a retry: the extraction is not kept) is read for what it marks as
        stated this visit instead.

        The reply's history proposals are then checked for materiality: one not worth
        offering is kept on the run as considered, with its reason (:mod:`.materiality`).

        Any type with a client in it also asks what the clinician said they would do
        (:mod:`.commitments`), its spoken deadlines read from ``visit_date``, the day
        of the session (today when not given). A type that proposes no chart updates
        answers with that alone."""
        complete = generator.chart_proposal_completion()
        if complete is None:
            return None
        commitments: list[DraftedProposal] | None = None
        commitment_error: str | None = None
        if proposes_commitments(definition):
            commitments, commitment_error = propose_commitments(
                complete, transcript, visit_date or utc_now().date()
            )
        if chart is None or not proposes_chart_updates(definition):
            if commitments is None and commitment_error is None:
                return None
            return Drafted(
                [],
                commitments=tuple(commitments) if commitments is not None else None,
                commitment_error=commitment_error,
            )
        draft, statements = (
            (note, None) if isinstance(note, Mapping) else (note.content, note.chart_statements)
        )
        drafted = propose_chart_updates(
            complete, chart, transcript, draft=draft, statements=statements
        )
        said = said_this_visit(extracted_fields(definition), statements, draft)
        return replace(
            screened(drafted, chart, transcript_segments(transcript), said),
            commitments=tuple(commitments) if commitments is not None else None,
            commitment_error=commitment_error,
        )

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

    def seed_from_document(
        self, generator: NoteGenerationService, chart: ChartContext, document: str
    ) -> Drafted | None:
        """The proposal call for a note an archive import landed, whatever its type.

        The gate on which note types propose asks whether a draft was written
        against the chart; it does not apply here. The note is a reference
        copy, and the purpose is to seed an empty chart from what it says.
        ``None`` only when no model is configured.
        """
        complete = generator.chart_proposal_completion()
        if complete is None:
            return None
        drafted = propose_from_document(complete, chart, document)
        return screened(drafted, chart, document_segments(document))

    def store_seed(self, note: Note, chart: ChartContext, drafted: Drafted) -> None:
        """After :meth:`seed_from_document`: the proposals replace what is pending on the
        note and how the call ended is recorded, with no note-text proposals to recompute
        (an imported narrative has no chart-shaped fields)."""
        status: RunStatus = "failed" if drafted.error_class else "ok"
        self._proposals.record_run(
            ProposalRun(
                note_id=note.id,
                patient_id=note.patient_id,
                status=status,
                computed_at=utc_now(),
                error_class=drafted.error_class,
                considered=drafted.considered,
            )
        )
        if not drafted.error_class:
            ChartProposalService(self._proposals).refresh(note, chart, {}, drafted.proposals)

    def store(
        self,
        note: Note,
        definition: NoteTypeDefinition | None,
        chart: ChartContext | None,
        drafted: Drafted | None,
        shown: Mapping[str, Any],
    ) -> None:
        """After a draft, redraft or retry: ``drafted`` replaces what is pending, and how
        the call ended is recorded. ``shown`` is the note as it reads.

        The run records the chart call alone: a type that proposes no chart updates is
        ``skipped`` whether or not its session proposed tasks. Those tasks replace the
        ones still pending on the note; a failed commitment call keeps them."""
        status: RunStatus = "skipped"
        if drafted is not None and proposes_chart_updates(definition):
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
        if drafted is not None and drafted.commitments is not None:
            ChartProposalService(self._proposals).refresh_session_tasks(note, drafted.commitments)

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
