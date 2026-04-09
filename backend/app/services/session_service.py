# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session business logic service.

Encapsulates multi-step session operations: upload with SOAP generation,
finalization with export queuing, and rating updates.
"""

import logging
import uuid
from datetime import UTC, datetime

from ..models import (
    FinalizeSessionRequest,
    Patient,
    ScheduleSessionRequest,
    SessionStatus,
    SOAPNote,
    TherapySession,
    Transcript,
    UpdateSessionMetadataRequest,
    UpdateSessionRatingRequest,
    UpdateSessionStatusRequest,
    UploadSessionRequest,
    UploadTranscriptToSessionRequest,
)
from ..repositories import PatientRepository, TherapySessionRepository
from .soap_generation_service import SOAPGenerationService

logger = logging.getLogger(__name__)

class SessionServiceError(Exception):
    """Base exception for session service errors."""

class PatientNotFoundError(SessionServiceError):
    """Raised when a patient is not found."""

class SessionNotFoundError(SessionServiceError):
    """Raised when a session is not found."""

class InvalidSessionStatusError(SessionServiceError):
    """Raised when a session is in the wrong status for an operation."""

    def __init__(self, current_status: str, expected: str) -> None:
        self.current_status = current_status
        super().__init__(f"Expected status '{expected}', got '{current_status}'")

class SOAPGenerationFailedError(SessionServiceError):
    """Raised when SOAP generation fails."""

class InvalidStatusTransitionError(SessionServiceError):
    """Raised when a session status transition is not allowed."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from '{current}' to '{target}'")

class SessionAlreadyInStatusError(SessionServiceError):
    """Raised when a session is already in the target status (409)."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Session is already in status '{status}'")

class SessionInTerminalStatusError(SessionServiceError):
    """Raised when trying to modify a session in a terminal status."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Cannot modify session in terminal status '{status}'")

# Valid status transitions (state machine)
VALID_TRANSITIONS: dict[str, set[str]] = {
    SessionStatus.SCHEDULED: {SessionStatus.IN_PROGRESS, SessionStatus.CANCELLED},
    SessionStatus.IN_PROGRESS: {SessionStatus.RECORDING_COMPLETE, SessionStatus.CANCELLED},
    SessionStatus.RECORDING_COMPLETE: {
        SessionStatus.QUEUED,
        SessionStatus.TRANSCRIBING,
        SessionStatus.CANCELLED,
    },
    SessionStatus.TRANSCRIBING: {SessionStatus.QUEUED, SessionStatus.FAILED},
    SessionStatus.QUEUED: {SessionStatus.PROCESSING},
    SessionStatus.PROCESSING: {SessionStatus.PENDING_REVIEW, SessionStatus.FAILED},
    SessionStatus.PENDING_REVIEW: {SessionStatus.FINALIZED},
}

TERMINAL_STATUSES = {SessionStatus.FINALIZED, SessionStatus.CANCELLED, SessionStatus.FAILED}

def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

