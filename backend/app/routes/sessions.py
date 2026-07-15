# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Session API routes.

Thin HTTP handlers that delegate business logic to SessionService.
"""

import logging
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from google.api_core.exceptions import AlreadyExists
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..api_errors import BadRequestError, ConflictError, NotFoundError, ServerError
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_baa_acceptance,
    require_cloud_tasks_invoker,
)
from ..db import release_db_connection
from ..jobs.task_queue import enqueue
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
from ..rate_limit import get_audio_upload_limiter
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
    UserRepository,
    get_user_repository,
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
from ..services.file_storage import FileTooLargeError, UploadTarget
from ..services.note_import_service import (
    DocumentTextExtractionError,
    NoteImportService,
    UnsupportedDocumentTypeError,
    extract_document_text,
)
from ..services.session_generation_worker import (
    UnknownTenantError,
    run_soap_generation_job,
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
    )


def get_worker_session_service(
    note_generation_service: NoteGenerationService = Depends(get_note_generation_service),
) -> SessionService:
    """SessionService for the off-request Cloud Tasks worker.

    Built from the raw repository factories — NOT the ``get_tenant_context``-
    scoped wrappers ``get_session_service`` uses. The worker authenticates as
    the Cloud-Tasks service account and arms its own tenant scope
    (``set_tenant_schema`` + RLS) inside ``run_soap_generation_job``, so it must
    not depend on ``get_tenant_context`` — that requires a Firebase user
    session and would 401 the service-account OIDC token before the handler
    runs. ``note_generation_service`` stays injected so tests can substitute a
    deterministic mock.
    """
    return SessionService(
        _session_repo_factory(),
        _patient_repo_factory(),
        note_generation_service,
        NoteService(_notes_repo_factory()),
    )


def _embed_note(note: Note | None) -> NoteResponse | None:
    return NoteResponse.from_note(note) if note is not None else None


@router.post("/api/patients/{patient_id}/sessions/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_session(
    patient_id: str,
    http_request: Request,
    request: UploadSessionRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> SessionResponse:
    """
    Upload a transcript and start SOAP generation asynchronously.

    Persists the session in ``processing`` and returns ``202`` immediately;
    the multi-second LLM generation runs on a Cloud Tasks worker
    (``/api/internal/jobs/generate-soap``) so it never holds the request
    thread (THERAPY-jonc). Poll ``GET /api/sessions/{id}`` for ``status`` —
    ``pending_review`` when the note is ready, ``failed`` if generation failed.
    The returned ``note`` is ``null`` until then.

    - **patient_id**: Patient ID for this session
    - **session_date**: ISO 8601 datetime of session
    - **transcript**: Transcript data (format and content)
    """
    _gate_trial_session(user.email)
    try:
        session, patient = session_service.create_session_for_generation(
            patient_id, user.id, request
        )
    except PatientNotFoundError as e:
        raise NotFoundError("Patient not found", {"patient_id": patient_id}) from e

    settings = get_settings()
    enqueue(
        settings.soap_generation_task_queue,
        "/api/internal/jobs/generate-soap",
        {"session_id": session.id, "user_id": user.id},
        dedup_key=session.id,
    )

    audit.log_session_action(AuditAction.SESSION_CREATED, user, http_request, session, patient)

    return SessionResponse.from_session(session, patient.display_name, _embed_note(None))


class GenerateSoapJob(BaseModel):
    """Cloud Tasks payload for off-request SOAP generation.

    Opaque identifiers only — deliberately no tenant schema name (it can
    identify a tenant) and no transcript/PHI. The worker re-resolves the schema
    from ``user_id`` server-side.
    """

    session_id: str
    user_id: str


@router.post("/api/internal/jobs/generate-soap", status_code=status.HTTP_200_OK)
def generate_soap_job(
    payload: GenerateSoapJob,
    http_request: Request,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    session_service: SessionService = Depends(get_worker_session_service),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: generate the SOAP note for a ``PROCESSING`` session.

    Invoked only by Cloud Tasks (service-account OIDC, enforced by
    ``require_cloud_tasks_invoker``). Scopes itself to the job's tenant and runs
    the LLM off the upload request thread.

    The SOAP note — clinical PHI — is created here, not at upload, so this is
    where its creation is audited (``SESSION_NOTE_GENERATED``), once the tenant
    schema is in scope. The actor is the owning clinician; the request context
    is the Cloud Tasks delivery (system-initiated), which is recorded honestly.

    Always answers ``200`` once the job is accounted for — including the
    non-retryable outcomes (unknown tenant, vanished session) and a recorded
    generation failure (the session is durably marked ``failed``). Returning
    ``200`` stops Cloud Tasks from retrying a job that can't succeed; only an
    unexpected ``5xx`` triggers the queue's retry/backoff.
    """
    try:
        session, patient, note = run_soap_generation_job(
            session_id=payload.session_id,
            user_id=payload.user_id,
            session_service=session_service,
        )
    except UnknownTenantError:
        logger.warning(
            "generate-soap job: no active tenant for session %s — dropping (non-retryable)",
            payload.session_id,
        )
        return {"status": "unknown_tenant"}
    except SessionNotFoundError:
        logger.warning(
            "generate-soap job: session %s not found — dropping (non-retryable)",
            payload.session_id,
        )
        return {"status": "not_found"}
    except SOAPGenerationFailedError:
        # Session already marked FAILED + committed inside the service. Don't
        # 5xx — a deterministic generation failure must not loop the queue.
        return {"status": "failed"}

    # Audit the PHI write at its creation point, in the tenant now in scope.
    owner = user_repo.get(payload.user_id)
    if owner is not None:
        audit.log_note_action(
            AuditAction.SESSION_NOTE_GENERATED,
            owner,
            http_request,
            note_id=note.id,
            patient_id=patient.id,
            session_id=session.id,
        )
    else:
        logger.warning(
            "generate-soap job: owner %s not found for audit on session %s",
            payload.user_id,
            session.id,
        )
    return {"status": "ok"}


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
    notes = notes_repo.get_by_session_ids([s.id for s in sessions], user.id)

    session_responses = []
    for s in sessions:
        patient = patients.get(s.patient_id)
        patient_name = patient.display_name if patient else "Unknown"
        session_responses.append(
            SessionResponse.from_session(
                s,
                patient_name,
                _embed_note(notes.get(s.id)),
            )
        )
        # This list embeds the full SOAP note in each item (see _embed_note),
        # so reading it is a per-record content read on par with the
        # GET /api/sessions/{id} detail view — audit one session_viewed per
        # session. The read-coalescing gate collapses repeats of the same
        # session within the window, so refetches don't flood the audit sink.
        audit.log_session_action(AuditAction.SESSION_VIEWED, user, request, s, patient)

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
        # This view discloses patient names and free-text session notes, so each
        # row is a per-record content read — audit one session_viewed per session,
        # matching GET /api/sessions. The read-coalescing gate collapses repeats
        # of the same session within the window so polling doesn't flood the sink.
        audit.log_session_action(AuditAction.SESSION_VIEWED, user, request, s, patient)

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


