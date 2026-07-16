# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Internal transcription endpoints (service-authed, not user-facing).

Covers the AssemblyAI batch-transcription lifecycle once audio is in object
storage:

  * ``POST /api/internal/assemblyai-submit`` — reduce each channel to a
    single speech-only file with the energy VAD, stage it in object storage,
    and submit one AssemblyAI job per channel (fetched via a presigned GET).
    Runs on a Cloud Task so the multi-second submit never sits on the
    upload request thread.
  * ``POST /api/internal/transcription-poll`` — poll the submitted jobs;
    re-enqueue until every one is done, then merge and hand off.
  * ``POST /api/internal/transcription-complete`` — accept a finished
    transcript posted by an external worker and hand off.

The two hand-off routes and the shared ``process_transcription_result`` do
only cheap, bounded DB work (flip the session, persist the transcript) and
then enqueue the durable SOAP-generation worker — the LLM call never runs
inline here. Every endpoint is gated to the Cloud Tasks invoker service
account (``require_cloud_tasks_invoker``) — these routes drive privileged,
tenant-scoped work and must never be reachable by an ordinary user token.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from google.api_core.exceptions import AlreadyExists
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth.service import _resolve_practice_from_email, require_cloud_tasks_invoker
from ..db import _request_session, arm_current_user_id, create_standalone_session
from ..db.models import TherapySessionRow
from ..db.platform_models import PlatformUserRow, PracticeRow
from ..models import SessionStatus, UploadTranscriptToSessionRequest
from ..models.audit import AuditAction
from ..repositories import (
    get_notes_repository,
    get_patient_repository,
    get_session_repository,
    get_user_repository,
)
from ..services import (
    NoteService,
    RegistryNoteGenerationService,
    SessionNotFoundError,
    SOAPGenerationFailedError,
    get_audit_service,
)
from ..services.assemblyai_transcription_service import AssemblyAiTranscriptionService
from ..services.cloud_tasks_service import enqueue_cloud_task
from ..services.file_storage import file_storage_from_settings
from ..services.session_service import SessionService
from ..settings import get_settings

if TYPE_CHECKING:
    from ..models.session import TherapySession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])

# How long AssemblyAI's fetch of the staged speech-only audio has before the
# presigned GET expires. Fetches start within seconds of submit; an hour
# covers provider-side retries without leaving a long-lived URL around.
_SPEECH_AUDIO_URL_TTL_SECONDS = 3600


class TranscriptionCompleteRequest(BaseModel):
    """Callback payload from the transcription worker."""

    session_id: str
    tenant_db: str
    user_id: str
    transcript_content: str = Field(min_length=1)
    transcript_format: str = "vtt"


class TranscriptionPollRequest(BaseModel):
    """Cloud Task payload for polling AssemblyAI transcription status."""

    session_id: str
    user_id: str


class AssemblyAiSubmitRequest(BaseModel):
    """Cloud Task payload for submitting uploaded audio to AssemblyAI."""

    session_id: str
    user_id: str


def _validate_tenant_db(tenant_db: str) -> None:
    """Validate that tenant_db corresponds to an active tenant."""
    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        return

    with create_standalone_session() as db:
        practice = (
            db.execute(select(PracticeRow).where(PracticeRow.tenant_id == tenant_db))
            .scalars()
            .first()
        )

    if not practice:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unknown tenant database",
        )


def _resolve_schema_for_user(user_id: str) -> str | None:
    """Resolve tenant schema from user_id via platform lookup."""
    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        return None

    with create_standalone_session() as tmp:
        user_row = tmp.get(PlatformUserRow, user_id)
        if user_row:
            practice = _resolve_practice_from_email(user_row.email)
            if practice:
                return practice[1]
    return None


def _record_transcript_upload_audit(session: TherapySession, user_id: str) -> None:
    """Emit SESSION_TRANSCRIPT_UPLOADED for a completed transcript.

    The user-facing upload ROUTE audits this, but the service-to-service
    completion path calls the service method directly and bypasses that
    route-level audit. This helper is the single funnel every completion path
    runs through, so the audit can't live on a route signature.
    get_audit_service() binds to the same armed, contextvar-scoped tenant
    session the caller commits, so the row lands in the tenant audit_logs in
    the same transaction.
    """
    audit_user = get_user_repository().get(user_id)
    if audit_user is not None:
        get_audit_service().log_session_action(
            AuditAction.SESSION_TRANSCRIPT_UPLOADED,
            audit_user,
            None,
            session,
        )


