# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Session API routes.

Thin HTTP handlers that delegate business logic to SessionService.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status

if TYPE_CHECKING:
    from ..services.eval_export_service import EvalExportService  # type: ignore[import-not-found]

from ..api_errors import BadRequestError, ConflictError, NotFoundError, ServerError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    FinalizeSessionRequest,
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
from ..repositories import (
    PatientRepository,
    TherapySessionRepository,
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
    MeetingTranscriptionSOAPService,
    PatientNotFoundError,
    SessionAlreadyInStatusError,
    SessionInTerminalStatusError,
    SessionNotFoundError,
    SessionService,
    SOAPGenerationFailedError,
    SOAPGenerationService,
    get_audit_service,
)
from ..services.assemblyai_transcription_service import AssemblyAiTranscriptionService
from ..services.transcription_queue_service import (
    MockTranscriptionQueueService,
    TranscriptionQueueService,
)
from ..settings import get_settings
from ..utcnow import utc_now

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


def get_soap_generation_service() -> SOAPGenerationService:
    """Get SOAP generation service instance."""
    return MeetingTranscriptionSOAPService()


def _build_eval_export_service() -> "EvalExportService | None":
    """Build eval export service if SaaS edition is active."""
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
    soap_service: SOAPGenerationService = Depends(get_soap_generation_service),
) -> SessionService:
    """Get session service instance with all dependencies."""
    return SessionService(session_repo, patient_repo, soap_service, _build_eval_export_service())


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
    try:
        session, patient = session_service.upload_session(patient_id, user.id, request)
    except PatientNotFoundError as e:
        raise NotFoundError("Patient not found", {"patient_id": patient_id}) from e

    audit.log_session_action(AuditAction.SESSION_CREATED, user, http_request, session, patient)

    return SessionResponse.from_session(session, patient.display_name)


@router.get("/api/sessions")
def list_sessions(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
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
        session_responses.append(SessionResponse.from_session(s, patient_name))

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
        raise BadRequestError(
            f"Invalid timezone: {timezone}", {"field": "timezone"}
        ) from None

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
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """
    Get session details by ID.

    - **session_id**: The session's unique identifier

    Returns the session if found and belongs to the current user.
    """
    session = session_repo.get(session_id, user.id)

    if not session:
        raise NotFoundError("Session not found", {"session_id": session_id})

    patient = patient_repo.get(session.patient_id, user.id)
    patient_name = patient.display_name if patient else "Unknown"

    audit.log_session_action(AuditAction.SESSION_VIEWED, user, request, session, patient)

    return SessionResponse.from_session(session, patient_name)


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
    - **quality_rating**: Quality rating 1-5 (required)
    - **quality_rating_reason**: Textual explanation for the rating (optional)
    - **quality_rating_sections**: SOAP sections needing improvement (optional)
    - **soap_note_edited**: Edited SOAP note if therapist made changes (optional)

    Sets status to "finalized" and records finalized_at timestamp.
    """
    try:
        session, patient = session_service.finalize_session(session_id, user.id, request)
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

    return SessionResponse.from_session(session, patient_name)


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
        session, patient, old_rating = session_service.update_rating(session_id, user.id, request)
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

    return SessionResponse.from_session(session, patient_name)


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
    _http_request: Request,
    request: UpdateSessionMetadataRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    _audit: AuditService = Depends(get_audit_service),
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
    return SessionResponse.from_session(session, patient_name)


@router.post("/api/sessions/{session_id}/transcript")
def upload_transcript_to_session(
    session_id: str,
    _http_request: Request,
    request: UploadTranscriptToSessionRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    _audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Upload a transcript to an existing session and trigger SOAP pipeline."""
    try:
        session = session_service.upload_transcript_to_session(session_id, user.id, request)
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

    return {
        "id": session.id,
        "status": session.status,
        "message": "Transcript received. SOAP note generation started.",
    }


# --- Audio upload for server-side transcription ---

_MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500 MB
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


@router.post("/api/sessions/{session_id}/upload-audio")
async def upload_audio(
    session_id: str,
    therapist_audio: UploadFile,
    client_audio: UploadFile,
    _http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
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

    therapist_data = await therapist_audio.read()
    client_data = await client_audio.read()

    for label, data in [("therapist_audio", therapist_data), ("client_audio", client_data)]:
        if len(data) > _MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{label} too large. Max {_MAX_AUDIO_SIZE // (1024 * 1024)} MB.",
            )

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

        # Enqueue Cloud Task to poll for results (HIPAA: no schema_name in payload)
        from ..services.cloud_tasks_service import enqueue_cloud_task

        enqueue_cloud_task(
            queue_name=settings.transcription_task_queue,
            endpoint_path="/api/internal/transcription-poll",
            payload={"session_id": session_id, "user_id": user.id},
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
    queue_service.enqueue_transcription(
        session_id=session_id,
        tenant_db="(default)",
        user_id=user.id,
        gcs_path=session.audio_gcs_path,
        priority=is_practice,
    )

    queue_type = "priority" if is_practice else "standard"
    return {
        "id": session.id,
        "status": session.status,
        "provider": "whisper",
        "queue": queue_type,
        "message": f"Audio uploaded (2 channels). Transcription queued ({queue_type}).",
    }
