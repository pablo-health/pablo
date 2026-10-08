# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A note's chart proposals: what the visit would change on the chart.

* ``GET  /api/notes/{note_id}/chart-proposals`` — every proposal on the note,
  pending and decided, each with what the chart says now and the transcript
  lines it cites. Reading changes nothing: proposals are recomputed where the
  note's content changes (``app.chart_proposals.step``).
* ``POST /api/notes/{note_id}/chart-proposals/retry`` — run the proposal call
  again, for a note whose last call failed. Only a note drafted from a
  session's transcript can be: a dictated note's transcript is not kept.
* ``POST /api/notes/{note_id}/chart-proposals/{proposal_id}/decision`` —
  accept, edit or discard one. Accept and edit write the chart with this note
  as the source; the note is not changed, signed or not.

Every route loads the note, then its patient, which is the access check (an
inaccessible one is a 404). Whoever may open the note may decide its
proposals. Audits name the field key, never the text.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..api_errors import ConflictError, NotFoundError
from ..auth.service import require_baa_acceptance
from ..chart_history.dependencies import get_chart_history_repository
from ..chart_history.service import ChartHistoryService
from ..chart_proposals.dependencies import (
    get_chart_proposal_repository,
    get_chart_proposal_step,
)
from ..chart_proposals.families import ChartWriters
from ..chart_proposals.schemas import (
    ChartProposalResponse,
    ChartProposalsResponse,
    DecideProposalRequest,
    proposal_response,
    run_response,
)
from ..chart_proposals.service import (
    ChartProposalService,
    Choice,
    ProposalDecidedError,
    ProposalNotFoundError,
)
from ..chart_proposals.step import ChartProposalStep, proposes_chart_updates
from ..db import release_db_connection
from ..models import AuditAction, Note, Patient, User
from ..notes import (  # noqa: TC001 — FastAPI resolves at runtime
    NoteTypeDefinition,
    NoteTypeRegistry,
)
from ..notes.chart_context import ChartContext, chart_context_for
from ..repositories import (  # noqa: TC001 — FastAPI resolves at runtime
    ChartHistoryRepository,
    ChartProposalRepository,
    PatientRepository,
    TherapySessionRepository,
)
from ..repositories.session_dictation import (  # noqa: TC001 — FastAPI resolves at runtime
    SessionDictationRepository,
)
from ..services import AuditService, NoteService, get_audit_service
from ..services.note_generation_service import (  # noqa: TC001 — FastAPI resolves at runtime
    NoteGenerationService,
)
from ..services.note_redraft import as_shown, source_transcript
from ..services.note_service import NoteNotFoundError
from .notes import get_note_generation_service, get_note_service, get_registry
from .patients import get_patient_repository
from .session_dictations import get_dictation_repository
from .sessions import get_session_repository

router = APIRouter(prefix="/api/notes", tags=["chart-proposals"])


def _note_and_patient(
    notes: NoteService, patients: PatientRepository, note_id: str, user: User
) -> tuple[Note, Patient]:
    try:
        note = notes.get_note(note_id, user.id)
    except NoteNotFoundError as exc:
        raise NotFoundError("Note not found", {"note_id": note_id}) from exc
    patient = patients.get(note.patient_id, user.id)
    if patient is None:
        raise NotFoundError("Note not found", {"note_id": note_id})
    return note, patient


def _chart(patient: Patient, history: ChartHistoryRepository) -> ChartContext:
    return chart_context_for(patient, [], history=history.entries(patient.id))


def _listing(
    note: Note, chart: ChartContext, proposals: ChartProposalRepository
) -> ChartProposalsResponse:
    listed = ChartProposalService(proposals).proposals(note.id)
    return ChartProposalsResponse(
        data=[proposal_response(p, chart) for p in listed],
        run=run_response(proposals.run(note.id), note),
    )