def process_transcription_result(
    *,
    session_id: str,
    user_id: str,
    transcript_content: str,
    transcript_format: str = "google_meet",
) -> dict[str, str]:
    """Persist the transcript and hand SOAP generation to the shared worker.

    Shared logic used by both the HTTP callback endpoint (for external
    workers) and the in-process poll completion. This path does only the
    cheap, bounded DB work — flip the session to PROCESSING and persist the
    transcript — then enqueues the durable Cloud Tasks worker
    (``/api/internal/jobs/generate-soap``), which re-resolves the tenant from
    ``user_id`` and runs the multi-second LLM call off this request thread.
    The note is never generated inline here.
    """
    settings = get_settings()

    schema_name = _resolve_schema_for_user(user_id)
    standalone_db = create_standalone_session(practice_schema=schema_name)
    _request_session.set(standalone_db)
    # Arm the RLS GUC for this user so the SOAP session/note writes pass the
    # tenant tables' WITH CHECK policies under a NOBYPASSRLS role. This
    # off-request path doesn't go through the normal request auth, so it must
    # arm the GUC itself. Harmless when no tenant schema is set (single
    # tenant): just an unused session var.
    arm_current_user_id(standalone_db, user_id)

    try:
        # Repos read the active DB session from the request contextvar set
        # above (_request_session.set), so no session arg is passed here.
        session_repo = get_session_repository()
        patient_repo = get_patient_repository()
        soap_service = RegistryNoteGenerationService()
        # Mirror the user-facing get_session_service() wiring: SessionService
        # requires a note_service. Build it from the same plain,
        # contextvar-scoped repo factory the request path uses, so this
        # background completion path can't drift out of sync with the
        # request-scoped dependency graph.
        note_service = NoteService(get_notes_repository())
        session_service = SessionService(session_repo, patient_repo, soap_service, note_service)

        session = session_repo.get(session_id, user_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        # Already generated (or finalized): nothing to do. A late duplicate
        # poll/callback after the worker finished must not regenerate.
        if session.status in (
            SessionStatus.PENDING_REVIEW,
            SessionStatus.FINALIZED,
        ):
            logger.info(
                "Session %s already has a note (status=%s); skipping",
                session.id,
                session.status,
            )
            return {
                "id": session.id,
                "status": session.status,
                "message": "Transcript already processed.",
            }

        # Generation already handed off (a retry, or the second AssemblyAI
        # channel completing right behind the first). The transcript is
        # persisted and the session is PROCESSING; don't re-persist (that
        # would reset PROCESSING) — just ensure the worker is queued, since a
        # prior enqueue may have failed after the transcript commit.
        if session.status == SessionStatus.PROCESSING:
            logger.info(
                "Session %s already PROCESSING; ensuring generate-soap is queued",
                session.id,
            )
        else:
            # AssemblyAI completes while the session is still TRANSCRIBING;
            # prepare_transcript_session_for_generation requires
            # recording_complete/failed, so advance it first.
            if session.status == SessionStatus.TRANSCRIBING:
                session.status = SessionStatus.RECORDING_COMPLETE
                session_repo.update(session)
            elif session.status != SessionStatus.FAILED:
                logger.warning(
                    "Session %s in unexpected status %s (expected transcribing); proceeding",
                    session.id,
                    session.status,
                )

            transcript_request = UploadTranscriptToSessionRequest(
                format=transcript_format,
                content=transcript_content,
            )
            # Request-thread half only: persist the transcript + PROCESSING and
            # commit. Releases the connection before any LLM call — the note is
            # generated on the shared Cloud Tasks worker, never inline here
            # (generating inline held this request's DB connection across the
            # multi-second LLM call; an idle SSL drop then 500'd the
            # PENDING_REVIEW write and orphaned the session in PROCESSING).
            session = session_service.prepare_transcript_session_for_generation(
                session_id, user_id, transcript_request
            )
            _record_transcript_upload_audit(session, user_id)

            if standalone_db:
                standalone_db.commit()

        # Hand the multi-second SOAP generation to the durable shared worker.
        # It re-resolves the tenant schema from user_id (no schema/PHI in the
        # payload), runs the LLM off this request thread, and flips the session
        # to PENDING_REVIEW / FAILED. task_name=session_id dedupes a retry or a
        # double-channel completion within the queue's retention window.
        try:
            enqueue_cloud_task(
                queue_name=settings.soap_generation_task_queue,
                endpoint_path="/api/internal/jobs/generate-soap",
                payload={"session_id": session_id, "user_id": user_id},
                task_name=session_id,
            )
        except AlreadyExists:
            logger.info("generate-soap already enqueued for session %s (dedup)", session_id)

        logger.info(
            "Transcription callback complete: session=%s status=%s (SOAP generation queued)",
            session.id,
            session.status,
        )
        return {
            "id": session.id,
            "status": session.status,
            "message": "Transcript received. SOAP generation queued.",
        }
    except Exception:
        if standalone_db:
            standalone_db.rollback()
        raise
    finally:
        if standalone_db:
            standalone_db.close()
            _request_session.set(None)


@router.post("/api/internal/transcription-complete")
def transcription_complete(
    request: TranscriptionCompleteRequest,
    _invoker: None = Depends(require_cloud_tasks_invoker),
) -> dict[str, str]:
    """Receive a completed transcript from an external worker and start SOAP."""
    _validate_tenant_db(request.tenant_db)

    try:
        return process_transcription_result(
            session_id=request.session_id,
            user_id=request.user_id,
            transcript_content=request.transcript_content,
            transcript_format=request.transcript_format,
        )
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {request.session_id} not found",
        ) from None
    except SOAPGenerationFailedError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SOAP generation failed after transcription",
        ) from None


