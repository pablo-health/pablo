# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A recorded visit's times, and the psychotherapy time the clinician confirms."""

from fastapi import APIRouter, Depends, Request

from ..api_errors import NotFoundError
from ..auth.service import require_baa_acceptance
from ..models import User
from ..models.audit import AuditAction
from ..models.visit_times import ConfirmPsychotherapyWindowRequest, VisitTimesResponse
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
)
from ..repositories.session_dictation import SessionDictationRepository
from ..scheduling_engine.repositories.appointment import AppointmentRepository
from ..services import AuditService, NoteService, get_audit_service
from ..services.visit_times_service import build_visit_times, confirm_psychotherapy_window
from .notes import get_appointment_repository
from .session_dictations import get_dictation_repository
from .sessions import (
    get_note_service,
    get_notes_repository,
    get_patient_repository,
    get_session_repository,
)

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions/{session_id}/visit-times")
def get_visit_times(
    session_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    appointment_repo: AppointmentRepository = Depends(get_appointment_repository),
    dictation_repo: SessionDictationRepository = Depends(get_dictation_repository),
    audit: AuditService = Depends(get_audit_service),
) -> VisitTimesResponse:
    """Start, end and minutes of a recorded visit, and its psychotherapy time."""
    session = session_repo.get(session_id, user.id)
    if session is None:
        raise NotFoundError("Session not found", {"session_id": session_id})
    note = notes_repo.get_by_session_id(session.id, user.id)
    appointment = appointment_repo.get_by_session_ids([session.id], user.id).get(session.id)
    patient = patient_repo.get(session.patient_id, user.id)
    audit.log_session_action(AuditAction.SESSION_VIEWED, user, request, session, patient)
    return build_visit_times(
        session, note, appointment, dictation_repo.list_for_session(session.id)
    )


@router.put("/api/sessions/{session_id}/psychotherapy-window")
def put_psychotherapy_window(
    session_id: str,
    body: ConfirmPsychotherapyWindowRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    appointment_repo: AppointmentRepository = Depends(get_appointment_repository),
    note_service: NoteService = Depends(get_note_service),
    dictation_repo: SessionDictationRepository = Depends(get_dictation_repository),
    audit: AuditService = Depends(get_audit_service),
) -> VisitTimesResponse:
    """Confirm which turns were therapy, where it started, or its minutes."""
    session = session_repo.get(session_id, user.id)
    if session is None:
        raise NotFoundError("Session not found", {"session_id": session_id})
    note = confirm_psychotherapy_window(
        session, notes_repo.get_by_session_id(session.id, user.id), body, note_service, user.id
    )
    patient = patient_repo.get(session.patient_id, user.id)
    audit.log_session_action(
        AuditAction.SESSION_UPDATED,
        user,
        request,
        session,
        patient,
        changes={"psychotherapy_window": "confirmed"},
    )
    appointment = appointment_repo.get_by_session_ids([session.id], user.id).get(session.id)
    return build_visit_times(
        session, note, appointment, dictation_repo.list_for_session(session.id)
    )
