# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL note chart-proposal repository.

Runs inside the request's tenant-scoped session, so the row policy
(``has_patient_access``) applies to every statement here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, select

from ...chart_proposals.models import ChartProposal, Decision, Evidence, Origin
from ...db.models import NoteChartProposalRow
from ..chart_proposals import ChartProposalRepository

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from sqlalchemy.orm import Session


def _proposal(row: NoteChartProposalRow) -> ChartProposal:
    return ChartProposal(
        id=row.id,
        note_id=row.note_id,
        patient_id=row.patient_id,
        field_key=row.field_key,
        item_key=row.item_key,
        proposed_text=row.proposed_text,
        what_changed=row.what_changed,
        evidence=tuple(Evidence(int(e["segment_id"]), str(e["text"])) for e in row.evidence),
        origin=cast("Origin", row.origin),
        created_at=row.created_at,
        decision=cast("Decision", row.decision),
        decided_text=row.decided_text,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
    )


class PostgresChartProposalRepository(ChartProposalRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_note(self, note_id: str) -> list[ChartProposal]:
        rows = self._session.scalars(
            select(NoteChartProposalRow)
            .where(NoteChartProposalRow.note_id == note_id)
            .order_by(NoteChartProposalRow.created_at, NoteChartProposalRow.id)
        ).all()
        return [_proposal(row) for row in rows]

    def add(self, proposals: Iterable[ChartProposal]) -> None:
        for p in proposals:
            self._session.add(
                NoteChartProposalRow(
                    id=p.id,
                    note_id=p.note_id,
                    patient_id=p.patient_id,
                    field_key=p.field_key,
                    item_key=p.item_key,
                    proposed_text=p.proposed_text,
                    what_changed=p.what_changed,
                    evidence=[{"segment_id": e.segment_id, "text": e.text} for e in p.evidence],
                    origin=p.origin,
                    decision=p.decision,
                    created_at=p.created_at,
                )
            )
        self._session.flush()

    def set_pending_text(self, proposal_id: str, proposed_text: str) -> None:
        row = self._session.get(NoteChartProposalRow, proposal_id)
        if row is not None:
            row.proposed_text = proposed_text
            self._session.flush()

    def delete(self, proposal_ids: Iterable[str]) -> None:
        ids = list(proposal_ids)
        if ids:
            self._session.execute(
                delete(NoteChartProposalRow).where(NoteChartProposalRow.id.in_(ids))
            )
            self._session.flush()

    def decide(
        self,
        proposal_id: str,
        decision: Decision,
        decided_text: str | None,
        decided_by: str,
        decided_at: datetime,
    ) -> None:
        row = self._session.get(NoteChartProposalRow, proposal_id)
        if row is not None:
            row.decision = decision
            row.decided_text = decided_text
            row.decided_by = decided_by
            row.decided_at = decided_at
            self._session.flush()