@router.post("/api/internal/assemblyai-submit", status_code=status.HTTP_200_OK)
def assemblyai_submit(
    request: AssemblyAiSubmitRequest,
    _invoker: None = Depends(require_cloud_tasks_invoker),
) -> dict[str, str]:
    """Submit uploaded dual-channel audio to AssemblyAI (Cloud Tasks worker).

    Reads the two channel objects the upload route streamed to object
    storage, runs the VAD split + region upload + submit off the upload
    request thread, records the job ids, and enqueues the poller. Invoked
    only by Cloud Tasks (service-account OIDC).

    Idempotent under retry: if the jobs are already recorded it just ensures
    the poller is queued. A submit failure marks the session ``failed`` and
    answers 200 so the queue does not loop a broken job — the session is then
    in a retryable status and the client can re-upload.
    """
    settings = get_settings()
    schema_name = _resolve_schema_for_user(request.user_id)

    with create_standalone_session(practice_schema=schema_name) as db:
        # Arm the RLS GUC so the tenant-scoped TherapySessionRow lookup isn't
        # fail-closed to zero rows under a NOBYPASSRLS role.
        if schema_name is not None:
            arm_current_user_id(db, request.user_id)

        session_row = (
            db.execute(
                select(TherapySessionRow).filter_by(id=request.session_id, user_id=request.user_id)
            )
            .scalars()
            .first()
        )
        if not session_row:
            logger.warning(
                "assemblyai-submit: session %s not found — dropping (non-retryable)",
                request.session_id,
            )
            return {"status": "not_found"}

        metadata = session_row.transcription_job_metadata or {}
        # Already submitted (a retry, or a double-enqueue): don't re-submit to
        # AssemblyAI — just make sure the poller is running.
        if metadata.get("jobs"):
            logger.info(
                "assemblyai-submit: session %s already submitted; ensuring poll is queued",
                request.session_id,
            )
            _enqueue_poll(request.session_id, request.user_id)
            return {"status": "already_submitted"}

        audio_path = session_row.audio_gcs_path
        if not audio_path or "," not in audio_path:
            logger.error(
                "assemblyai-submit: session %s has no dual-channel audio path",
                request.session_id,
            )
            session_row.status = SessionStatus.FAILED.value
            session_row.error = "Audio upload incomplete; cannot transcribe."
            db.commit()
            return {"status": "error"}

        therapist_object, client_object = (part.strip() for part in audio_path.split(",", 1))
        bucket = settings.transcription_audio_bucket
        storage = file_storage_from_settings(settings)

        # The prepared speech-only audio is staged back into the bucket and
        # handed to AssemblyAI as a presigned GET, so the (potentially large)
        # audio never has to be pushed through this process a second time.
        speech_objects = {
            "Therapist": f"{therapist_object}.speech.wav",
            "Client": f"{client_object}.speech.wav",
        }

        def _stage_speech_audio(speaker: str, wav_bytes: bytes) -> str:
            object_name = speech_objects[speaker]
            storage.upload_bytes(
                bucket=bucket,
                object_name=object_name,
                data=wav_bytes,
                content_type="audio/wav",
            )
            return storage.make_download_url(
                bucket=bucket,
                object_name=object_name,
                ttl_seconds=_SPEECH_AUDIO_URL_TTL_SECONDS,
            )

        try:
            therapist_bytes = storage.download_bytes(bucket=bucket, object_name=therapist_object)
            client_bytes = storage.download_bytes(bucket=bucket, object_name=client_object)
            service = AssemblyAiTranscriptionService(settings)
            jobs = asyncio.run(
                service.submit_dual_channel(
                    therapist_audio=therapist_bytes,
                    client_audio=client_bytes,
                    audio_url_factory=_stage_speech_audio,
                )
            )
        except Exception as exc:
            # Fail hard rather than loop the queue: mark the session failed and
            # answer 200. failed is a retryable upload status, so the client can
            # re-upload to try again.
            logger.exception("assemblyai-submit failed for session %s", request.session_id)
            session_row.status = SessionStatus.FAILED.value
            session_row.error = f"Transcription submit failed: {exc}"
            db.commit()
            return {"status": "error"}

        session_row.transcription_job_metadata = {"provider": "assemblyai", "jobs": jobs}
        db.commit()

    _enqueue_poll(request.session_id, request.user_id)
    logger.info(
        "assemblyai-submit: session=%s submitted %d jobs; poll queued",
        request.session_id,
        len(jobs),
    )
    return {"status": "ok"}


