# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Session API routes.

Thin HTTP handlers that delegate business logic to SessionService.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

if TYPE_CHECKING:
    from ..services.eval_export_service import EvalExportService  # type: ignore[import-not-found]

from ..api_errors import BadRequestError, ConflictError, NotFoundError, ServerError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..db import release_db_connection
from ..models import (
    AuditAction,
    FinalizeSessionRequest,
    NoteResponse,
    PatientSummary,
    ScheduleSessionRequest,
    SessionListResponse,
    SessionResponse,
    SessionStatus,
    TodaySessionListResponse,
    TodaySessionResponse,
    UpdateSessionMetadataRequest,
    UpdateSessionRatingRequest,
    UpdateSessionStatusRequest,
    UploadSessionRequest,
    UploadTranscriptToSessionRequest,
    User,
)
from ..models.note import Note
from ..models.session import TherapySession
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
)
from ..repositories import (
    get_notes_repository as _notes_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..repositories import (
    get_session_repository as _session_repo_factory,
)
from ..services import (
    AuditService,
    InvalidSessionStatusError,
    InvalidStatusTransitionError,
    NoteGenerationService,
    NoteService,
    PatientNotFoundError,
    RegistryNoteGenerationService,
    SessionAlreadyInStatusError,
    SessionInTerminalStatusError,
    SessionNotFoundError,
    SessionService,
    SOAPGenerationFailedError,
    get_audit_service,
)
from ..services.assemblyai_transcription_service import AssemblyAiTranscriptionService
from ..services.note_import_service import (
    DocumentTextExtractionError,
    NoteImportService,
    UnsupportedDocumentTypeError,
    extract_document_text,
)
from ..services.transcription_queue_service import (
    MockTranscriptionQueueService,
    TranscriptionQueueService,
)
from ..settings import get_settings
from ..utcnow import utc_now

# Optional subscription extension point. When a billing overlay is
# installed it registers ``app.routes.subscription``; otherwise the
# import fails and the gate becomes a no-op.
try:
    from ..routes.subscription import (  # type: ignore[import-not-found]
        TrialLimitReachedError,
        check_and_count_trial_session,
    )
except ImportError:  # pragma: no cover -- no subscription overlay installed
    TrialLimitReachedError = None  # type: ignore[assignment,misc]
    check_and_count_trial_session = None  # type: ignore[assignment]


def _gate_trial_session(user_email: str) -> None:
    """Increment the trial-session counter and 402 if exhausted.

    No-op when no subscription overlay is installed. Wrapping the call
    in a helper keeps the per-route callsites a single line each.
    """
    if check_and_count_trial_session is None:
        return
    settings = get_settings()
    try:
        check_and_count_trial_session(user_email, settings)
    except Exception as exc:
        if TrialLimitReachedError is None or not isinstance(exc, TrialLimitReachedError):
            raise
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": {
                    "code": "TRIAL_LIMIT_REACHED",
                    "message": "Trial session limit reached. Upgrade to continue.",
                    "details": {
                        "trial_sessions_used": exc.used,
                        "trial_sessions_limit": exc.limit,
                    },
                }
            },
        ) from exc


logger = logging.getLogger(__name__)

# Background transcription tasks — prevent garbage collection

router = APIRouter(tags=["sessions"])


def get_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """Get patient repository scoped to the tenant's database."""
    return _patient_repo_factory()


def get_session_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> TherapySessionRepository:
    """Get session repository scoped to the tenant's database."""
    return _session_repo_factory()


def get_notes_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> NotesRepository:
    """Get notes repository scoped to the tenant's database."""
    return _notes_repo_factory()


def get_note_generation_service() -> NoteGenerationService:
    """Get note generation service instance."""
    return RegistryNoteGenerationService()


def get_note_import_service() -> NoteImportService:
    """Get the imported-note parse service instance."""
    return NoteImportService()


