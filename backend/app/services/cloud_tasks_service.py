# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reusable Cloud Tasks enqueue utility.

HIPAA Payload Policy: Cloud Tasks payloads MUST NOT contain schema_name,
practice_name, or any other identifying metadata that could reveal which
healthcare practice a request belongs to. Pass only opaque identifiers
(user_id, session_id) and resolve tenant context server-side.
"""

import json
import logging

from ..settings import get_settings

logger = logging.getLogger(__name__)


def enqueue_cloud_task(
    queue_name: str,
    endpoint_path: str,
    payload: dict,
    *,
    service_account_prefix: str = "cloud-tasks-invoker",
) -> None:
    """Enqueue an authenticated Cloud Task targeting an internal API endpoint.

    In development mode, logs the payload and returns (no-op).

    Args:
        queue_name: Cloud Tasks queue name (e.g., "pablo-transcription").
        endpoint_path: URL path on the backend (e.g., "/api/internal/transcription-poll").
        payload: JSON-serializable dict. Must not contain schema_name or practice_name.
        service_account_prefix: Prefix for the OIDC service account email.
    """
    settings = get_settings()

    if settings.is_development:
        logger.info(
            "Dev mode: would enqueue Cloud Task to %s (payload keys: %s)",
            endpoint_path,
            list(payload.keys()),
        )
        return

    from google.cloud import tasks_v2

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(
        settings.gcp_project_id,
        settings.transcription_queue_location,
        queue_name,
    )

    backend_url = settings.transcription_backend_callback_url
    if not backend_url:
        backend_url = settings.app_url.replace(":3000", ":8000")

    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{backend_url}{endpoint_path}",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode(),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=(
                    f"{service_account_prefix}@{settings.gcp_project_id}.iam.gserviceaccount.com"
                ),
                audience=backend_url,
            ),
        ),
    )

    client.create_task(parent=parent, task=task)
    logger.info("Enqueued Cloud Task: queue=%s endpoint=%s", queue_name, endpoint_path)
