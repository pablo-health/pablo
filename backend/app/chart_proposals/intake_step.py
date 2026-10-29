# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Proposing chart updates from a form the clinician has just accepted.

Run by the accept route after the form is accepted and its AI-notes answer
recorded. The answers the form says where to put are mapped straight away
(:func:`app.chart_proposals.intake.mapped_proposals`). The written answers
left over are read by the proposal call only when the client's AI-notes
answer on record, which now includes this form's, is that they consent;
the request's connection is released for the call, as in the draft worker,
so the acceptance is already committed when it runs. A call that fails
proposes nothing from those answers and keeps the mapped ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db import release_db_connection
from .drafting import propose_from_intake, screened
from .intake import admitted, form_answers, mapped_proposals, pointed, read_segments
from .service import ChartProposalService

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..models import Patient
    from ..repositories import ChartProposalRepository
    from ..services.note_generation_service import NoteGenerationService
    from .models import DraftedProposal
    from .step import ChartProposalStep


@dataclass(frozen=True)
class AcceptedForm:
    """The form just accepted: whose it is, its questions and the live answers to
    them (as the intake assignment service hands them back), and whether the
    client's AI-notes answer on record is that they consent."""

    assignment_id: str
    patient: Patient
    items: Sequence[Mapping[str, object]]
    answers: Mapping[str, Mapping[str, object]]
    ai_consented: bool


@dataclass(frozen=True)
class IntakeProposalOutcome:
    """How many proposals the form made, and whether its written answers were read.

    ``read`` is ``consent`` (read with the client's agreement), ``no_consent``
    (written answers left unread), ``failed`` (the call raised) or ``none``
    (nothing to read, or no model configured). Counts and words only: what
    goes to the audit log."""

    proposed: int
    read: str


class IntakeProposalStep:
    def __init__(
        self,
        proposals: ChartProposalRepository,
        step: ChartProposalStep,
        generator: NoteGenerationService | None,
    ) -> None:
        self._proposals = proposals
        self._step = step
        self._generator = generator

    def propose(self, form: AcceptedForm, user_id: str) -> IntakeProposalOutcome:
        """Store the form's chart proposals, replacing any still pending on it. The chart
        is read as ``user_id`` sees it."""
        chart = self._step.chart(form.patient, user_id)
        answers = form_answers(form.items, form.answers)
        mapped = mapped_proposals(answers, chart)
        read = "none"
        extracted: list[DraftedProposal] = []
        complete = (
            self._generator.chart_proposal_completion() if self._generator is not None else None
        )
        if mapped.to_read and not form.ai_consented:
            read = "no_consent"
        elif mapped.to_read and complete is not None:
            segments = read_segments(mapped.to_read)
            release_db_connection()
            drafted = screened(propose_from_intake(complete, chart, segments), chart, segments)
            if drafted.error_class:
                read = "failed"
            else:
                read = "consent"
                extracted = pointed(drafted.proposals, mapped.to_read)
        added = ChartProposalService(self._proposals).refresh_intake(
            form.assignment_id, form.patient.id, admitted([*mapped.proposals, *extracted], chart)
        )
        return IntakeProposalOutcome(proposed=len(added), read=read)


__all__ = ["AcceptedForm", "IntakeProposalOutcome", "IntakeProposalStep"]
