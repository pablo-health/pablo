# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's pending chart proposals, across their notes.

* ``GET /api/patients/{patient_id}/chart-proposals`` — every proposal still
  pending on any of the client's notes, grouped by the note that made it,
  newest note first, each with what the chart says now. This is the chart's
  own door to them: a clinician opening a client before a visit sees what the
  last notes would change and decides it there, so the draft that follows is
  written against a current chart.

Decisions keep going through the note's route
(``app.routes.note_chart_proposals``): a proposal is decided against its note,
whichever screen it was read on. Reading changes nothing and audits one
patient view, never a field's text.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth.service import require_baa_acceptance
from ..chart_proposals.dependencies import (
    get_chart_proposal_repository,
    get_chart_proposal_step,
)
from ..chart_proposals.schemas import (
    NoteChartProposalGroup,
    PatientChartProposalsResponse,
    proposal_response,
)
from ..chart_proposals.step import ChartProposalStep  # noqa: TC001 — FastAPI resolves at runtime
from ..models import AuditAction, Note, User
from ..models.enums import SessionStatus
from ..models.note_source import note_source
from ..models.notes import NoteDiagnosisCode
from ..repositories import (  # noqa: TC001 — FastAPI resolves at runtime
    ChartProposalRepository,
    PatientRepository,
    TherapySessionRepository,
)
from ..services import AuditService, NoteService, get_audit_service
from ._deps import patient_not_found, visible_patient
from .notes import get_note_service
from .patients import get_patient_repository
from .sessions import get_session_repository

router = APIRouter(prefix="/api/patients", tags=["chart-proposals"])


@router.get("/{patient_id}/chart-proposals", response_model=PatientChartProposalsResponse)
def list_patient_chart_proposals(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    sessions: TherapySessionRepository = Depends(get_session_repository),
    notes: NoteService = Depends(get_note_service),
    proposals: ChartProposalRepository = Depends(get_chart_proposal_repository),
    step: ChartProposalStep = Depends(get_chart_proposal_step),
    audit: AuditService = Depends(get_audit_service),
) -> PatientChartProposalsResponse:
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    pending = proposals.list_pending_for_patient(patient.id)
    if not pending:
        return PatientChartProposalsResponse(groups=[], pending=0)

    # The notes the proposals hang off, as this user may read them: a note
    # the listing leaves out (a restricted one by another author) takes its
    # proposals out of the answer with it.
    by_note: dict[str, Note] = {n.id: n for n in notes.list_notes_for_patient(patient.id, user.id)}
    wanted = {p.note_id for p in pending} & set(by_note)
    session_ids = [sid for sid in (by_note[nid].session_id for nid in wanted) if sid is not None]
    found = sessions.get_multiple(session_ids, user.id) if session_ids else {}
    chart = step.chart(patient, user.id)

    groups: list[NoteChartProposalGroup] = []
    for note_id in wanted:
        note = by_note[note_id]
        session = found.get(note.session_id) if note.session_id else None
        source = note_source(note)
        imported = source is not None or (
            session is not None and session.status == SessionStatus.IMPORTED
        )
        date = (
            (source.visit_date if source is not None else None)
            or (session.session_date if session is not None else None)
            or note.created_at
        )
        groups.append(
            NoteChartProposalGroup(
                note_id=note.id,
                note_type=note.note_type,
                session_id=note.session_id,
                date=date,
                source_system=source.system if source is not None else None,
                imported=imported,
                source_diagnoses=[
                    NoteDiagnosisCode(code=code, description=description)
                    for code, description in (source.diagnoses if source is not None else ())
                ],
                proposals=[proposal_response(p, chart) for p in pending if p.note_id == note_id],
            )
        )
    groups.sort(key=lambda g: g.date, reverse=True)

    audit.log_patient_action(
        AuditAction.PATIENT_VIEWED,
        user,
        http_request,
        patient,
        changes={"viewed": "chart_proposals"},
    )
    return PatientChartProposalsResponse(
        groups=groups, pending=sum(len(g.proposals) for g in groups)
    )
