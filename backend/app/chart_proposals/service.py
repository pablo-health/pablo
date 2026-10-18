# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Storing a note's proposals, and deciding them.

A redraft replaces the proposals still pending; a decided one stays, and its
field is not proposed again on that note, so a discard is never re-offered.
The proposals from the note's own text (``recorded``) are recomputed whenever
the note's content changes: after a draft, a redraft or a saved edit.

Accepting writes the proposed text to the chart, through the chart's own
record, with the note as the source and the signer as the writer; editing is
accepting the clinician's text instead. Whoever may open the note may decide
its proposals. The note itself is never touched.

An accepted intake form's proposals (``app.chart_proposals.intake``) are
stored and decided the same way, against the form rather than a note.
Storing them again replaces those still pending, so a form's proposals are
always the latest reading of what it says; a decided one stays and its field
is not proposed again from that form.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from ..utcnow import utc_now
from .families import WriteSource, family_for, is_session_task
from .models import ChartProposal, Decision
from .recorded import recorded_proposals

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date
    from typing import Any

    from ..models import Note, Patient
    from ..notes.chart_context import ChartContext
    from ..repositories import ChartProposalRepository
    from .families import ChartWriters
    from .models import DraftedProposal

DecisionRequest = Literal["accept", "edit", "discard"]

_DECIDED: dict[DecisionRequest, Decision] = {
    "accept": "accepted",
    "edit": "edited",
    "discard": "discarded",
}


#: A task proposal's due date left as proposed.
AS_PROPOSED: Literal["as_proposed"] = "as_proposed"


@dataclass(frozen=True)
class Choice:
    """What the clinician did with a proposal; ``text`` is theirs, for an edit.

    ``due_date``, for a task the clinician said they would do, is when they
    want it due instead (``None`` for undated); left as proposed by default."""

    decision: DecisionRequest
    text: str | None = None
    due_date: date | None | Literal["as_proposed"] = AS_PROPOSED


class ProposalNotFoundError(LookupError):
    """The note has no proposal with that id."""


class ProposalDecidedError(ValueError):
    """The proposal was already accepted, edited or discarded."""


class ProposalNotEditableError(ValueError):
    """The proposal is a structured change: it is accepted or discarded, not rewritten."""


def _identity(field_key: str, item_key: str) -> tuple[str, str]:
    return field_key, item_key.strip().lower()


