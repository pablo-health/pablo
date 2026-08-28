# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session business logic service.

Encapsulates multi-step session operations: upload with note generation,
finalization, scheduling, and status transitions. Note-flavored business
logic (edit, finalize-with-quality-rating) lives on
:class:`app.services.note_service.NoteService`; this service delegates
through to it for session-scoped endpoints.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from ..api_errors import APIError, BadRequestError, ConflictError, NotFoundError, ServerError
from ..models import (
    FinalizeSessionRequest,
    Note,
    Patient,
    ScheduleSessionRequest,
    SessionSource,
    SessionStatus,
    TherapySession,
    Transcript,
    TranscriptFormat,
    UpdateSessionMetadataRequest,
    UpdateSessionRatingRequest,
    UpdateSessionStatusRequest,
    UploadSessionRequest,
    UploadTranscriptToSessionRequest,
)
from ..notes import get_default_registry
from ..repositories import PatientRepository, TherapySessionRepository
from ..utcnow import utc_now
from .note_generation_service import (
    NoteGenerationService,
    TransientNoteGenerationError,
)
from .note_service import (
    NoteNotFinalizedError,
    NoteNotFoundError,
    NoteService,
)

DEFAULT_NOTE_TYPE = "soap"


class InvalidNoteTypeError(BadRequestError):
    """Raised when a request specifies a note_type not in the registry."""

    code = "INVALID_NOTE_TYPE"


logger = logging.getLogger(__name__)


class SessionServiceError(APIError):
    """Base exception for session service errors."""


class PatientNotFoundError(NotFoundError):
    """Raised when a patient is not found."""


class SessionNotFoundError(NotFoundError):
    """Raised when a session is not found."""


class InvalidSessionStatusError(BadRequestError):
    """Raised when a session is in the wrong status for an operation."""

    code = "INVALID_SESSION_STATUS"

    def __init__(self, current_status: str, expected: str) -> None:
        self.current_status = current_status
        super().__init__(
            f"Expected status '{expected}', got '{current_status}'",
            {"current_status": current_status, "expected": expected},
        )


class SOAPGenerationFailedError(ServerError):
    """Raised when SOAP generation fails deterministically (won't succeed on retry)."""

    code = "SOAP_GENERATION_FAILED"
    default_message = "Failed to generate SOAP note. Please try again."


class TransientSOAPGenerationError(ServerError):
    """Raised when SOAP generation fails for a transient, retryable reason.

    The session is deliberately left in its pre-generation status (not marked
    ``failed``) so the job can be retried and complete from the same
    already-persisted transcript.
    """

    code = "SOAP_GENERATION_TRANSIENT"
    default_message = "Note generation is temporarily unavailable; retrying."


class InvalidStatusTransitionError(BadRequestError):
    """Raised when a session status transition is not allowed."""

    code = "INVALID_STATUS_TRANSITION"

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition from '{current}' to '{target}'",
            {"current": current, "target": target},
        )


class SessionAlreadyInStatusError(ConflictError):
    """Raised when a session is already in the target status (409)."""

    code = "SESSION_ALREADY_IN_STATUS"

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"Session is already in status '{status}'",
            {"status": status},
        )


class SessionInTerminalStatusError(ConflictError):
    """Raised when trying to modify a session in a terminal status."""

    code = "SESSION_IN_TERMINAL_STATUS"

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"Cannot modify session in terminal status '{status}'",
            {"status": status},
        )


