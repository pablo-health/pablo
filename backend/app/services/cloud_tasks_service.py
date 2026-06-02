# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reusable Cloud Tasks enqueue utility.

HIPAA Payload Policy: Cloud Tasks payloads MUST NOT contain schema_name,
practice_name, or any other identifying metadata that could reveal which
healthcare practice a request belongs to. Pass only opaque identifiers
(user_id, session_id) and resolve tenant context server-side.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.cloud import tasks_v2

from ..logging_config import request_id_var
from ..middleware.outbound import build_traceparent
from ..middleware.request_context import REQUEST_ID_HEADER, W3C_TRACEPARENT_HEADER
from ..settings import get_settings

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _cloud_tasks_client() -> tasks_v2.CloudTasksClient:
    """Return a cached CloudTasksClient (one gRPC channel for the process lifetime)."""
    from google.cloud import tasks_v2 as _tasks_v2

    return _tasks_v2.CloudTasksClient()


def _trace_propagation_headers() -> dict[str, str]:
    """Build trace-propagation headers from the current request context.

    Returns the headers that should ride along on the Cloud Task's HTTP
    request so the receiving handler can join its logs to the
    originating user-facing request. The receiver's
    `RequestContextMiddleware` already parses `traceparent`, so no
    handler-side code is needed.

    Empty dict when there's no bound request_id (e.g. a Cloud Task
    enqueued from a startup hook or cron).
    """
    request_id = request_id_var.get()
    if request_id is None:
        return {}
    headers = {REQUEST_ID_HEADER: request_id}
    traceparent = build_traceparent(request_id)
    if traceparent is not None:
        headers[W3C_TRACEPARENT_HEADER] = traceparent
    return headers


def enqueue_cloud_task(
    queue_name: str,
    endpoint_path: str,
    payload: dict,
    *,
    service_account_prefix: str = "cloud-tasks-invoker",
    task_name: str | None = None,
) -> None:
    """Enqueue an authenticated Cloud Task targeting an internal API endpoint.

    In development mode, logs the payload and returns (no-op).

    Args:
        queue_name: Cloud Tasks queue name (e.g., "pablo-transcription").
        endpoint_path: URL path on the backend (e.g., "/api/internal/transcription-poll").
        payload: JSON-serializable dict. Must not contain schema_name or practice_name.
        service_account_prefix: Prefix for the OIDC service account email.
        task_name: Optional task id. When set, becomes the Cloud Tasks task name so
            the queue deduplicates against it within its retention window (~1 hour).
            Cloud Tasks raises ``409 AlreadyExists`` on a duplicate; this function
            does not swallow it (the caller decides). Must match ``[A-Za-z0-9_-]+``.
    """
    settings = get_settings()
    trace_headers = _trace_propagation_headers()

    if settings.is_development:
        logger.info(
            "Dev mode: would enqueue Cloud Task to %s (payload keys: %s)",
            endpoint_path,
            list(payload.keys()),
        )
        return

    from google.cloud import tasks_v2

    client = _cloud_tasks_client()
    parent = client.queue_path(
        settings.gcp_project_id,
        settings.transcription_queue_location,
        queue_name,
    )

    backend_url = settings.transcription_backend_callback_url
    if not backend_url:
        backend_url = settings.app_url.replace(":3000", ":8000")

    task_resource_name = (
        client.task_path(
            settings.gcp_project_id,
            settings.transcription_queue_location,
            queue_name,
            task_name,
        )
        if task_name is not None
        else None
    )

    task = tasks_v2.Task(
        name=task_resource_name,
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{backend_url}{endpoint_path}",
            headers={"Content-Type": "application/json", **trace_headers},
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
