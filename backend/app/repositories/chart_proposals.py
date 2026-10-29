# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart-proposal repository: the updates a note or an accepted intake form proposes.

Callers reach a note's proposals only after loading the note through
``NoteService.get_note``, and a form's only after loading the form on the
patient's chart, which is the access check; the row policy
(``has_patient_access``) backs it at the database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable
    from datetime import datetime

    from ..chart_proposals.models import ChartProposal, Decision, ProposalRun


class ChartProposalRepository(ABC):
    @abstractmethod
    def list_for_note(self, note_id: str) -> list[ChartProposal]:
        """Every proposal on the note, decided or not, oldest first."""

    @abstractmethod
    def list_for_intake(self, assignment_id: str) -> list[ChartProposal]:
        """Every proposal an accepted intake form made, decided or not, oldest first."""

    @abstractmethod
    def list_pending_for_patient(self, patient_id: str) -> list[ChartProposal]:
        """Every proposal still pending across the patient's notes and forms, oldest first.

        The caller has loaded the patient, which is the access check; the row
        policy backs it at the database.
        """

    @abstractmethod
    def count_pending_by_patient(
        self, user_id: str, patient_ids: Collection[str]
    ) -> dict[str, int]:
        """Pending proposals per patient, for those of ``patient_ids`` that have any.

        Counts only what the clinician could open: a live grant on the
        patient, on a note that is not deleted or on an intake form. A patient
        with none is left out of the answer rather than mapped to zero.
        """

    @abstractmethod
    def count_patients_with_pending(self, user_id: str) -> int:
        """How many of the clinician's patients have at least one pending proposal."""

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
    """For unit tests.

    Every clinician reaches every patient unless :meth:`withhold` says
    otherwise, standing in for a missing grant in the counts.
    """

    def __init__(self) -> None:
        self._rows: dict[str, ChartProposal] = {}
        self._runs: dict[str, ProposalRun] = {}
        self._withheld: set[tuple[str, str]] = set()  # (patient_id, user_id)

    def withhold(self, patient_id: str, user_id: str) -> None:
        """``user_id`` holds no grant on ``patient_id``."""
        self._withheld.add((patient_id, user_id))

    def _waiting(self, user_id: str) -> list[ChartProposal]:
        return [
            p
            for p in self._rows.values()
            if p.pending and (p.patient_id, user_id) not in self._withheld
        ]

    def run(self, note_id: str) -> ProposalRun | None:
        return self._runs.get(note_id)

    def record_run(self, run: ProposalRun) -> None:
        self._runs[run.note_id] = run

    def list_for_note(self, note_id: str) -> list[ChartProposal]:
        mine = [p for p in self._rows.values() if p.note_id == note_id]
        return sorted(mine, key=lambda p: p.created_at)

    def list_for_intake(self, assignment_id: str) -> list[ChartProposal]:
        mine = [p for p in self._rows.values() if p.intake_assignment_id == assignment_id]
        return sorted(mine, key=lambda p: p.created_at)

    def list_pending_for_patient(self, patient_id: str) -> list[ChartProposal]:
        mine = [p for p in self._rows.values() if p.patient_id == patient_id and p.pending]
        return sorted(mine, key=lambda p: p.created_at)

    def count_pending_by_patient(
        self, user_id: str, patient_ids: Collection[str]
    ) -> dict[str, int]:
        wanted = set(patient_ids)
        counts: dict[str, int] = {}
        for p in self._waiting(user_id):
            if p.patient_id in wanted:
                counts[p.patient_id] = counts.get(p.patient_id, 0) + 1
        return counts

    def count_patients_with_pending(self, user_id: str) -> int:
        return len({p.patient_id for p in self._waiting(user_id)})

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