@router.get("/{note_id}/chart-proposals", response_model=ChartProposalsResponse)
def list_chart_proposals(
    note_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    notes: NoteService = Depends(get_note_service),
    patients: PatientRepository = Depends(get_patient_repository),
    history: ChartHistoryRepository = Depends(get_chart_history_repository),
    proposals: ChartProposalRepository = Depends(get_chart_proposal_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ChartProposalsResponse:
    note, patient = _note_and_patient(notes, patients, note_id, user)
    chart = _chart(patient, history)
    response = _listing(note, chart, proposals)
    audit.log_note_action(
        action=AuditAction.SESSION_VIEWED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes={"viewed": "chart_proposals"},
    )
    return response


@router.post(
    "/{note_id}/chart-proposals/{proposal_id}/decision", response_model=ChartProposalResponse
)
def decide_chart_proposal(
    note_id: str,
    proposal_id: str,
    body: DecideProposalRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    notes: NoteService = Depends(get_note_service),
    patients: PatientRepository = Depends(get_patient_repository),
    history: ChartHistoryRepository = Depends(get_chart_history_repository),
    proposals: ChartProposalRepository = Depends(get_chart_proposal_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ChartProposalResponse:
    note, patient = _note_and_patient(notes, patients, note_id, user)
    service = ChartProposalService(
        proposals, ChartWriters(history=ChartHistoryService(history), patients=patients)
    )
    try:
        decided = service.decide(
            note, patient, proposal_id, Choice(body.decision, body.text), user.id
        )
    except ProposalNotFoundError as exc:
        raise NotFoundError("No such proposal on this note", {"proposal_id": proposal_id}) from exc
    except ProposalDecidedError as exc:
        raise ConflictError(
            "This proposal was already decided", {"proposal_id": proposal_id}
        ) from exc
    change = {"chart_proposal": decided.decision, "field_key": decided.field_key}
    audit.log_note_action(
        action=AuditAction.SESSION_UPDATED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes=change,
    )
    if decided.decision != "discarded":
        audit.log_patient_action(
            AuditAction.PATIENT_UPDATED,
            user,
            http_request,
            patient,
            changes={"changed_fields": [decided.field_key], **change},
        )
    return proposal_response(decided, _chart(patient, history))


@router.post("/{note_id}/chart-proposals/retry", response_model=ChartProposalsResponse)
def retry_chart_proposals(
    note_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    notes: NoteService = Depends(get_note_service),
    patients: PatientRepository = Depends(get_patient_repository),
    sessions: TherapySessionRepository = Depends(get_session_repository),
    dictations: SessionDictationRepository = Depends(get_dictation_repository),
    proposals: ChartProposalRepository = Depends(get_chart_proposal_repository),
    step: ChartProposalStep = Depends(get_chart_proposal_step),
    registry: NoteTypeRegistry = Depends(get_registry),
    generator: NoteGenerationService = Depends(get_note_generation_service),
    audit: AuditService = Depends(get_audit_service),
) -> ChartProposalsResponse:
    note, patient = _note_and_patient(notes, patients, note_id, user)
    try:
        definition: NoteTypeDefinition | None = registry.get(note.note_type, note.note_type_version)
    except KeyError:
        definition = None
    session = sessions.get(note.session_id, user.id) if note.session_id else None
    if session is None or not proposes_chart_updates(definition):
        raise ConflictError("This note can't be checked again", {"note_id": note_id})
    transcript = source_transcript(session, dictations)
    chart = step.chart(patient)
    shown = as_shown(note.note_type, note.content, note.content_edited)
    # Nothing is held open across the model call, as in the draft worker.
    release_db_connection()
    drafted = step.draft(generator, definition, chart, transcript, shown)
    step.store(note, definition, chart, drafted, shown)
    response = _listing(note, chart, proposals)
    audit.log_note_action(
        action=AuditAction.SESSION_UPDATED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes={
            "chart_proposals": "retried",
            "status": response.run.status if response.run else None,
        },
    )
    return response
