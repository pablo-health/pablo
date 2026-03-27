# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Internal API routes — service-to-service endpoints (IAM-authed, not user-facing)."""

import logging

import google.auth.transport.requests
import google.oauth2.id_token
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..database import get_admin_firestore_client, get_tenant_firestore_client
from ..models import SessionStatus, UploadTranscriptToSessionRequest
from ..repositories import FirestorePatientRepository, FirestoreTherapySessionRepository
from ..services import (
    MeetingTranscriptionSOAPService,
    SessionNotFoundError,
    SOAPGenerationFailedError,
)
from ..services.eval_export_service import EvalExportService
from ..services.pii_redaction_service import PIIRedactionService
from ..services.session_service import SessionService
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


class TranscriptionCompleteRequest(BaseModel):
    """Callback payload from the transcription worker."""

    session_id: str
    tenant_db: str
    user_id: str
    transcript_content: str = Field(min_length=1)
    transcript_format: str = "vtt"


def _verify_service_token(http_request: Request) -> None:
    """Verify the OIDC identity token from the transcription worker.

    The worker fetches a Google-signed OIDC token targeting the backend URL.
    We verify the token signature and audience to ensure the request is from
    an authorized GCP service account.
    """
    auth_header = http_request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal endpoint — requires service auth",
        )

    token = auth_header.removeprefix("Bearer ")
    settings = get_settings()

    try:
        request_adapter = google.auth.transport.requests.Request()
        claims = google.oauth2.id_token.verify_token(
            token,
            request_adapter,
            audience=settings.transcription_backend_callback_url or None,
        )
        logger.info(
            "Internal endpoint authenticated: sub=%s email=%s",
            claims.get("sub"),
            claims.get("email"),
        )
    except Exception as err:
        logger.warning("Internal endpoint OIDC verification failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired service identity token",
        ) from err


def _validate_tenant_db(tenant_db: str) -> None:
    """Validate that tenant_db corresponds to an active tenant."""
    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        return

    admin_db = get_admin_firestore_client()
    # Check all tenants to find one whose firestore_database matches
    tenants = admin_db.collection("tenants").where(
        "firestore_database", "==", tenant_db
    ).limit(1).get()

    if not tenants:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unknown tenant database",
        )

    tenant_data = tenants[0].to_dict()
    if tenant_data.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant is not active",
        )


@router.post("/api/internal/transcription-complete")
def transcription_complete(
    request: TranscriptionCompleteRequest,
    http_request: Request,
) -> dict[str, str]:
    """Receive completed transcript from worker and trigger SOAP generation.

    This endpoint is called by the transcription worker service after
    Whisper processing completes. It stores the transcript on the session
    and triggers the existing SOAP generation pipeline.

    Authentication: In production, secured via Google OIDC identity token
    from the worker's service account. In development, unauthenticated.
    """
    settings = get_settings()

    if not settings.is_development:
        _verify_service_token(http_request)

    _validate_tenant_db(request.tenant_db)

    # Build session service for this tenant
    db = get_tenant_firestore_client(request.tenant_db)
    session_repo = FirestoreTherapySessionRepository(db)
    patient_repo = FirestorePatientRepository(db)
    soap_service = MeetingTranscriptionSOAPService()
    pii_service = PIIRedactionService()
    eval_export_service = EvalExportService(pii_service, settings)
    session_service = SessionService(session_repo, patient_repo, soap_service, eval_export_service)

    # Fetch session
    session = session_repo.get(request.session_id, request.user_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {request.session_id} not found",
        )

    if session.status != SessionStatus.TRANSCRIBING:
        logger.warning(
            "Session %s in unexpected status %s (expected transcribing), proceeding anyway",
            session.id,
            session.status,
        )

    # Store transcript and trigger SOAP pipeline (reuses existing flow)
    try:
        # Temporarily allow transcribing → queued transition for this flow
        if session.status == SessionStatus.TRANSCRIBING:
            session.status = SessionStatus.RECORDING_COMPLETE
            session_repo.update(session)

        transcript_request = UploadTranscriptToSessionRequest(
            format=request.transcript_format,
            content=request.transcript_content,
        )
        session = session_service.upload_transcript_to_session(
            request.session_id, request.user_id, transcript_request
        )

        logger.info(
            "Transcription callback complete: session=%s status=%s",
            session.id,
            session.status,
        )
        return {
            "id": session.id,
            "status": session.status,
            "message": "Transcript received. SOAP note generated.",
        }

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