# Valid status transitions (state machine)
VALID_TRANSITIONS: dict[str, set[str]] = {
    SessionStatus.SCHEDULED: {SessionStatus.IN_PROGRESS, SessionStatus.CANCELLED},
    SessionStatus.IN_PROGRESS: {
        SessionStatus.RECORDING_COMPLETE,
        SessionStatus.CANCELLED,
        SessionStatus.SCHEDULED,
    },
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


def _now() -> datetime:
    return utc_now()


def _commit_intermediate(user_id: str) -> None:
    """Commit the request-scoped DB transaction mid-flight.

    Used at lock-release seams in long request flows (notably either side
    of the multi-second LLM call in ``upload_session``) so row locks held
    since request entry release before the slow external call begins.

    ``user_id`` is accepted but not used directly: the ``after_begin``
    Session listener in ``app.db`` re-arms ``app.current_user_id`` on
    every fresh transaction from the user id armed on ``Session.info`` at
    auth time (``arm_current_user_id``). Using ``Session.info`` rather
    than a ContextVar is what makes this work on sync routes, whose
    dependency and endpoint run in separate threadpool workers that don't
    share ContextVar mutations. The argument is kept on the helper
    signature so call sites read as "commit the locks held for this
    user" -- self-documenting at the seam, and gives us a hook point if
    we ever need per-call diagnostics.

    No-ops when no request-scoped session is in context (unit tests with
    in-memory fakes, CLI scripts that never installed the middleware) --
    there's no transaction to commit there.
    """
    del user_id  # listener-driven re-arm; see app.db._rearm_rls_principal_gucs_on_txn_begin
    from ..db import release_db_connection

    release_db_connection()


class SessionService:
    """Orchestrates multi-step session operations."""

    def __init__(
        self,
        session_repo: TherapySessionRepository,
        patient_repo: PatientRepository,
        note_generation_service: NoteGenerationService,
        note_service: NoteService,
    ) -> None:
        self.session_repo = session_repo
        self.patient_repo = patient_repo
        self.note_generation_service = note_generation_service
        self.note_service = note_service

    def _get_patient_or_raise(self, patient_id: str, user_id: str) -> Patient:
        patient = self.patient_repo.get(patient_id, user_id)
        if patient is None:
            raise PatientNotFoundError(f"Patient {patient_id} not found")
        return patient

    def _update_next_session_date(self, patient: Patient, user_id: str) -> None:
        """Recompute and persist next_session_date from scheduled sessions."""
        patient.next_session_date = self.session_repo.get_next_session_date(
            patient.id,
            user_id,
            after=datetime.now(UTC),
            exclude_statuses=TERMINAL_STATUSES,
        )
        self.patient_repo.update(patient)

    # --- Generation pipeline ---

    def _generate_and_persist_note(
        self,
        session: TherapySession,
        patient: Patient,
        note_type: str,
        user_id: str,
    ) -> Note:
        result = self.note_generation_service.generate_note(
            note_type, session.transcript, patient, session.session_date
        )
        return self.note_service.create_or_update_for_session(
            session_id=session.id,
            patient_id=session.patient_id,
            note_type=result.note_type,
            content=result.content,
            user_id=user_id,
        )

    def create_session_for_generation(
        self,
        patient_id: str,
        user_id: str,
        request: UploadSessionRequest,
    ) -> tuple[TherapySession, Patient]:
        """Persist a ``PROCESSING`` session and commit, without generating.

        This is the request-thread half of the async upload: it does the cheap,
        bounded DB work (validate the patient, insert the session, bump patient
        metadata, commit) so the route can return ``202`` immediately and hand
        the multi-second LLM generation to a Cloud Tasks worker
        (``generate_session_note``). The commit also releases the pooled
        connection before the worker runs — nothing holds locks across the
        Gemini call (THERAPY-da7t).

        The session is counted here, at creation, rather than after generation:
        a session row exists regardless of whether its note generates, and
        counting once at creation is idempotent — a generation retry can't
        double-count it.

        Returns ``(session, patient)``.

        Raises:
            PatientNotFoundError: If patient doesn't exist or doesn't belong to user.
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
            status=SessionStatus.PROCESSING,
            transcript=Transcript(
                format=request.transcript.format,
                content=request.transcript.content,
            ),
            created_at=now,
            processing_started_at=now,
        )
        session = self.session_repo.create(session)

        patient.session_count += 1
        if patient.last_session_date is None or request.session_date > patient.last_session_date:
            patient.last_session_date = request.session_date
        self.patient_repo.update(patient)

        _commit_intermediate(user_id)
        return session, patient

    def generate_session_note(
        self,
        session_id: str,
        user_id: str,
        *,
        transient_is_terminal: bool = False,
    ) -> tuple[TherapySession, Patient, Note]:
        """Generate the SOAP note for an already-persisted ``PROCESSING`` session.

        The worker half shared by both async upload paths — a fresh upload
        (``create_session_for_generation``) and a transcript added to an
        existing session (``prepare_transcript_session_for_generation``). Loads
        the session, runs the LLM with no open DB transaction, then flips the
        session to ``PENDING_REVIEW``. The note type is taken from any
        pre-existing Note row (set at schedule time) or defaults. On failure the
        session is marked ``FAILED`` and committed before re-raising, so a failed
        generation leaves a durable record rather than vanishing on rollback.
        It does not touch patient metadata (the session is counted at creation),
        so retrying the job while ``PROCESSING`` is safe and never double-counts.

        Returns ``(session, patient, note)``.

        Raises:
            SessionNotFoundError: If the session no longer exists / isn't visible.
            PatientNotFoundError: If the session's patient is gone.
            SOAPGenerationFailedError: If note generation fails.
        """
        session = self.session_repo.get(session_id, user_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        patient = self.patient_repo.get(session.patient_id, user_id)
        if not patient:
            raise PatientNotFoundError(f"Patient {session.patient_id} not found")

        # Pick the note type from any pre-existing Note row (e.g. set at
        # schedule time, or by a transcript upload onto an existing session).
        # Fall back to the default for a freshly-created upload session.
        existing_note = self.note_service.get_note_by_session_id(session.id, user_id)
        note_type = existing_note.note_type if existing_note is not None else DEFAULT_NOTE_TYPE

        # Release the pooled connection before the multi-second model call. The
        # SELECTs above (session, patient, existing note) opened a read
        # transaction; holding it across generation leaves the connection
        # idle-in-transaction long enough for the server/proxy to drop it, and
        # the note INSERT afterward then fails with OperationalError ->
        # PendingRollbackError, so the worker keeps retrying. Commit here (the
        # either-side-of-the-LLM pattern upload_session uses) so generation runs
        # with nothing checked out. expire_on_commit=False keeps session/patient
        # usable, transcript is a loaded JSONB column (no lazy reload), and the
        # note INSERT autobegins a fresh, pre-pinged connection that the
        # checkout / after_begin listeners re-arm with search_path + the RLS GUC.
        _commit_intermediate(user_id)

        try:
            logger.info("Starting note generation for session %s", session.id)
            note = self._generate_and_persist_note(session, patient, note_type, user_id)
            logger.info("Note generation completed for session %s", session.id)

            session.status = SessionStatus.PENDING_REVIEW
            session.processing_completed_at = _now()
            session = self.session_repo.update(session)

        except TransientNoteGenerationError as e:
            # A retryable provider failure (e.g. 429). Unless this is the final
            # attempt, leave the session in its pre-generation status so a retry
            # can complete from the same persisted transcript, and signal the
            # caller to ask the queue to retry rather than marking it failed.
            if not transient_is_terminal:
                logger.warning(
                    "Note generation transiently failed for session %s; leaving for retry",
                    session.id,
                )
                _commit_intermediate(user_id)
                raise TransientSOAPGenerationError from e
            logger.warning(
                "Note generation transiently failed for session %s on the final "
                "attempt; marking failed",
                session.id,
            )
            session.status = SessionStatus.FAILED
            session.error = "SOAP generation failed (temporarily unavailable; retries exhausted)"
            self.session_repo.update(session)
            _commit_intermediate(user_id)
            raise SOAPGenerationFailedError from e

        except Exception as e:
            logger.exception("Note generation failed for session %s", session.id)
            session.status = SessionStatus.FAILED
            session.error = "SOAP generation failed"
            self.session_repo.update(session)
            # Persist FAILED status before the middleware-level rollback
            # discards it -- the prior behavior left no DB audit trail
            # of failed uploads.
            _commit_intermediate(user_id)
            raise SOAPGenerationFailedError from e

        # The session was already counted at creation
        # (create_session_for_generation / the recording flow), so generation
        # only flips status — no patient-metadata change here, which keeps a
        # generation retry from double-counting.
        return session, patient, note

    def upload_session(
        self,
        patient_id: str,
        user_id: str,
        request: UploadSessionRequest,
    ) -> tuple[TherapySession, Patient, Note]:
        """Synchronous create-then-generate, kept for direct/non-async callers.

        The HTTP upload route runs the two halves separately (202 + Cloud Tasks
        worker); this convenience method chains them for tests and any caller
        that wants the note inline.

        Returns ``(session, patient, note)``.
        """
        session, _patient = self.create_session_for_generation(patient_id, user_id, request)
        return self.generate_session_note(session.id, user_id)

    def import_session(
        self,
        patient_id: str,
        user_id: str,
        *,
        session_date: datetime,
        source_text: str,
        note_content: dict[str, Any],
        note_type: str = DEFAULT_NOTE_TYPE,
    ) -> tuple[TherapySession, Patient, Note]:
        """Create a session from an already-written note imported as a file.

        Unlike :meth:`upload_session`, the note content is supplied by the
        caller — parsed from an uploaded SOAP document — rather than
        generated from a transcript, so there is no LLM call here. The
        original document text is stored as the session transcript so it can
        be shown beside the parsed note during review. The session lands in
        ``PENDING_REVIEW``, exactly like a generated note.

        Returns ``(session, patient, note)``.

        Raises:
            PatientNotFoundError: If patient doesn't exist or doesn't belong
                to the user.
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
            session_date=session_date,
            session_number=session_number,
            status=SessionStatus.PENDING_REVIEW,
            transcript=Transcript(format=TranscriptFormat.TXT, content=source_text),
            source=SessionSource.IMPORTED.value,
            created_at=now,
            processing_started_at=now,
            processing_completed_at=now,
        )
        session = self.session_repo.create(session)

        note = self.note_service.create_or_update_for_session(
            session_id=session.id,
            patient_id=session.patient_id,
            note_type=note_type,
            content=note_content,
            user_id=user_id,
        )

        patient.session_count += 1
        if patient.last_session_date is None or session_date > patient.last_session_date:
            patient.last_session_date = session_date
        self.patient_repo.update(patient)

        return session, patient, note

    def finalize_session(
        self,
        session_id: str,
        user_id: str,
        request: FinalizeSessionRequest,
    ) -> tuple[TherapySession, Patient, Note]:
        """Finalize a session after therapist review.

        Validates session status, applies note edits, finalizes the note
        with quality rating, and transitions session to FINALIZED. Returns
        the finalized session, patient, and updated note.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            InvalidSessionStatusError: If session is not in pending_review status.
            NoteNotFoundError: If the session has no note (generation never ran).
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.status != SessionStatus.PENDING_REVIEW:
            raise InvalidSessionStatusError(session.status, "pending_review")

        note = self.note_service.get_note_by_session_id(session_id, user_id)
        if note is None:
            raise NoteNotFoundError(f"Session {session_id} has no note to finalize")

        if request.soap_note_edited:
            note = self.note_service.update_note_edits(
                note.id,
                content_edited={
                    "subjective": request.soap_note_edited.subjective,
                    "objective": request.soap_note_edited.objective,
                    "assessment": request.soap_note_edited.assessment,
                    "plan": request.soap_note_edited.plan,
                },
                user_id=user_id,
            )

        finalized_at = _now()
        note = self.note_service.finalize_note(
            note.id,
            quality_rating=request.quality_rating,
            quality_rating_reason=request.quality_rating_reason,
            quality_rating_sections=(
                [s.value for s in request.quality_rating_sections]
                if request.quality_rating_sections
                else None
            ),
            finalized_at=finalized_at,
            user_id=user_id,
        )

        session.status = SessionStatus.FINALIZED
        session = self.session_repo.update(session)

        patient = self._get_patient_or_raise(session.patient_id, user_id)
        return session, patient, note

    def update_rating(
        self,
        session_id: str,
        user_id: str,
        request: UpdateSessionRatingRequest,
    ) -> tuple[TherapySession, Patient, Note, int | None]:
        """Update quality rating on a finalized session's note.

        Returns ``(session, patient, note, old_rating)``.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            InvalidSessionStatusError: If session is not finalized.
            NoteNotFoundError: If the session has no note.
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.status != SessionStatus.FINALIZED:
            raise InvalidSessionStatusError(session.status, "finalized")

        note = self.note_service.get_note_by_session_id(session_id, user_id)
        if note is None:
            raise NoteNotFoundError(f"Session {session_id} has no note")

        try:
            note, old_rating = self.note_service.update_quality_rating(
                note.id,
                quality_rating=request.quality_rating,
                quality_rating_reason=request.quality_rating_reason,
                quality_rating_sections=(
                    [s.value for s in request.quality_rating_sections]
                    if request.quality_rating_sections
                    else None
                ),
                user_id=user_id,
            )
        except NoteNotFinalizedError as exc:
            # Defensive: should not happen given the session.status guard above.
            raise InvalidSessionStatusError(session.status, "finalized") from exc

        patient = self._get_patient_or_raise(session.patient_id, user_id)
        return session, patient, note, old_rating

    def update_soap_note_edits(
        self,
        session_id: str,
        user_id: str,
        content_edited: dict[str, Any],
    ) -> Note:
        """Persist clinician edits via the session-scoped path.

        Thin wrapper around :meth:`NoteService.update_note_edits` so
        callers that only know the ``session_id`` (legacy frontend) can
        edit through the session route.
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        note = self.note_service.get_note_by_session_id(session_id, user_id)
        if note is None:
            raise NoteNotFoundError(f"Session {session_id} has no note")
        return self.note_service.update_note_edits(note.id, content_edited, user_id)

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

        note_type = request.note_type or DEFAULT_NOTE_TYPE
        if not get_default_registry().has(note_type):
            raise InvalidNoteTypeError(f"Unknown note_type: {note_type!r}")

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

        # Pre-create an empty Note row so the requested ``note_type`` is
        # remembered until a transcript is uploaded and content generated.
        # Body fields (content, finalized_at, quality_*) stay NULL until
        # the generation pipeline runs.
        self.note_service.create_or_update_for_session(
            session_id=session.id,
            patient_id=session.patient_id,
            note_type=note_type,
            content=None,
            user_id=user_id,
        )

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
        elif target == SessionStatus.SCHEDULED:
            session.started_at = None
            session.ended_at = None

        session.status = target
        session.updated_at = now
        session = self.session_repo.update(session)

        patient = self._get_patient_or_raise(session.patient_id, user_id)
        if target in {SessionStatus.CANCELLED, SessionStatus.IN_PROGRESS, SessionStatus.SCHEDULED}:
            self._update_next_session_date(patient, user_id)
        return session, patient

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
        if request.session_date is not None:
            session.session_date = request.session_date
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

        patient = self._get_patient_or_raise(session.patient_id, user_id)
        return session, patient

    def prepare_transcript_session_for_generation(
        self,
        session_id: str,
        user_id: str,
        request: UploadTranscriptToSessionRequest,
    ) -> TherapySession:
        """Attach a transcript to an existing session and mark it ``PROCESSING``.

        The request-thread half of the async transcript upload: validate the
        session, persist the transcript + ``PROCESSING`` status, and commit so
        row locks release before the LLM call (THERAPY-da7t). Generation is
        handed to the worker (``generate_session_note``), which reads the
        transcript and note type off the persisted session. The session is not
        re-counted here — it was counted when it was first created.

        Returns the updated ``session``.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            InvalidSessionStatusError: If session is not in recording_complete/failed.
        """
        session = self.session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.status not in (SessionStatus.RECORDING_COMPLETE, SessionStatus.FAILED):
            raise InvalidSessionStatusError(session.status, "recording_complete or failed")

        session.transcript = Transcript(format=request.format, content=request.content)
        session.status = SessionStatus.PROCESSING
        session.processing_started_at = _now()
        session.updated_at = _now()
        session = self.session_repo.update(session)
        _commit_intermediate(user_id)
        return session

    def upload_transcript_to_session(
        self,
        session_id: str,
        user_id: str,
        request: UploadTranscriptToSessionRequest,
    ) -> tuple[TherapySession, Note]:
        """Synchronous prepare-then-generate, kept for direct/non-async callers.

        The HTTP route runs the two halves separately (``202`` + Cloud Tasks
        worker); this convenience method chains them for tests and any caller
        that wants the note inline.

        Returns ``(session, note)``.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            InvalidSessionStatusError: If session is not in recording_complete status.
            SOAPGenerationFailedError: If note generation fails.
        """
        self.prepare_transcript_session_for_generation(session_id, user_id, request)
        session, _patient, note = self.generate_session_note(session_id, user_id)
        return session, note
