# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note chart-proposal repository.

Callers reach a note's proposals only after loading the note through
``NoteService.get_note``, which is the access check; the row policy
(``has_patient_access``) backs it at the database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from ..chart_proposals.models import ChartProposal, Decision, ProposalRun


class ChartProposalRepository(ABC):
    @abstractmethod
    def list_for_note(self, note_id: str) -> list[ChartProposal]:
        """Every proposal on the note, decided or not, oldest first."""

    @abstractmethod
    def add(self, proposals: Iterable[ChartProposal]) -> None:
        """Store new proposals."""

    @abstractmethod
    def set_pending_text(self, proposal_id: str, proposed_text: str) -> None:
        """Replace a pending proposal's text."""

    @abstractmethod
    def delete(self, proposal_ids: Iterable[str]) -> None:
        """Remove proposals; an id that is not there is ignored."""

    @abstractmethod
    def decide(
        self,
        proposal_id: str,
        decision: Decision,
        decided_text: str | None,
        decided_by: str,
        decided_at: datetime,
    ) -> None:
        """Record the clinician's decision on a proposal."""

    @abstractmethod
    def run(self, note_id: str) -> ProposalRun | None:
        """How the note's proposal call last ended; ``None`` before it has run."""

    @abstractmethod
    def record_run(self, run: ProposalRun) -> None:
        """Replace the note's run record."""


class InMemoryChartProposalRepository(ChartProposalRepository):
    """For unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, ChartProposal] = {}
        self._runs: dict[str, ProposalRun] = {}

    def run(self, note_id: str) -> ProposalRun | None:
        return self._runs.get(note_id)

    def record_run(self, run: ProposalRun) -> None:
        self._runs[run.note_id] = run

    def list_for_note(self, note_id: str) -> list[ChartProposal]:
        mine = [p for p in self._rows.values() if p.note_id == note_id]
        return sorted(mine, key=lambda p: p.created_at)

    def add(self, proposals: Iterable[ChartProposal]) -> None:
        for proposal in proposals:
            self._rows[proposal.id] = proposal

    def set_pending_text(self, proposal_id: str, proposed_text: str) -> None:
        self._rows[proposal_id] = replace(self._rows[proposal_id], proposed_text=proposed_text)

    def delete(self, proposal_ids: Iterable[str]) -> None:
        for proposal_id in proposal_ids:
            self._rows.pop(proposal_id, None)

    def decide(
        self,
        proposal_id: str,
        decision: Decision,
        decided_text: str | None,
        decided_by: str,
        decided_at: datetime,
    ) -> None:
        self._rows[proposal_id] = replace(
            self._rows[proposal_id],
            decision=decision,
            decided_text=decided_text,
            decided_by=decided_by,
            decided_at=decided_at,
        )