class SessionService:
    """Orchestrates multi-step session operations."""

    def __init__(
        self,
        session_repo: TherapySessionRepository,
        patient_repo: PatientRepository,
        soap_service: SOAPGenerationService,
    ) -> None:
        self.session_repo = session_repo
        self.patient_repo = patient_repo
        self.soap_service = soap_service

    def _update_next_session_date(self, patient: Patient, user_id: str) -> None:
        """Recompute and persist next_session_date from scheduled sessions."""
        sessions = self.session_repo.list_by_patient(patient.id, user_id)
        now = datetime.now(UTC).isoformat()
        future = [
            s.scheduled_at or s.session_date
            for s in sessions
            if (s.scheduled_at or s.session_date) > now and s.status not in TERMINAL_STATUSES
        ]
        patient.next_session_date = min(future) if future else None
        self.patient_repo.update(patient)

    def upload_session(
        self,
        patient_id: str,
        user_id: str,
        request: UploadSessionRequest,
    ) -> tuple[TherapySession, Patient]:
        """Create a session, generate SOAP note, and update patient metadata.

        Returns the completed session and patient.

        Raises:
            PatientNotFoundError: If patient doesn't exist or doesn't belong to user.
            SOAPGenerationFailedError: If SOAP note generation fails.
        """
        patient = self.patient_repo.get(patient_id, user_id)
        if not patient:
            raise PatientNotFoundError(f"Patient {patient_id} not found")

        now = _now()

        session_number = self.session_repo.get_session_number_for_patient(patient_id)

        session = TherapySession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=patient_id,
            session_date=request.session_date,
            session_number=session_number,
            status=SessionStatus.QUEUED,
            transcript=Transcript(
                format=request.transcript.format,
                content=request.transcript.content,
            ),
            created_at=now,
        )
        session = self.session_repo.create(session)

        # Transition to processing
        session.status = SessionStatus.PROCESSING
        session.processing_started_at = _now()
        session = self.session_repo.update(session)

        try:
            logger.info("Starting SOAP generation for session %s", session.id)

            soap_note = self.soap_service.generate_soap_note(
                session.transcript, patient, request.session_date
            )

            logger.info("SOAP generation completed for session %s", session.id)

            session.soap_note = soap_note
            session.status = SessionStatus.PENDING_REVIEW
            session.processing_completed_at = _now()
            session = self.session_repo.update(session)

        except Exception as e:
            logger.exception("SOAP generation failed for session %s", session.id)
            session.status = SessionStatus.FAILED
            session.error = "SOAP generation failed"
            self.session_repo.update(session)
            raise SOAPGenerationFailedError from e

        # Update patient metadata
        patient.session_count += 1
        if patient.last_session_date is None or request.session_date > patient.last_session_date:
            patient.last_session_date = request.session_date
        self.patient_repo.update(patient)

        return session, patient

    def finalize_session(
        self,
        session_id: str,
        user_id: str,
        request: FinalizeSessionRequest,
    ) -> tuple[TherapySession, Patient]:
        """Finalize a session after therapist review.

        Validates status, applies edits, queues for export if needed.
        Returns the finalized session and patient.

        Raises:
            SessionNotFoundError: If session doesn't exist or doesn't belong to user.
            InvalidSessionStatusError: If session is not in pending_review status.
            RatingFeedbackRequiredError: If low rating lacks feedback.
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.status != SessionStatus.PENDING_REVIEW:
            raise InvalidSessionStatusError(session.status, "pending_review")

        session.status = SessionStatus.FINALIZED
        session.quality_rating = request.quality_rating
        session.quality_rating_reason = request.quality_rating_reason
        session.quality_rating_sections = (
            [s.value for s in request.quality_rating_sections]
            if request.quality_rating_sections
            else None
        )
        session.finalized_at = _now()

        if request.soap_note_edited:
            session.soap_note_edited = SOAPNote.from_dict(
                {
                    "subjective": request.soap_note_edited.subjective,
                    "objective": request.soap_note_edited.objective,
                    "assessment": request.soap_note_edited.assessment,
                    "plan": request.soap_note_edited.plan,
                }
            )

        session = self.session_repo.update(session)

        patient = self.patient_repo.get(session.patient_id, user_id)

        return session, patient  # type: ignore[return-value]

    def update_rating(
        self,
        session_id: str,
        user_id: str,
        request: UpdateSessionRatingRequest,
    ) -> tuple[TherapySession, Patient, int | None]:
        """Update quality rating for a finalized session.

        Returns the updated session, patient, and old rating value.

        Raises:
            SessionNotFoundError: If session doesn't exist or doesn't belong to user.
            InvalidSessionStatusError: If session is not finalized.
            RatingFeedbackRequiredError: If low rating lacks feedback.
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.status != SessionStatus.FINALIZED:
            raise InvalidSessionStatusError(session.status, "finalized")

        old_rating = session.quality_rating

        session.quality_rating = request.quality_rating
        session.quality_rating_reason = request.quality_rating_reason
        session.quality_rating_sections = (
            [s.value for s in request.quality_rating_sections]
            if request.quality_rating_sections
            else None
        )
        session = self.session_repo.update(session)

        patient = self.patient_repo.get(session.patient_id, user_id)

        return session, patient, old_rating  # type: ignore[return-value]

    def schedule_session(
        self,
        user_id: str,
        request: ScheduleSessionRequest,
    ) -> tuple[TherapySession, Patient]:
        """Create a scheduled session (pre-recording).

        Returns the created session and patient.

        Raises:
            PatientNotFoundError: If patient doesn't exist or doesn't belong to user.
        """
        patient = self.patient_repo.get(request.patient_id, user_id)
        if not patient:
            raise PatientNotFoundError(f"Patient {request.patient_id} not found")

        now = _now()
        session_number = self.session_repo.get_session_number_for_patient(request.patient_id)

        session = TherapySession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=request.patient_id,
            session_date=request.scheduled_at,
            session_number=session_number,
            status=SessionStatus.SCHEDULED,
            transcript=Transcript(format="txt", content=""),
            created_at=now,
            scheduled_at=request.scheduled_at,
            video_link=request.video_link,
            video_platform=request.video_platform.value if request.video_platform else None,
            session_type=request.session_type.value,
            duration_minutes=request.duration_minutes,
            source=request.source.value,
            notes=request.notes,
            updated_at=now,
        )
        session = self.session_repo.create(session)
        self._update_next_session_date(patient, user_id)
        return session, patient

    def transition_status(
        self,
        session_id: str,
        user_id: str,
        request: UpdateSessionStatusRequest,
    ) -> tuple[TherapySession, Patient]:
        """Transition session status with state machine validation.

        Returns the updated session and patient.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            SessionAlreadyInStatusError: If session is already in target status (409).
            InvalidStatusTransitionError: If transition is not allowed (400).
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        target = request.status.value
        current = session.status

        if current == target:
            raise SessionAlreadyInStatusError(current)

        allowed = VALID_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidStatusTransitionError(current, target)

        now = _now()

        # Apply side effects
        if target == SessionStatus.IN_PROGRESS:
            session.started_at = now
        elif target == SessionStatus.RECORDING_COMPLETE or (
            target == SessionStatus.CANCELLED and session.started_at
        ):
            session.ended_at = now

        session.status = target
        session.updated_at = now
        session = self.session_repo.update(session)

        patient = self.patient_repo.get(session.patient_id, user_id)
        if patient and target in {SessionStatus.CANCELLED, SessionStatus.IN_PROGRESS}:
            self._update_next_session_date(patient, user_id)
        return session, patient  # type: ignore[return-value]

    def update_session_metadata(
        self,
        session_id: str,
        user_id: str,
        request: UpdateSessionMetadataRequest,
    ) -> tuple[TherapySession, Patient]:
        """Update session metadata (reschedule, change video link, etc.).

        Returns the updated session and patient.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            SessionInTerminalStatusError: If session is in a terminal status.
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.status in TERMINAL_STATUSES:
            raise SessionInTerminalStatusError(session.status)

        if request.scheduled_at is not None:
            session.scheduled_at = request.scheduled_at
            session.session_date = request.scheduled_at
        if request.video_link is not None:
            session.video_link = request.video_link
        if request.video_platform is not None:
            session.video_platform = request.video_platform.value
        if request.duration_minutes is not None:
            session.duration_minutes = request.duration_minutes
        if request.notes is not None:
            session.notes = request.notes

        session.updated_at = _now()
        session = self.session_repo.update(session)

        patient = self.patient_repo.get(session.patient_id, user_id)
        return session, patient  # type: ignore[return-value]

    def upload_transcript_to_session(
        self,
        session_id: str,
        user_id: str,
        request: UploadTranscriptToSessionRequest,
    ) -> TherapySession:
        """Upload a transcript to an existing session and trigger SOAP pipeline.

        Returns the updated session.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            InvalidSessionStatusError: If session is not in recording_complete status.
            SOAPGenerationFailedError: If SOAP generation fails.
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.status not in (SessionStatus.RECORDING_COMPLETE, SessionStatus.FAILED):
            raise InvalidSessionStatusError(session.status, "recording_complete or failed")

        # Store the transcript
        session.transcript = Transcript(format=request.format, content=request.content)

        # Transition to queued → processing → pending_review (same as upload_session)
        session.status = SessionStatus.QUEUED
        session.updated_at = _now()
        session = self.session_repo.update(session)

        session.status = SessionStatus.PROCESSING
        session.processing_started_at = _now()
        session = self.session_repo.update(session)

        patient = self.patient_repo.get(session.patient_id, user_id)
        if not patient:
            raise PatientNotFoundError(f"Patient {session.patient_id} not found")

        try:
            logger.info("Starting SOAP generation for session %s", session.id)

            soap_note = self.soap_service.generate_soap_note(
                session.transcript, patient, session.session_date
            )

            logger.info("SOAP generation completed for session %s", session.id)

            session.soap_note = soap_note
            session.status = SessionStatus.PENDING_REVIEW
            session.processing_completed_at = _now()
            session = self.session_repo.update(session)

        except Exception as e:
            logger.exception("SOAP generation failed for session %s", session.id)
            session.status = SessionStatus.FAILED
            session.error = "SOAP generation failed"
            self.session_repo.update(session)
            raise SOAPGenerationFailedError from e

        return session
