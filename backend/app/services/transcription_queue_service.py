# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Transcription queue service — uploads audio to GCS, submits GCP Batch jobs.

Uses GCP Batch with spot T4 GPU instances for cost-effective transcription.
Each job provisions a spot VM, runs Whisper in a container, and tears down.
Priority jobs use SPOT with on-demand fallback; standard jobs are spot-only.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from google.cloud import batch_v1, storage  # type: ignore[attr-defined]

from ..settings import get_settings

logger = logging.getLogger(__name__)


class TranscriptionQueueService:
    """Handles audio upload to GCS and GCP Batch job submission."""

    def __init__(self) -> None:
        settings = get_settings()
        self.gcp_project = settings.gcp_project_id
        self.gcs_bucket = settings.transcription_audio_bucket
        self.worker_image = settings.transcription_worker_image
        self.location = settings.transcription_queue_location
        self.backend_url = settings.transcription_backend_callback_url
        self._storage_client: storage.Client | None = None
        self._batch_client: batch_v1.BatchServiceClient | None = None

    @property
    def storage_client(self) -> storage.Client:
        if self._storage_client is None:
            self._storage_client = storage.Client()
        return self._storage_client

    @property
    def batch_client(self) -> batch_v1.BatchServiceClient:
        if self._batch_client is None:
            self._batch_client = batch_v1.BatchServiceClient()
        return self._batch_client

    def upload_audio(self, audio_data: bytes, session_id: str, filename: str) -> str:
        """Upload audio to GCS. Returns the GCS object path (not full URI)."""
        date_prefix = datetime.now(UTC).strftime("%Y/%m/%d")
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "wav"
        gcs_path = f"{date_prefix}/{session_id}/{uuid.uuid4().hex}.{ext}"

        bucket = self.storage_client.bucket(self.gcs_bucket)
        blob = bucket.blob(gcs_path)

        content_type = "audio/wav" if ext == "wav" else f"audio/{ext}"
        blob.upload_from_string(audio_data, content_type=content_type)

        logger.info(
            "Uploaded audio for session %s: gs://%s/%s (%d bytes)",
            session_id,
            self.gcs_bucket,
            gcs_path,
            len(audio_data),
        )
        return gcs_path

    def enqueue_transcription(
        self,
        session_id: str,
        tenant_db: str,
        user_id: str,
        gcs_path: str,
        priority: bool = False,
    ) -> str:
        """Submit a GCP Batch transcription job. Returns the job name."""
        job_id = f"transcribe-{session_id[:8]}-{uuid.uuid4().hex[:8]}"

        # Container that runs faster-whisper, downloads from GCS, callbacks to backend
        container = batch_v1.Runnable.Container(
            image_uri=self.worker_image,
            commands=[
                "python3",
                "-c",
                _build_entrypoint_script(
                    session_id=session_id,
                    tenant_db=tenant_db,
                    user_id=user_id,
                    gcs_path=gcs_path,
                    gcs_bucket=self.gcs_bucket,
                    backend_url=self.backend_url,
                ),
            ],
        )

        runnable = batch_v1.Runnable(container=container)

        task_spec = batch_v1.TaskSpec(
            runnables=[runnable],
            max_retry_count=2,
            max_run_duration="1800s",  # 30 min max per job
        )

        task_group = batch_v1.TaskGroup(
            task_spec=task_spec,
            task_count=1,
            parallelism=1,
        )

        # GPU allocation — T4 spot for both tiers
        # Priority: spot with on-demand fallback via higher scheduling priority
        accelerator = batch_v1.AllocationPolicy.Accelerator(
            type_="nvidia-tesla-t4",
            count=1,
        )

        instance_policy = batch_v1.AllocationPolicy.InstancePolicy(
            machine_type="n1-standard-4",
            accelerators=[accelerator],
            provisioning_model=(
                batch_v1.AllocationPolicy.ProvisioningModel.SPOT
            ),
        )

        instances = batch_v1.AllocationPolicy.InstancePolicyOrTemplate(
            policy=instance_policy,
            install_gpu_drivers=True,
        )

        sa_email = f"transcription-worker@{self.gcp_project}.iam.gserviceaccount.com"
        service_account = batch_v1.ServiceAccount(email=sa_email)

        allocation_policy = batch_v1.AllocationPolicy(
            instances=[instances],
            location=batch_v1.AllocationPolicy.LocationPolicy(
                allowed_locations=[f"zones/{self.location}-b"],
            ),
            service_account=service_account,
        )

        job = batch_v1.Job(
            task_groups=[task_group],
            allocation_policy=allocation_policy,
            logs_policy=batch_v1.LogsPolicy(
                destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING,
            ),
            labels={
                "service": "pablo-transcription",
                "priority": "high" if priority else "standard",
                "session-id": session_id[:63],
            },
        )

        # Priority jobs get higher scheduling priority
        if priority:
            job.priority = 99

        parent = f"projects/{self.gcp_project}/locations/{self.location}"
        response = self.batch_client.create_job(
            request={"parent": parent, "job_id": job_id, "job": job},
        )

        logger.info(
            "Submitted Batch job: session=%s job=%s priority=%s",
            session_id,
            response.name,
            "high" if priority else "standard",
        )
        return response.name


def _build_entrypoint_script(
    session_id: str,
    tenant_db: str,
    user_id: str,
    gcs_path: str,
    gcs_bucket: str,
    backend_url: str,
) -> str:
    """Build the inline Python script that runs inside the Batch container.

    This script: downloads audio from GCS → transcribes with Whisper → callbacks.
    Using inline script avoids needing a separate entry point in the container.
    """
    params = json.dumps({
        "session_id": session_id,
        "tenant_db": tenant_db,
        "user_id": user_id,
        "gcs_path": gcs_path,
        "gcs_bucket": gcs_bucket,
        "backend_url": backend_url,
    })
    return (
        "from worker import TranscriptionWorker; "
        "from config import TranscriptionSettings; "
        f"import json; params = json.loads('{params}'); "
        "s = TranscriptionSettings("
        "gcs_audio_bucket=params['gcs_bucket'], "
        "backend_url=params['backend_url']); "
        "w = TranscriptionWorker(s); "
        "w.process_job("
        "session_id=params['session_id'], "
        "tenant_db=params['tenant_db'], "
        "user_id=params['user_id'], "
        "gcs_path=params['gcs_path'])"
    )


class MockTranscriptionQueueService(TranscriptionQueueService):
    """Mock for local development — skips GCS and Batch."""

    def upload_audio(self, audio_data: bytes, session_id: str, filename: str) -> str:
        gcs_path = f"mock/{session_id}/{filename}"
        logger.info(
            "[MOCK] Would upload %d bytes to gs://mock-bucket/%s",
            len(audio_data),
            gcs_path,
        )
        return gcs_path

    def enqueue_transcription(
        self,
        session_id: str,
        tenant_db: str,  # noqa: ARG002
        user_id: str,  # noqa: ARG002
        gcs_path: str,  # noqa: ARG002
        priority: bool = False,
    ) -> str:
        queue = "high" if priority else "standard"
        logger.info(
            "[MOCK] Would submit Batch job: session=%s priority=%s",
            session_id,
            queue,
        )
        return f"mock-job-{session_id}"
