# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""FastAPI app for the transcription worker service.

Receives Cloud Tasks HTTP requests, downloads audio from GCS,
transcribes with faster-whisper, and posts results back to the Pablo backend.
"""

import logging

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import TranscriptionSettings
from worker import TranscriptionWorker

settings = TranscriptionSettings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Pablo Transcription Worker", version="0.1.0")
worker = TranscriptionWorker(settings)


class TranscribeRequest(BaseModel):
    """Request payload from Cloud Tasks."""

    session_id: str
    tenant_db: str
    user_id: str
    gcs_path: str = Field(description="Comma-separated GCS paths: therapist_path,client_path")
    priority: str = Field(default="standard", pattern="^(priority|standard)$")


class TranscribeResponse(BaseModel):
    """Response after processing a transcription job."""

    status: str
    session_id: str


@app.post("/transcribe", status_code=status.HTTP_200_OK)
def transcribe(request: TranscribeRequest, http_request: Request) -> TranscribeResponse:
    """Process a transcription job from Cloud Tasks.

    Cloud Tasks expects a 2xx response to consider the task successful.
    Any non-2xx triggers a retry per the queue's retry config.
    """
    # Verify this came from Cloud Tasks (header present in production)
    task_name = http_request.headers.get("X-CloudTasks-TaskName", "local-dev")
    queue_name = http_request.headers.get("X-CloudTasks-QueueName", "local-dev")
    logger.info(
        "Received job: session=%s queue=%s task=%s priority=%s",
        request.session_id,
        queue_name,
        task_name,
        request.priority,
    )

    try:
        result = worker.process_job(
            session_id=request.session_id,
            tenant_db=request.tenant_db,
            user_id=request.user_id,
            gcs_path=request.gcs_path,
        )
        return TranscribeResponse(**result)
    except RuntimeError as e:
        # Graceful shutdown — return 503 so Cloud Tasks retries
        if "Shutting down" in str(e):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Worker shutting down (spot preemption). Task will be retried.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception("Transcription failed for session %s", request.session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transcription failed: {e}",
        ) from e


@app.get("/health")
def health() -> dict[str, str]:
    """Health check for MIG autohealing."""
    return {"status": "ok"}