def get_note_service(
    notes_repo: NotesRepository = Depends(get_notes_repository),
) -> NoteService:
    """Get note service instance."""
    return NoteService(notes_repo)


def _build_eval_export_service() -> "EvalExportService | None":
    """Build eval export service when the optional module is available."""
    settings = get_settings()
    if not settings.is_saas:
        return None
    try:
        from ..services.eval_export_service import EvalExportService
        from ..services.pii_redaction_service import PIIRedactionService
    except ImportError:
        logger.warning("presidio_analyzer not installed — eval export disabled")
        return None

    return EvalExportService(PIIRedactionService(), settings)


def get_session_service(
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
    note_service: NoteService = Depends(get_note_service),
) -> SessionService:
    """Get session service instance with all dependencies."""
    return SessionService(
        session_repo,
        patient_repo,
        note_generation_service,
        note_service,
        _build_eval_export_service(),
    )


def _embed_note(note: Note | None) -> NoteResponse | None:
    return NoteResponse.from_note(note) if note is not None else None


@router.post("/api/patients/{patient_id}/sessions/upload", status_code=status.HTTP_201_CREATED)
def upload_session(
    patient_id: str,
    http_request: Request,
    request: UploadSessionRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """
    Upload transcript and create session with SOAP note generation.

    - **patient_id**: Patient ID for this session
    - **session_date**: ISO 8601 datetime of session
    - **transcript**: Transcript data (format and content)
    """
    _gate_trial_session(user.email)
    try:
        session, patient, note = session_service.upload_session(patient_id, user.id, request)
    except PatientNotFoundError as e:
        raise NotFoundError("Patient not found", {"patient_id": patient_id}) from e

    audit.log_session_action(AuditAction.SESSION_CREATED, user, http_request, session, patient)

    return SessionResponse.from_session(session, patient.display_name, _embed_note(note))


# Generous guardrail for an uploaded note document. A single SOAP note is
# tiny; this only guards against accidental large uploads.
_MAX_IMPORT_DOC_BYTES = 15 * 1024 * 1024


def _resolve_import_session_date(override: str | None, extracted: datetime | None) -> datetime:
    """Pick the session date for an import: caller override > document > now.

    Falls back to upload time (as a naive datetime, matching the date the
    transcript-upload path stores from a ``datetime-local`` input) so the
    import still succeeds when no date is found; the clinician can correct
    it during review.
    """
    if override:
        try:
            return datetime.fromisoformat(override)
        except ValueError as exc:
            raise BadRequestError(
                "Invalid session_date; expected ISO 8601.",
                {"session_date": override},
                code="INVALID_SESSION_DATE",
            ) from exc
    if extracted is not None:
        return extracted
    logger.warning("Imported note had no detectable session date; defaulting to now")
    return utc_now().replace(tzinfo=None)


@router.post(
    "/api/patients/{patient_id}/sessions/import",
    status_code=status.HTTP_201_CREATED,
)
async def import_session(
    patient_id: str,
    file: UploadFile,
    http_request: Request,
    session_date: str | None = Form(default=None),
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    note_import_service: NoteImportService = Depends(get_note_import_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """Import an existing SOAP note (PDF or TXT) as a pending-review session.

    Extracts the document's text, parses it into a structured SOAP note plus
    the date the session took place, and creates a session dated from the
    document — or from ``session_date`` when the caller overrides it. The
    original text is kept as the session transcript so it can be reviewed
    beside the parsed note. One file per request; the client uploads several
    in parallel for a bulk chart import.
    """
    _gate_trial_session(user.email)

    data = await file.read()
    if len(data) > _MAX_IMPORT_DOC_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max {_MAX_IMPORT_DOC_BYTES // (1024 * 1024)} MB.",
        )

    try:
        text = extract_document_text(data, content_type=file.content_type, filename=file.filename)
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except DocumentTextExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Release the request-scoped DB connection before the multi-second LLM
    # parse. The middleware opens a transaction (SET search_path) at request
    # entry; holding that pooled connection idle across the Gemini call is
    # what let Cloud SQL reap it mid-request, surfacing as a 500 on the first
    # query inside import_session. Tenant scoping re-arms on the next
    # checkout -- same seam pattern as upload_session (THERAPY-da7t).
    release_db_connection()

    try:
        parsed = note_import_service.parse_soap_note(text)
    except ValueError as exc:
        logger.exception("Imported-note parse failed")
        raise ServerError("Could not read the SOAP note from this document.") from exc

    resolved_date = _resolve_import_session_date(session_date, parsed.session_datetime())

    try:
        session, patient, note = session_service.import_session(
            patient_id,
            user.id,
            session_date=resolved_date,
            source_text=text,
            note_content=parsed.content,
        )
    except PatientNotFoundError as exc:
        raise NotFoundError("Patient not found", {"patient_id": patient_id}) from exc

    audit.log_session_action(AuditAction.SESSION_CREATED, user, http_request, session, patient)

    return SessionResponse.from_session(session, patient.display_name, _embed_note(note))


@router.get("/api/sessions")
def list_sessions(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    audit: AuditService = Depends(get_audit_service),
) -> SessionListResponse:
    """
    List sessions for the current user with pagination.

    - **page**: Page number (default 1)
    - **page_size**: Items per page (default 20, max 100)

    Returns sessions sorted by session_date descending (newest first).
    """
    sessions, total = session_repo.list_by_user(user.id, page=page, page_size=page_size)

    patient_ids = list({s.patient_id for s in sessions})
    patients = patient_repo.get_multiple(patient_ids, user.id)

    session_responses = []
    for s in sessions:
        patient = patients.get(s.patient_id)
        patient_name = patient.display_name if patient else "Unknown"
        session_responses.append(
            SessionResponse.from_session(
                s,
                patient_name,
                _embed_note(notes_repo.get_by_session_id(s.id, user.id)),
            )
        )

    audit.log_session_list(user, request, total)

    return SessionListResponse(
        data=session_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/api/sessions/today")
def get_today_sessions(
    request: Request,
    timezone: str = Query("UTC", description="IANA timezone (e.g. America/New_York)"),
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> TodaySessionListResponse:
    """Fetch today's sessions for the authenticated therapist."""
    try:
        sessions = session_repo.list_today_by_user(user.id, timezone)
    except KeyError:
        raise BadRequestError(f"Invalid timezone: {timezone}", {"field": "timezone"}) from None

    # Batch-fetch patients to avoid N+1
    patient_ids = list({s.patient_id for s in sessions})
    patients = patient_repo.get_multiple(patient_ids, user.id)

    data = []
    for s in sessions:
        patient = patients.get(s.patient_id)
        patient_summary = PatientSummary(
            id=patient.id if patient else s.patient_id,
            first_name=patient.first_name if patient else "Unknown",
            last_name=patient.last_name if patient else "",
        )
        data.append(
            TodaySessionResponse(
                id=s.id,
                patient_id=s.patient_id,
                patient=patient_summary,
                status=SessionStatus(s.status),
                scheduled_at=s.scheduled_at,
                duration_minutes=s.duration_minutes or 50,
                video_link=s.video_link,
                video_platform=s.video_platform,
                session_type=s.session_type or "individual",
                source=s.source or "companion",
                notes=s.notes,
                started_at=s.started_at,
                ended_at=s.ended_at,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
        )

    audit.log_session_list(user, request, len(data))

    return TodaySessionListResponse(data=data, total=len(data))


@router.get("/api/sessions/{session_id}")
def get_session(
    session_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """
    Get session details by ID.

    - **session_id**: The session's unique identifier

    Returns the session if found and belongs to the current user, with
    the associated note (if any) embedded under ``note``.
    """
    session = session_repo.get(session_id, user.id)

    if not session:
        raise NotFoundError("Session not found", {"session_id": session_id})

    patient = patient_repo.get(session.patient_id, user.id)
    patient_name = patient.display_name if patient else "Unknown"
    note = notes_repo.get_by_session_id(session.id, user.id)

    audit.log_session_action(AuditAction.SESSION_VIEWED, user, request, session, patient)

    return SessionResponse.from_session(session, patient_name, _embed_note(note))


@router.patch("/api/sessions/{session_id}/finalize")
def finalize_session(
    session_id: str,
    http_request: Request,
    request: FinalizeSessionRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """
    Finalize a session after therapist review.

    - **session_id**: The session's unique identifier
    - **quality_rating**: Quality rating 1-5 (optional)
    - **quality_rating_reason**: Textual explanation for the rating (optional)
    - **quality_rating_sections**: SOAP sections needing improvement (optional)
    - **soap_note_edited**: Edited SOAP note if therapist made changes (optional)

    Sets status to "finalized" and records finalized_at timestamp.
    """
    try:
        session, patient, note = session_service.finalize_session(session_id, user.id, request)
    except SessionNotFoundError as e:
        raise NotFoundError("Session not found", {"session_id": session_id}) from e
    except InvalidSessionStatusError as e:
        raise BadRequestError(
            f"Cannot finalize session with status '{e.current_status}'",
            {"current_status": e.current_status},
            code="INVALID_STATUS",
        ) from e

    patient_name = patient.display_name if patient else "Unknown"

    audit.log_session_action(
        AuditAction.SESSION_FINALIZED,
        user,
        http_request,
        session,
        patient,
        changes={"quality_rating": request.quality_rating},
    )

    return SessionResponse.from_session(session, patient_name, _embed_note(note))


@router.patch("/api/sessions/{session_id}/rating")
def update_session_rating(
    session_id: str,
    http_request: Request,
    request: UpdateSessionRatingRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """
    Update quality rating for a finalized session.

    - **session_id**: The session's unique identifier
    - **quality_rating**: New quality rating 1-5
    - **quality_rating_reason**: Textual explanation for the rating (optional)
    - **quality_rating_sections**: SOAP sections needing improvement (optional)

    Allows therapist to update rating after finalization.
    """
    try:
        session, patient, note, old_rating = session_service.update_rating(
            session_id, user.id, request
        )
    except SessionNotFoundError as e:
        raise NotFoundError("Session not found", {"session_id": session_id}) from e
    except InvalidSessionStatusError as e:
        raise BadRequestError(
            "Can only update rating for finalized sessions",
            {"current_status": "not_finalized"},
            code="INVALID_STATUS",
        ) from e

    patient_name = patient.display_name if patient else "Unknown"

    audit.log_session_action(
        AuditAction.SESSION_RATING_UPDATED,
        user,
        http_request,
        session,
        patient,
        changes={"quality_rating": {"old": old_rating, "new": request.quality_rating}},
    )

    return SessionResponse.from_session(session, patient_name, _embed_note(note))


# --- Companion scheduling endpoints ---


@router.post("/api/sessions/schedule", status_code=status.HTTP_201_CREATED)
def schedule_session(
    http_request: Request,
    request: ScheduleSessionRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """Create a scheduled session (pre-recording)."""
    _gate_trial_session(user.email)
    try:
        session, patient = session_service.schedule_session(user.id, request)
    except PatientNotFoundError as e:
        raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND") from e

    audit.log_session_action(AuditAction.SESSION_CREATED, user, http_request, session, patient)

    return SessionResponse.from_session(session, patient.display_name)


@router.patch("/api/sessions/{session_id}/status")
def update_session_status(
    session_id: str,
    http_request: Request,
    request: UpdateSessionStatusRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """Transition session status with state machine validation."""
    try:
        session, patient = session_service.transition_status(session_id, user.id, request)
    except SessionNotFoundError as e:
        raise NotFoundError("Session not found") from e
    except SessionAlreadyInStatusError as e:
        raise ConflictError(
            f"Session is already in status '{e.status}'", code="ALREADY_IN_STATUS"
        ) from e
    except InvalidStatusTransitionError as e:
        raise BadRequestError(
            f"Cannot transition from '{e.current}' to '{e.target}'",
            code="INVALID_STATUS_TRANSITION",
        ) from e

    patient_name = patient.display_name if patient else "Unknown"

    audit.log_session_action(
        AuditAction.SESSION_CREATED,
        user,
        http_request,
        session,
        patient,
        changes={"status": request.status.value},
    )

    return SessionResponse.from_session(session, patient_name)


@router.patch("/api/sessions/{session_id}")
def update_session_metadata(
    session_id: str,
    http_request: Request,
    request: UpdateSessionMetadataRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """Update session metadata (reschedule, change video link, etc.)."""
    try:
        session, patient = session_service.update_session_metadata(session_id, user.id, request)
    except SessionNotFoundError as e:
        raise NotFoundError("Session not found") from e
    except SessionInTerminalStatusError as e:
        raise BadRequestError(
            f"Cannot modify session in terminal status '{e.status}'",
            code="TERMINAL_STATUS",
        ) from e

    patient_name = patient.display_name if patient else "Unknown"
    changed_fields = sorted(request.model_dump(exclude_unset=True).keys())
    audit.log_session_action(
        AuditAction.SESSION_UPDATED,
        user,
        http_request,
        session,
        patient,
        changes={"changed_fields": changed_fields},
    )
    return SessionResponse.from_session(session, patient_name)


@router.post("/api/sessions/{session_id}/transcript")
def upload_transcript_to_session(
    session_id: str,
    http_request: Request,
    request: UploadTranscriptToSessionRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Upload a transcript to an existing session and trigger SOAP pipeline."""
    try:
        session, _note = session_service.upload_transcript_to_session(session_id, user.id, request)
    except SessionNotFoundError as e:
        raise NotFoundError("Session not found") from e
    except InvalidSessionStatusError as e:
        raise BadRequestError(
            f"Session must be in 'recording_complete' status, got '{e.current_status}'",
            code="INVALID_STATUS",
        ) from e
    except SOAPGenerationFailedError as e:
        raise ServerError(
            "SOAP generation failed. Please try again.", code="SOAP_GENERATION_FAILED"
        ) from e

    audit.log_session_action(
        AuditAction.SESSION_TRANSCRIPT_UPLOADED,
        user,
        http_request,
        session,
        changes={"format": request.format},
    )
    return {
        "id": session.id,
        "status": session.status,
        "message": "Transcript received. SOAP note generation started.",
    }


# --- Audio upload for server-side transcription ---

_MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500 MB
_AUDIO_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MiB
_ALLOWED_AUDIO_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/webm",
    "audio/flac",
    "application/octet-stream",
}


async def _read_bounded(upload: UploadFile, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_AUDIO_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{label} too large. Max {_MAX_AUDIO_SIZE // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _revert_transcribing_and_raise(
    session: TherapySession,
    session_repo: TherapySessionRepository,
    session_id: str,
    provider: str,
) -> None:
    """Revert a session out of TRANSCRIBING after a failed transcription enqueue.

    Without this, a failure between status=TRANSCRIBING and the queue task
    leaves the session stuck in TRANSCRIBING with no poller — orphan forever.
    Reverting to RECORDING_COMPLETE lets the client retry the upload.
    """
    logger.exception(
        "Failed to enqueue %s transcription for session %s; reverting status so client can retry",
        provider,
        session_id,
    )
    session.status = SessionStatus.RECORDING_COMPLETE
    session_repo.update(session)
    raise ServerError(
        "Audio uploaded but transcription could not be queued. Please retry the upload."
    ) from None


@router.post("/api/sessions/{session_id}/upload-audio")
async def upload_audio(
    session_id: str,
    therapist_audio: UploadFile,
    client_audio: UploadFile,
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Upload dual-channel audio for server-side Whisper transcription.

    Accepts two audio files (therapist mic + client system audio), matching
    the companion app's AudioCaptureKit channel split. Each channel is
    transcribed separately with speaker labels, then merged by timestamp.

    Practice tier users get priority processing; Solo tier uses standard queue.
    """
    settings = get_settings()
    if not settings.transcription_enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Server-side transcription is not enabled.",
        )

    session = session_repo.get(session_id, user.id)
    if not session:
        raise NotFoundError("Session not found")

    _retryable_statuses = {
        SessionStatus.RECORDING_COMPLETE,
        SessionStatus.TRANSCRIBING,
        SessionStatus.FAILED,
    }
    if session.status not in _retryable_statuses:
        raise BadRequestError(
            f"Session must be in 'recording_complete', 'transcribing', "
            f"or 'failed' status, got '{session.status}'",
            code="INVALID_STATUS",
        )

    for label, f in [("therapist_audio", therapist_audio), ("client_audio", client_audio)]:
        if f.content_type and f.content_type not in _ALLOWED_AUDIO_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported audio type for {label}: {f.content_type}",
            )

    therapist_data = await _read_bounded(therapist_audio, "therapist_audio")
    client_data = await _read_bounded(client_audio, "client_audio")

    # Transition to transcribing
    session.status = SessionStatus.TRANSCRIBING
    session.updated_at = utc_now()

    if settings.transcription_provider == "assemblyai":
        # AssemblyAI: upload + submit (fast), then enqueue Cloud Task for polling.
        # This survives Cloud Run instance restarts — no more in-process polling.
        aai_service = AssemblyAiTranscriptionService(settings)
        job_metadata = await aai_service.submit_dual_channel(
            therapist_audio=therapist_data,
            client_audio=client_data,
        )

        # Store job metadata so the polling Cloud Task knows which jobs to check
        session.transcription_job_metadata = {
            "provider": "assemblyai",
            "jobs": job_metadata,
        }
        session_repo.update(session)

        # Enqueue Cloud Task to poll for results (HIPAA: no schema_name in payload).
        from ..services.cloud_tasks_service import enqueue_cloud_task

        try:
            enqueue_cloud_task(
                queue_name=settings.transcription_task_queue,
                endpoint_path="/api/internal/transcription-poll",
                payload={"session_id": session_id, "user_id": user.id},
            )
        except Exception as _enqueue_exc:
            from google.api_core.exceptions import AlreadyExists

            if isinstance(_enqueue_exc, AlreadyExists):
                # Named-task dedup: task already enqueued and running — no revert.
                logger.info("Transcription task already enqueued (dedup); skipping revert")
            else:
                _revert_transcribing_and_raise(session, session_repo, session_id, "assemblyai")

        audit.log_session_action(
            AuditAction.SESSION_AUDIO_UPLOADED,
            user,
            http_request,
            session,
            changes={"provider": "assemblyai", "channels": 2},
        )
        return {
            "id": session.id,
            "status": session.status,
            "provider": "assemblyai",
            "message": "Audio uploaded (2 channels). Transcription queued (AssemblyAI).",
        }

    # Whisper: upload to GCS, submit GCP Batch job
    queue_service: TranscriptionQueueService
    if settings.is_development:
        queue_service = MockTranscriptionQueueService()
    else:
        queue_service = TranscriptionQueueService()

    therapist_filename = therapist_audio.filename or f"{session_id}-therapist.pcm"
    client_filename = client_audio.filename or f"{session_id}-client.pcm"
    therapist_gcs_path = queue_service.upload_audio(therapist_data, session_id, therapist_filename)
    client_gcs_path = queue_service.upload_audio(client_data, session_id, client_filename)

    session.audio_gcs_path = f"{therapist_gcs_path},{client_gcs_path}"
    session_repo.update(session)

    is_practice = settings.pablo_edition == "practice"
    try:
        queue_service.enqueue_transcription(
            session_id=session_id,
            tenant_db="(default)",
            user_id=user.id,
            gcs_path=session.audio_gcs_path,
            priority=is_practice,
        )
    except Exception:
        _revert_transcribing_and_raise(session, session_repo, session_id, "whisper")

    queue_type = "priority" if is_practice else "standard"
    audit.log_session_action(
        AuditAction.SESSION_AUDIO_UPLOADED,
        user,
        http_request,
        session,
        changes={"provider": "whisper", "queue": queue_type, "channels": 2},
    )
    return {
        "id": session.id,
        "status": session.status,
        "provider": "whisper",
        "queue": queue_type,
        "message": f"Audio uploaded (2 channels). Transcription queued ({queue_type}).",
    }


# --- Audio upload via signed-URL direct-to-GCS (additive) ---
#
# Complements the multipart ``/upload-audio`` endpoint above. The
# companion app can opt into this path to keep Cloud Run bandwidth
# flat when many users record concurrently — bytes flow browser→GCS
# directly. The multipart endpoint stays so existing companion
# builds keep working; migrating is its own bead.
#
# Scope: Whisper provider only. AssemblyAI's VAD region splitting
# operates on raw bytes and isn't a natural fit for browser-direct
# upload — that path stays on multipart until we land a follow-up
# that uses AssemblyAI's ``audio_url`` parameter against a signed
# GCS GET URL.


def _audio_signed_object_name(session_id: str, channel: str) -> str:
    """Per-session object names for signed PUT URLs.

    Lives under ``signed/`` so the GCS-side bucket policy can apply
    a different retention / IAM shape if needed without touching the
    multipart-path objects.
    """
    return f"signed/{session_id}/{channel}.pcm"


class _AudioUploadChannel(BaseModel):
    upload_url: str
    gcs_path: str


class _AudioInitResponse(BaseModel):
    session_id: str
    therapist: _AudioUploadChannel
    client: _AudioUploadChannel
    required_content_type: str
    max_bytes: int


class _AudioFinalizeResponse(BaseModel):
    id: str
    status: str
    provider: str
    queue: str
    message: str


@router.post(
    "/api/sessions/{session_id}/upload-audio/init",
    status_code=status.HTTP_201_CREATED,
)
def init_audio_upload(
    session_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    audit: AuditService = Depends(get_audit_service),
) -> _AudioInitResponse:
    """Mint two signed PUT URLs (therapist + client channels).

    Audit emission carries channel count and provider only — no
    filename, no caller-provided metadata to redact.
    """
    settings = get_settings()
    if not settings.transcription_enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Server-side transcription is not enabled.",
        )
    if settings.transcription_provider != "whisper":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Signed-URL audio upload only supports the whisper provider "
                "in v1; use POST /api/sessions/{id}/upload-audio for "
                "assemblyai."
            ),
        )

    session = session_repo.get(session_id, user.id)
    if not session:
        raise NotFoundError("Session not found")

    bucket = settings.transcription_audio_bucket
    if not bucket:
        raise ServerError("Transcription audio bucket is not configured")

    from google.cloud import storage  # type: ignore[attr-defined]

    from ..services.signed_upload import make_upload_url

    client = storage.Client()
    content_type = "application/octet-stream"
    therapist_path = _audio_signed_object_name(session_id, "therapist")
    client_path = _audio_signed_object_name(session_id, "client")
    therapist_url = make_upload_url(
        client=client,
        bucket=bucket,
        object_name=therapist_path,
        content_type=content_type,
        max_bytes=_MAX_AUDIO_SIZE,
        ttl_seconds=settings.patient_documents_upload_url_ttl_seconds,
    )
    client_url = make_upload_url(
        client=client,
        bucket=bucket,
        object_name=client_path,
        content_type=content_type,
        max_bytes=_MAX_AUDIO_SIZE,
        ttl_seconds=settings.patient_documents_upload_url_ttl_seconds,
    )

    audit.log_session_action(
        AuditAction.SESSION_AUDIO_UPLOAD_INITIATED,
        user,
        http_request,
        session,
        changes={"provider": "whisper", "channels": 2, "transport": "signed_url"},
    )

    return _AudioInitResponse(
        session_id=session_id,
        therapist=_AudioUploadChannel(upload_url=therapist_url, gcs_path=therapist_path),
        client=_AudioUploadChannel(upload_url=client_url, gcs_path=client_path),
        required_content_type=content_type,
        max_bytes=_MAX_AUDIO_SIZE,
    )


@router.post("/api/sessions/{session_id}/upload-audio/finalize")
def finalize_audio_upload(
    session_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    audit: AuditService = Depends(get_audit_service),
) -> _AudioFinalizeResponse:
    """Verify both channel blobs landed in GCS, then enqueue Whisper.

    Idempotent on retry: the size/exists check is read-only against
    GCS, and ``enqueue_transcription`` is the same as the multipart
    endpoint calls — the queue worker dedupes by ``session_id``.
    """
    settings = get_settings()
    if settings.transcription_provider != "whisper":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Signed-URL audio finalize only supports the whisper provider in v1.",
        )

    session = session_repo.get(session_id, user.id)
    if not session:
        raise NotFoundError("Session not found")

    _retryable_statuses = {
        SessionStatus.RECORDING_COMPLETE,
        SessionStatus.TRANSCRIBING,
        SessionStatus.FAILED,
    }
    if session.status not in _retryable_statuses:
        raise BadRequestError(
            f"Session must be in 'recording_complete', 'transcribing', "
            f"or 'failed' status, got '{session.status}'",
            code="INVALID_STATUS",
        )

    bucket = settings.transcription_audio_bucket
    if not bucket:
        raise ServerError("Transcription audio bucket is not configured")

    from google.cloud import storage  # type: ignore[attr-defined]
    from google.cloud.exceptions import NotFound

    from ..services.signed_upload import fetch_blob_metadata

    storage_client = storage.Client()
    therapist_path = _audio_signed_object_name(session_id, "therapist")
    client_path = _audio_signed_object_name(session_id, "client")

    for label, path in (("therapist", therapist_path), ("client", client_path)):
        try:
            meta = fetch_blob_metadata(client=storage_client, bucket=bucket, object_name=path)
        except NotFound as exc:
            raise BadRequestError(
                f"{label} audio upload not complete",
                {"channel": label},
                code="UPLOAD_NOT_COMPLETE",
            ) from exc
        if meta is None:
            raise BadRequestError(
                f"{label} audio upload not complete",
                {"channel": label},
                code="UPLOAD_NOT_COMPLETE",
            )

    session.status = SessionStatus.TRANSCRIBING
    session.updated_at = utc_now()
    session.audio_gcs_path = f"{therapist_path},{client_path}"

    queue_service: TranscriptionQueueService = (
        MockTranscriptionQueueService() if settings.is_development else TranscriptionQueueService()
    )
    is_practice = settings.pablo_edition == "practice"
    try:
        queue_service.enqueue_transcription(
            session_id=session_id,
            tenant_db="(default)",
            user_id=user.id,
            gcs_path=session.audio_gcs_path,
            priority=is_practice,
        )
    except Exception:
        _revert_transcribing_and_raise(session, session_repo, session_id, "whisper")

    session_repo.update(session)
    queue_type = "priority" if is_practice else "standard"
    audit.log_session_action(
        AuditAction.SESSION_AUDIO_UPLOADED,
        user,
        http_request,
        session,
        changes={
            "provider": "whisper",
            "queue": queue_type,
            "channels": 2,
            "transport": "signed_url",
        },
    )

    return _AudioFinalizeResponse(
        id=session.id,
        status=session.status,
        provider="whisper",
        queue=queue_type,
        message=f"Audio uploaded via signed URL (2 channels). Transcription queued ({queue_type}).",
    )