def _enqueue_poll(session_id: str, user_id: str) -> None:
    """Enqueue the AssemblyAI poller (HIPAA: no schema_name in payload)."""
    enqueue_cloud_task(
        queue_name=get_settings().transcription_task_queue,
        endpoint_path="/api/internal/transcription-poll",
        payload={"session_id": session_id, "user_id": user_id},
    )


def _poll_assemblyai_jobs(
    api_key: str,
    jobs: list[dict],
) -> tuple[list[tuple[dict, dict]], bool, str | None]:
    """Poll all AssemblyAI jobs. Returns (completed_results, all_complete, error_msg)."""
    completed: list[tuple[dict, dict]] = []
    for job in jobs:
        job_status, result = AssemblyAiTranscriptionService.check_job_status(
            api_key, job["transcript_id"]
        )
        if job_status == "error":
            error_msg = result.get("error", "unknown") if result else "unknown"
            return (completed, False, f"AssemblyAI job {job['transcript_id']}: {error_msg}")
        if job_status == "completed" and result is not None:
            completed.append((job, result))
    return (completed, len(completed) == len(jobs), None)


@router.post("/api/internal/transcription-poll")
def transcription_poll(
    request: TranscriptionPollRequest,
    _invoker: None = Depends(require_cloud_tasks_invoker),
) -> dict[str, str]:
    """Poll AssemblyAI for transcription completion (called by Cloud Tasks)."""
    settings = get_settings()
    schema_name = _resolve_schema_for_user(request.user_id)

    with create_standalone_session(practice_schema=schema_name) as db:
        # Arm the RLS GUC so the tenant-scoped TherapySessionRow lookup below
        # isn't fail-closed to zero rows under a NOBYPASSRLS role.
        if schema_name is not None:
            arm_current_user_id(db, request.user_id)
        session_row = (
            db.execute(
                select(TherapySessionRow).filter_by(id=request.session_id, user_id=request.user_id)
            )
            .scalars()
            .first()
        )

        if not session_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {request.session_id} not found",
            )

        job_metadata = session_row.transcription_job_metadata
        if not job_metadata or not job_metadata.get("jobs"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No transcription jobs found for this session",
            )

        api_key = settings.assemblyai_api_key.get_secret_value()
        completed_results, all_complete, error_msg = _poll_assemblyai_jobs(
            api_key, job_metadata["jobs"]
        )

        if error_msg:
            logger.error("Transcription poll failed: session=%s %s", request.session_id, error_msg)
            session_row.status = "failed"
            session_row.error = f"Transcription failed: {error_msg}"
            db.commit()
            return {"status": "error", "detail": error_msg}

        if not all_complete:
            enqueue_cloud_task(
                queue_name=settings.transcription_task_queue,
                endpoint_path="/api/internal/transcription-poll",
                payload={"session_id": request.session_id, "user_id": request.user_id},
            )
            total = len(job_metadata["jobs"])
            logger.info(
                "Transcription poll: %d/%d complete, re-enqueued: session=%s",
                len(completed_results),
                total,
                request.session_id,
            )
            return {"status": "polling", "detail": f"{len(completed_results)}/{total} complete"}

    transcript = AssemblyAiTranscriptionService.process_completed_jobs(completed_results)

    try:
        return process_transcription_result(
            session_id=request.session_id,
            user_id=request.user_id,
            transcript_content=transcript,
            # process_completed_jobs emits bracketed canonical lines
            # ("[HH:MM:SS]" on its own line, then "Speaker: text") — the
            # google_meet shape, NOT WebVTT. Labeling it "vtt" routes it to
            # the _normalize_vtt parser, which only emits on "-->" cues, finds
            # none, and returns "" — so every SOAP comes back empty ("No
            # transcript provided"). Match the label to the bytes.
            transcript_format="google_meet",
        )
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {request.session_id} not found",
        ) from None
    except SOAPGenerationFailedError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SOAP generation failed after transcription",
        ) from None