@router.post("/api/sessions/{session_id}/transcript", status_code=status.HTTP_202_ACCEPTED)
def upload_transcript_to_session(
    session_id: str,
    http_request: Request,
    request: UploadTranscriptToSessionRequest,
    user: User = Depends(require_baa_acceptance),
    session_service: SessionService = Depends(get_session_service),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Attach a transcript to an existing session and start async generation.

    Persists the transcript, marks the session ``processing``, returns ``202``,
    and enqueues SOAP generation on the Cloud Tasks worker — the note is
    produced off the request thread (THERAPY-jonc). Poll
    ``GET /api/sessions/{id}`` for status (``pending_review`` / ``failed``).
    """
    try:
        session = session_service.prepare_transcript_session_for_generation(
            session_id, user.id, request
        )
    except SessionNotFoundError as e:
        raise NotFoundError("Session not found") from e
    except InvalidSessionStatusError as e:
        raise BadRequestError(
            f"Session must be in 'recording_complete' status, got '{e.current_status}'",
            code="INVALID_STATUS",
        ) from e

    settings = get_settings()
    try:
        enqueue(
            settings.soap_generation_task_queue,
            "/api/internal/jobs/generate-soap",
            {"session_id": session.id, "user_id": user.id},
            dedup_key=session.id,
        )
    except AlreadyExists:
        # A generation job for this session is already queued (e.g. a
        # double-submit, or a retry inside the dedup window). The session is
        # PROCESSING and the in-flight job reads the latest transcript, so
        # there's nothing to do — answer 202 either way.
        logger.info("generate-soap already enqueued for session %s (dedup)", session.id)

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


def _audio_multipart_object_name(session_id: str, channel: str) -> str:
    """Per-session object name for a multipart-streamed channel.

    Kept separate from the ``signed/`` prefix the direct-to-GCS path uses so
    the two transports never collide on an object name.
    """
    return f"audio/{session_id}/{channel}.pcm"


async def _stream_audio_to_storage(
    upload: UploadFile,
    label: str,
    *,
    bucket: str,
    object_name: str,
) -> None:
    """Stream one channel's ``UploadFile`` straight to object storage.

    ``UploadFile`` already spools past ~1 MiB to a temp file, so the bytes are
    never held whole in heap — the win over ``_read_bounded`` (which joins
    every chunk into one ``bytes``) that would OOM under concurrent long
    sessions. The blocking storage write runs in a worker thread so it doesn't
    stall the event loop.
    """
    from ..services.file_storage import file_storage_from_settings

    storage = file_storage_from_settings(get_settings())
    try:
        await run_in_threadpool(
            storage.upload_stream,
            bucket=bucket,
            object_name=object_name,
            fileobj=upload.file,
            content_type=upload.content_type or "application/octet-stream",
            max_bytes=_MAX_AUDIO_SIZE,
        )
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{label} too large. Max {_MAX_AUDIO_SIZE // (1024 * 1024)} MB.",
        ) from exc


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


async def _stream_and_submit_assemblyai(
    *,
    session: TherapySession,
    session_repo: TherapySessionRepository,
    session_id: str,
    therapist_audio: UploadFile,
    client_audio: UploadFile,
    user: User,
    http_request: Request,
    audit: AuditService,
) -> dict[str, str]:
    """Stream both channels to object storage and enqueue the submit worker.

    Keeps the bytes off the heap (streamed, never buffered whole) and the
    multi-second VAD-split + provider submit off this request thread — the
    Cloud Task worker does that against ``audio_gcs_path``.
    """
    settings = get_settings()
    bucket = settings.transcription_audio_bucket
    if not bucket:
        raise ServerError("Transcription audio bucket is not configured")

    therapist_object = _audio_multipart_object_name(session_id, "therapist")
    client_object = _audio_multipart_object_name(session_id, "client")
    await _stream_audio_to_storage(
        therapist_audio, "therapist_audio", bucket=bucket, object_name=therapist_object
    )
    await _stream_audio_to_storage(
        client_audio, "client_audio", bucket=bucket, object_name=client_object
    )

    session.status = SessionStatus.TRANSCRIBING
    session.updated_at = utc_now()
    session.audio_gcs_path = f"{therapist_object},{client_object}"
    # "submitting" is the pre-submit marker; the submit worker overwrites it
    # with the provider job ids. Persisted so a retry is idempotent.
    session.transcription_job_metadata = {"provider": "assemblyai", "state": "submitting"}
    session_repo.update(session)

    try:
        enqueue(
            settings.transcription_task_queue,
            "/api/internal/assemblyai-submit",
            {"session_id": session_id, "user_id": user.id},
            dedup_key=f"aai-submit-{session_id}",
        )
    except AlreadyExists:
        # A submit task for this session is already queued (double-submit or a
        # retry inside the dedup window) — the worker reads the latest audio
        # path, so there's nothing to do. Don't revert.
        logger.info("assemblyai-submit already enqueued for session %s (dedup)", session_id)
    except Exception:
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


@router.post("/api/sessions/{session_id}/upload-audio")
async def upload_audio(
    session_id: str,
    therapist_audio: UploadFile,
    client_audio: UploadFile,
    http_request: Request,
    response: Response,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Upload dual-channel audio for server-side transcription.

    Accepts two audio files (therapist mic + client system audio), matching
    the companion app's AudioCaptureKit channel split. Each channel is
    transcribed separately with speaker labels, then merged by timestamp.

    For AssemblyAI the bytes are streamed straight to object storage and the
    provider submit runs on a Cloud Task, so a long session neither holds the
    request thread nor buffers hundreds of MB per channel in heap; the route
    answers 202. Practice tier users get priority processing; Solo tier uses
    the standard queue.
    """
    # Per-user burst guard: each upload spawns a transcription job, so cap how
    # fast a single caller can trigger them (per-minute + per-hour). Raises 429.
    get_audio_upload_limiter().check(user.id)

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

    if settings.transcription_provider == "assemblyai":
        result = await _stream_and_submit_assemblyai(
            session=session,
            session_repo=session_repo,
            session_id=session_id,
            therapist_audio=therapist_audio,
            client_audio=client_audio,
            user=user,
            http_request=http_request,
            audit=audit,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return result

    # Whisper: buffer + upload to GCS, submit GCP Batch job
    therapist_data = await _read_bounded(therapist_audio, "therapist_audio")
    client_data = await _read_bounded(client_audio, "client_audio")

    session.status = SessionStatus.TRANSCRIBING
    session.updated_at = utc_now()

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
# Both providers are supported: finalize records the object paths and
# dispatches. Whisper submits a GCP Batch job; AssemblyAI hands off to the
# same submit worker the multipart path uses — that worker downloads the
# objects and runs the VAD split server-side, so browser-direct and
# multipart converge on one transport-agnostic completion path.


def _audio_signed_object_name(session_id: str, channel: str) -> str:
    """Per-session object names for signed PUT URLs.

    Lives under ``signed/`` so the GCS-side bucket policy can apply
    a different retention / IAM shape if needed without touching the
    multipart-path objects.
    """
    return f"signed/{session_id}/{channel}.pcm"


class _AudioUploadChannel(BaseModel):
    # Self-describing upload recipe (url/method/headers/fields); the
    # client executes it without knowing which provider is configured.
    upload: UploadTarget
    gcs_path: str


class _AudioInitResponse(BaseModel):
    session_id: str
    therapist: _AudioUploadChannel
    client: _AudioUploadChannel
    # For client-side pre-flight UX only; the storage layer enforces.
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

    session = session_repo.get(session_id, user.id)
    if not session:
        raise NotFoundError("Session not found")

    bucket = settings.transcription_audio_bucket
    if not bucket:
        raise ServerError("Transcription audio bucket is not configured")

    from ..services.file_storage import file_storage_from_settings

    storage = file_storage_from_settings(settings)
    content_type = "application/octet-stream"
    therapist_path = _audio_signed_object_name(session_id, "therapist")
    client_path = _audio_signed_object_name(session_id, "client")
    therapist_target = storage.make_upload_target(
        bucket=bucket,
        object_name=therapist_path,
        content_type=content_type,
        max_bytes=_MAX_AUDIO_SIZE,
        ttl_seconds=settings.patient_documents_upload_url_ttl_seconds,
    )
    client_target = storage.make_upload_target(
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
        changes={
            "provider": settings.transcription_provider,
            "channels": 2,
            "transport": "signed_url",
        },
    )

    return _AudioInitResponse(
        session_id=session_id,
        therapist=_AudioUploadChannel(upload=therapist_target, gcs_path=therapist_path),
        client=_AudioUploadChannel(upload=client_target, gcs_path=client_path),
        max_bytes=_MAX_AUDIO_SIZE,
    )


@router.post("/api/sessions/{session_id}/upload-audio/finalize")
def finalize_audio_upload(
    session_id: str,
    http_request: Request,
    response: Response,
    user: User = Depends(require_baa_acceptance),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    audit: AuditService = Depends(get_audit_service),
) -> _AudioFinalizeResponse:
    """Verify both channel blobs landed in object storage, then start transcription.

    Provider-agnostic: the browser uploaded both channels directly to object
    storage via the signed URLs, so this only records the object paths and
    dispatches. Whisper submits a GCP Batch job; AssemblyAI hands off to the
    same submit worker the multipart path uses (it just reads
    ``audio_gcs_path``), so both transports converge there.

    Idempotent on retry: the size/exists check is read-only against storage,
    and both hand-offs dedupe by ``session_id``.
    """
    settings = get_settings()

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

    from ..services.file_storage import file_storage_from_settings

    storage = file_storage_from_settings(settings)
    therapist_path = _audio_signed_object_name(session_id, "therapist")
    client_path = _audio_signed_object_name(session_id, "client")

    for label, path in (("therapist", therapist_path), ("client", client_path)):
        meta = storage.fetch_metadata(bucket=bucket, object_name=path)
        if meta is None:
            raise BadRequestError(
                f"{label} audio upload not complete",
                {"channel": label},
                code="UPLOAD_NOT_COMPLETE",
            )

    session.status = SessionStatus.TRANSCRIBING
    session.updated_at = utc_now()
    session.audio_gcs_path = f"{therapist_path},{client_path}"

    if settings.transcription_provider == "assemblyai":
        # "submitting" is the pre-submit marker; the submit worker overwrites
        # it with the provider job ids. Persisted so a retry is idempotent.
        session.transcription_job_metadata = {"provider": "assemblyai", "state": "submitting"}
        try:
            enqueue(
                settings.transcription_task_queue,
                "/api/internal/assemblyai-submit",
                {"session_id": session_id, "user_id": user.id},
                dedup_key=f"aai-submit-{session_id}",
            )
        except AlreadyExists:
            logger.info("assemblyai-submit already enqueued for session %s (dedup)", session_id)
        except Exception:
            _revert_transcribing_and_raise(session, session_repo, session_id, "assemblyai")

        session_repo.update(session)
        audit.log_session_action(
            AuditAction.SESSION_AUDIO_UPLOADED,
            user,
            http_request,
            session,
            changes={"provider": "assemblyai", "channels": 2, "transport": "signed_url"},
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return _AudioFinalizeResponse(
            id=session.id,
            status=session.status,
            provider="assemblyai",
            queue="",
            message=(
                "Audio uploaded via signed URL (2 channels). Transcription queued (AssemblyAI)."
            ),
        )

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
