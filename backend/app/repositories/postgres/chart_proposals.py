# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL chart-proposal repository, for notes' and intake forms' proposals.

Runs inside the request's tenant-scoped session, so the row policy
(``has_patient_access``) applies to every statement here.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, or_, select

from ...chart_proposals.models import (
    ChartProposal,
    Considered,
    Evidence,
    MedicationChange,
    ProposalRun,
)
from ...db.models import (
    NoteChartProposalRow,
    NoteChartProposalRunRow,
    NoteRow,
    PatientClinicianRow,
)
from ..chart_proposals import ChartProposalRepository
from ._grants import live_grant

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable
    from datetime import datetime

    from sqlalchemy import Select
    from sqlalchemy.orm import Session

    from ...chart_proposals.models import (
        ConsideredReason,
        Decision,
        MedicationAction,
        Origin,
        RunStatus,
    )


def _change(raw: dict[str, Any] | None) -> MedicationChange | None:
    if not raw:
        return None
    return MedicationChange(
        action=cast("MedicationAction", raw["action"]),
        drug_name=str(raw["drug_name"]),
        dose=raw.get("dose"),
        frequency=raw.get("frequency"),
        category=raw.get("category"),
        reason=raw.get("reason"),
        # Rows written before ``heard_as`` existed need a useful display
        # fallback.  Preserve an explicitly stored empty string, though, so a
        # newly written MedicationChange still round-trips exactly.
        heard_as=str(raw["heard_as"] if "heard_as" in raw else raw["drug_name"]),
        name_uncertain=bool(raw.get("name_uncertain", False)),
    )


def _evidence(raw: dict[str, Any]) -> Evidence:
    item_id, question = raw.get("item_id"), raw.get("question")
    return Evidence(
        int(raw["segment_id"]),
        str(raw["text"]),
        item_id=str(item_id) if item_id is not None else None,
        question=str(question) if question is not None else None,
    )


def _evidence_json(evidence: Evidence) -> dict[str, Any]:
    """A cited segment as stored. An intake's carries its question; a note's does not,
    which keeps a note's rows exactly as they were written before forms proposed."""
    stored: dict[str, Any] = {"segment_id": evidence.segment_id, "text": evidence.text}
    if evidence.item_id is not None:
        stored["item_id"] = evidence.item_id
        stored["question"] = evidence.question
    return stored


def _proposal(row: NoteChartProposalRow) -> ChartProposal:
    return ChartProposal(
        id=row.id,
        note_id=row.note_id,
        intake_assignment_id=row.intake_assignment_id,
        patient_id=row.patient_id,
        field_key=row.field_key,
        item_key=row.item_key,
        proposed_text=row.proposed_text,
        what_changed=row.what_changed,
        evidence=tuple(_evidence(e) for e in row.evidence),
        origin=cast("Origin", row.origin),
        created_at=row.created_at,
        decision=cast("Decision", row.decision),
        decided_text=row.decided_text,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        change=_change(row.change),
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

    def list_for_intake(self, assignment_id: str) -> list[ChartProposal]:
        rows = self._session.scalars(
            select(NoteChartProposalRow)
            .where(NoteChartProposalRow.intake_assignment_id == assignment_id)
            .order_by(NoteChartProposalRow.created_at, NoteChartProposalRow.id)
        ).all()
        return [_proposal(row) for row in rows]

    def list_pending_for_patient(self, patient_id: str) -> list[ChartProposal]:
        rows = self._session.scalars(
            select(NoteChartProposalRow)
            .where(
                NoteChartProposalRow.patient_id == patient_id,
                NoteChartProposalRow.decision == "pending",
            )
            .order_by(NoteChartProposalRow.created_at, NoteChartProposalRow.id)
        ).all()
        return [_proposal(row) for row in rows]

    def count_pending_by_patient(
        self, user_id: str, patient_ids: Collection[str]
    ) -> dict[str, int]:
        ids = list(patient_ids)
        if not ids:
            return {}
        rows = self._session.execute(
            _pending_visible_to(
                user_id,
                select(NoteChartProposalRow.patient_id, func.count()),
            )
            .where(NoteChartProposalRow.patient_id.in_(ids))
            .group_by(NoteChartProposalRow.patient_id)
        ).all()
        return {str(patient_id): int(count) for patient_id, count in rows}

    def count_patients_with_pending(self, user_id: str) -> int:
        return (
            self._session.scalar(
                _pending_visible_to(
                    user_id,
                    select(func.count(func.distinct(NoteChartProposalRow.patient_id))),
                )
            )
            or 0
        )

    def add(self, proposals: Iterable[ChartProposal]) -> None:
        for p in proposals:
            self._session.add(
                NoteChartProposalRow(
                    id=p.id,
                    note_id=p.note_id,
                    intake_assignment_id=p.intake_assignment_id,
                    patient_id=p.patient_id,
                    field_key=p.field_key,
                    item_key=p.item_key,
                    proposed_text=p.proposed_text,
                    what_changed=p.what_changed,
                    evidence=[_evidence_json(e) for e in p.evidence],
                    origin=p.origin,
                    decision=p.decision,
                    created_at=p.created_at,
                    change=asdict(p.change) if p.change is not None else None,
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

    def run(self, note_id: str) -> ProposalRun | None:
        row = self._session.get(NoteChartProposalRunRow, note_id)
        if row is None:
            return None
        return ProposalRun(
            note_id=row.note_id,
            patient_id=row.patient_id,
            status=cast("RunStatus", row.status),
            computed_at=row.computed_at,
            error_class=row.error_class,
            considered=tuple(_considered(raw) for raw in row.considered or ()),
        )

    def record_run(self, run: ProposalRun) -> None:
        row = self._session.get(NoteChartProposalRunRow, run.note_id)
        if row is None:
            row = NoteChartProposalRunRow(note_id=run.note_id, patient_id=run.patient_id)
            self._session.add(row)
        row.status = run.status
        row.error_class = run.error_class
        row.computed_at = run.computed_at
        row.considered = [
            {**asdict(c), "evidence_segment_ids": list(c.evidence_segment_ids)}
            for c in run.considered
        ]
        self._session.flush()


def _pending_visible_to[T: tuple[Any, ...]](user_id: str, query: Select[T]) -> Select[T]:
    """Restrict ``query`` to pending proposals the clinician could open.

    Through a live grant on the patient, from a note that is not deleted or from
    an intake form, the same reach the chart's own listing has; the row policy
    backs it.
    """
    return (
        query.select_from(NoteChartProposalRow)
        .outerjoin(NoteRow, NoteRow.id == NoteChartProposalRow.note_id)
        .join(
            PatientClinicianRow,
            PatientClinicianRow.patient_id == NoteChartProposalRow.patient_id,
        )
        .where(
            NoteChartProposalRow.decision == "pending",
            or_(
                NoteChartProposalRow.intake_assignment_id.is_not(None),
                NoteRow.deleted_at.is_(None) & NoteRow.id.is_not(None),
            ),
            *live_grant(user_id),
        )
    )


def _considered(raw: dict[str, Any]) -> Considered:
    return Considered(
        field_key=str(raw["field_key"]),
        proposed_text=str(raw["proposed_text"]),
        what_changed=str(raw.get("what_changed") or ""),
        evidence_segment_ids=tuple(int(i) for i in raw.get("evidence_segment_ids") or ()),
        origin=cast("Origin", raw["origin"]),
        reason=cast("ConsideredReason", raw["reason"]),
    )
