# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's pending chart proposals, across their notes and intake forms.

* ``GET /api/patients/{patient_id}/chart-proposals`` — every proposal still
  pending on any of the client's notes, grouped by the note that made it,
  newest note first, and on any of their accepted intake forms, grouped by
  form, each with what the chart says now. This is the chart's own door to
  them: a clinician opening a client before a visit sees what the last notes
  and the intake would change and decides it there, so the draft that
  follows is written against a current chart.
* ``POST /api/patients/{patient_id}/intake-assignments/{assignment_id}/
  chart-proposals/{proposal_id}/decision`` — accept, edit or discard one an
  accepted intake form made. Accept and
  edit write the chart; the form is not changed. Same rules and errors as a
  note's.

A note's proposals are decided through the note's route
(``app.routes.note_chart_proposals``): a proposal is decided against its
source, whichever screen it was read on. Reading changes nothing and audits
one patient view, never a field's text.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import require_baa_acceptance
from ..chart_history.dependencies import get_chart_history_repository
from ..chart_history.service import ChartHistoryService
from ..chart_proposals.dependencies import (
    get_chart_proposal_repository,
    get_chart_proposal_step,
    get_proposal_medication_repository,
)
from ..chart_proposals.families import ChartChangedError, ChartWriters
from ..chart_proposals.schemas import (
    ChartProposalResponse,
    DecideProposalRequest,
    IntakeChartProposalGroup,
    NoteChartProposalGroup,
    PatientChartProposalsResponse,
    proposal_response,
)
from ..chart_proposals.service import (
    ChartProposalService,
    Choice,
    ProposalDecidedError,
    ProposalNotEditableError,
    ProposalNotFoundError,
)
from ..chart_proposals.step import ChartProposalStep  # noqa: TC001 — FastAPI resolves at runtime
from ..medications.repository import (  # noqa: TC001 — FastAPI resolves at runtime
    MedicationRepository,
)
from ..medications.service import MedicationService
from ..models import AuditAction, Note, User
from ..models.audit import ResourceType
from ..models.enums import SessionStatus
from ..models.note_source import note_source
from ..models.notes import NoteDiagnosisCode
from ..repositories import (  # noqa: TC001 — FastAPI resolves at runtime
    ChartHistoryRepository,
    ChartProposalRepository,
    PatientRepository,
    TherapySessionRepository,
)
from ..services import AuditService, NoteService, get_audit_service
from ..services.patient_intake_assignment_service import (  # noqa: TC001 — FastAPI resolves at runtime
    IntakeAssignmentService,
)
from ._deps import patient_not_found, visible_patient
from .notes import get_note_service
from .patient_intake_assignments import (
    assignment_response,
    get_clinician_intake_assignment_service,
    get_clinician_patient_repository,
)
from .patients import get_patient_repository
from .sessions import get_session_repository

if TYPE_CHECKING:
    from ..chart_proposals.models import ChartProposal
    from ..notes.chart_context import ChartContext

router = APIRouter(prefix="/api/patients", tags=["chart-proposals"])