class ChartProposalService:
    def __init__(
        self,
        repo: ChartProposalRepository,
        writers: ChartWriters | None = None,
        *,
        visit_date: date | None = None,
    ) -> None:
        """``visit_date`` is the day of the note's visit, which a medication started
        or stopped from it is dated by; without it, today."""
        self._repo = repo
        self._writers = writers
        self._visit_date = visit_date

    def proposals(self, note_id: str) -> list[ChartProposal]:
        return self._repo.list_for_note(note_id)

    def refresh(
        self,
        note: Note,
        chart: ChartContext,
        content: Mapping[str, Any],
        drafted: Iterable[DraftedProposal] | None = None,
    ) -> None:
        """Bring the note's pending proposals up to date with its content.

        ``drafted`` is the proposal call's answer after a draft or a redraft,
        and replaces the drafted proposals still pending; ``None`` (an edit
        saved) keeps them. The proposals from the note's own text are
        recomputed from ``content`` either way, and win over one drafted from
        a transcript for the same field. One drafted from an imported note's
        document wins over them instead: the note's text is the other
        system's, possibly a stale carried block, and the drafted one cites
        the document's paragraphs, the conflicting one included. A decided
        proposal is never replaced or offered again.
        """
        # A session's tasks are kept by :meth:`refresh_session_tasks`, never here.
        existing = [
            p for p in self._repo.list_for_note(note.id) if not is_session_task(p.field_key)
        ]
        seeds = {
            _identity(p.field_key, p.item_key): p
            for p in recorded_proposals(
                content, (f.key for f in chart.history), chart.allergy_status
            )
        }
        documented = [p for p in drafted or () if p.origin == "document"]
        cited = {_identity(p.field_key, p.item_key) for p in documented}
        decided = {_identity(p.field_key, p.item_key) for p in existing if not p.pending}
        pending = {_identity(p.field_key, p.item_key): p for p in existing if p.pending}

        stale = [
            p
            for key, p in pending.items()
            if (p.origin == "note" and (key not in seeds or key in cited))
            or (p.origin == "transcript" and (drafted is not None or key in seeds))
            or (p.origin == "document" and drafted is not None)
        ]
        self._repo.delete(p.id for p in stale)
        kept = {key: p for key, p in pending.items() if p not in stale}

        for key, seed in seeds.items():
            held = kept.get(key)
            if held and held.origin == "note" and held.proposed_text != seed.proposed_text:
                self._repo.set_pending_text(held.id, seed.proposed_text)
        taken = decided | set(kept)
        added = []
        others = [p for p in drafted or () if p.origin != "document"]
        for proposal in [*documented, *seeds.values(), *others]:
            key = _identity(proposal.field_key, proposal.item_key)
            if key not in taken:
                added.append(self._new(note, proposal))
                taken.add(key)
        self._repo.add(added)

    def refresh_session_tasks(self, note: Note, drafted: Iterable[DraftedProposal]) -> None:
        """Store what the clinician said they would do, replacing the tasks still
        pending on the note. One accepted or dismissed stays, and is not proposed
        again from the line it was said on."""
        existing = [p for p in self._repo.list_for_note(note.id) if is_session_task(p.field_key)]
        self._repo.delete(p.id for p in existing if p.pending)
        taken = {_identity(p.field_key, p.item_key) for p in existing if not p.pending}
        added = []
        for proposal in drafted:
            key = _identity(proposal.field_key, proposal.item_key)
            if key not in taken:
                taken.add(key)
                added.append(self._new(note, proposal))
        self._repo.add(added)

    def refresh_intake(
        self, assignment_id: str, patient_id: str, drafted: Iterable[DraftedProposal]
    ) -> list[ChartProposal]:
        """Store an accepted form's proposals, replacing those still pending on it.

        A decided proposal stays, and its field and entry are not proposed again
        from this form, so a discard is never re-offered. Returns what was added."""
        existing = self._repo.list_for_intake(assignment_id)
        self._repo.delete(p.id for p in existing if p.pending)
        taken = {_identity(p.field_key, p.item_key) for p in existing if not p.pending}
        added: list[ChartProposal] = []
        for proposal in drafted:
            key = _identity(proposal.field_key, proposal.item_key)
            if key in taken:
                continue
            taken.add(key)
            added.append(
                ChartProposal(
                    id=str(uuid.uuid4()),
                    note_id=None,
                    intake_assignment_id=assignment_id,
                    patient_id=patient_id,
                    field_key=proposal.field_key,
                    item_key=proposal.item_key,
                    proposed_text=proposal.proposed_text,
                    what_changed=proposal.what_changed,
                    evidence=proposal.evidence,
                    origin=proposal.origin,
                    created_at=utc_now(),
                    change=proposal.change,
                )
            )
        self._repo.add(added)
        return added

    def decide(
        self,
        note: Note,
        patient: Patient,
        proposal_id: str,
        choice: Choice,
        user_id: str,
    ) -> ChartProposal:
        """Record the clinician's decision; an accept or an edit writes the chart.

        A change the chart can no longer take (a medication stopped since it was
        proposed) raises ``ChartChangedError`` and leaves the proposal pending."""
        proposal = self._find(self._repo.list_for_note(note.id), proposal_id)
        self._decide(proposal, note.id, patient, choice, user_id)
        return self._find(self._repo.list_for_note(note.id), proposal.id)

    def decide_for_intake(
        self,
        assignment_id: str,
        patient: Patient,
        proposal_id: str,
        choice: Choice,
        user_id: str,
    ) -> ChartProposal:
        """:meth:`decide`, for a proposal an accepted intake form made. The chart's
        records name no note as the source of what it writes."""
        proposal = self._find(self._repo.list_for_intake(assignment_id), proposal_id)
        self._decide(proposal, None, patient, choice, user_id)
        return self._find(self._repo.list_for_intake(assignment_id), proposal.id)

    @staticmethod
    def _find(listed: list[ChartProposal], proposal_id: str) -> ChartProposal:
        proposal = next((p for p in listed if p.id == proposal_id), None)
        if proposal is None or family_for(proposal.field_key) is None:
            raise ProposalNotFoundError(proposal_id)
        return proposal

    def _decide(
        self,
        proposal: ChartProposal,
        note_id: str | None,
        patient: Patient,
        choice: Choice,
        user_id: str,
    ) -> None:
        family = family_for(proposal.field_key)
        if family is None:
            raise ProposalNotFoundError(proposal.id)
        proposal_id = proposal.id
        if not proposal.pending:
            raise ProposalDecidedError(proposal_id)
        if choice.decision == "edit" and not family.editable:
            raise ProposalNotEditableError(proposal_id)
        if choice.due_date != AS_PROPOSED and proposal.commitment is not None:
            proposal = replace(
                proposal, commitment=replace(proposal.commitment, due_date=choice.due_date)
            )
        edited = (choice.text or "").strip() if choice.decision == "edit" else None
        if choice.decision != "discard":
            if self._writers is None:
                raise RuntimeError("deciding a proposal needs the chart's writers")
            written = edited if edited is not None else proposal.proposed_text
            if not written:
                raise ValueError("an edit needs the text to record")
            source = WriteSource(user_id, note_id, self._visit_date)
            family.apply(self._writers, patient, proposal, written, source)
        self._repo.decide(proposal.id, _DECIDED[choice.decision], edited, user_id, utc_now())

    @staticmethod
    def _new(note: Note, drafted: DraftedProposal) -> ChartProposal:
        return ChartProposal(
            id=str(uuid.uuid4()),
            note_id=note.id,
            patient_id=note.patient_id,
            field_key=drafted.field_key,
            item_key=drafted.item_key,
            proposed_text=drafted.proposed_text,
            what_changed=drafted.what_changed,
            evidence=drafted.evidence,
            origin=drafted.origin,
            created_at=utc_now(),
            change=drafted.change,
            commitment=drafted.commitment,
        )