@router.get("/{patient_id}/chart-proposals", response_model=PatientChartProposalsResponse)
def list_patient_chart_proposals(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    sessions: TherapySessionRepository = Depends(get_session_repository),
    notes: NoteService = Depends(get_note_service),
    assignments: IntakeAssignmentService = Depends(get_clinician_intake_assignment_service),
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
    wanted = {p.note_id for p in pending if p.note_id is not None} & set(by_note)
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
    intakes = _intake_groups(
        [p for p in pending if p.intake_assignment_id is not None],
        assignments,
        patient.id,
        user.id,
        chart,
    )

    audit.log_patient_action(
        AuditAction.PATIENT_VIEWED,
        user,
        http_request,
        patient,
        changes={"viewed": "chart_proposals"},
    )
    return PatientChartProposalsResponse(
        groups=groups,
        intakes=intakes,
        pending=sum(len(g.proposals) for g in groups) + sum(len(g.proposals) for g in intakes),
    )


def _intake_groups(
    pending: list[ChartProposal],
    assignments: IntakeAssignmentService,
    patient_id: str,
    user_id: str,
    chart: ChartContext,
) -> list[IntakeChartProposalGroup]:
    """The pending proposals of each accepted form on the chart, newest form first."""
    groups: list[IntakeChartProposalGroup] = []
    for assignment_id in dict.fromkeys(p.intake_assignment_id for p in pending):
        if assignment_id is None:
            continue
        assignment = assignments.get_for_clinician(assignment_id, user_id)
        if assignment is None or str(assignment["patient_id"]) != patient_id:
            continue
        handed_in = assignment.get("submitted_at") or assignment.get("accepted_at")
        groups.append(
            IntakeChartProposalGroup(
                assignment_id=assignment_id,
                packet_name=assignment_response(assignments, assignment, patient_id).packet_name,
                date=handed_in if isinstance(handed_in, datetime) else assignment["assigned_at"],  # type: ignore[arg-type]  # assigned_at is a datetime on every stored assignment
                proposals=[
                    proposal_response(p, chart)
                    for p in pending
                    if p.intake_assignment_id == assignment_id
                ],
            )
        )
    groups.sort(key=lambda g: g.date, reverse=True)
    return groups


@router.post(
    "/{patient_id}/intake-assignments/{assignment_id}/chart-proposals/{proposal_id}/decision",
    response_model=ChartProposalResponse,
)
def decide_intake_chart_proposal(
    patient_id: str,
    assignment_id: str,
    proposal_id: str,
    body: DecideProposalRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    assignments: IntakeAssignmentService = Depends(get_clinician_intake_assignment_service),
    history: ChartHistoryRepository = Depends(get_chart_history_repository),
    medications: MedicationRepository = Depends(get_proposal_medication_repository),
    proposals: ChartProposalRepository = Depends(get_chart_proposal_repository),
    step: ChartProposalStep = Depends(get_chart_proposal_step),
    audit: AuditService = Depends(get_audit_service),
) -> ChartProposalResponse:
    """Accept, edit or discard one update an accepted intake form proposed.

    The form must be on this patient's chart (a 404 otherwise, as for a
    patient the clinician cannot see). Accept and edit write the chart with
    the clinician as the writer and no note as the source; a medication is
    dated the day it was accepted. The audit names the field and the form,
    never the text.
    """
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    assignment = assignments.get_for_clinician(assignment_id, user.id)
    if assignment is None or str(assignment["patient_id"]) != patient.id:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})
    writers = ChartWriters(
        history=ChartHistoryService(history),
        patients=patients,
        medications=MedicationService(medications),
    )
    service = ChartProposalService(proposals, writers)
    try:
        decided = service.decide_for_intake(
            assignment_id, patient, proposal_id, Choice(body.decision, body.text), user.id
        )
    except ProposalNotFoundError as exc:
        raise NotFoundError("No such proposal on this form", {"proposal_id": proposal_id}) from exc
    except ProposalDecidedError as exc:
        raise ConflictError(
            "This proposal was already decided", {"proposal_id": proposal_id}
        ) from exc
    except ProposalNotEditableError as exc:
        raise ConflictError(
            "Accept or discard this change; edit the list on the client's page",
            {"proposal_id": proposal_id},
        ) from exc
    except ChartChangedError as exc:
        raise ConflictError(
            "The chart has changed since this was proposed", {"proposal_id": proposal_id}
        ) from exc
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc), {"proposal_id": proposal_id}) from exc
    change = {"chart_proposal": decided.decision, "field_key": decided.field_key}
    audit.log(
        action=AuditAction.INTAKE_CHART_UPDATE_DECIDED,
        user=user,
        request=http_request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes=change,
    )
    if decided.decision != "discarded":
        audit.log_patient_action(
            AuditAction.PATIENT_UPDATED,
            user,
            http_request,
            patient,
            changes={
                "changed_fields": [decided.field_key],
                **change,
                "intake_submission_id": assignment_id,
            },
        )
    return proposal_response(decided, step.chart(patient, user.id))
