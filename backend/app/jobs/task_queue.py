# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Queue abstraction for offloading heavy work to Cloud Tasks.

This is the single import point for all heavy-path callers (audio upload,
SOAP generation, document finalize). It lives in the OSS ``app`` layer so
both OSS and a downstream deployment's overlay can import it (the overlay
may import from ``app``, never the reverse).

The current backend is Cloud Tasks via :func:`enqueue_cloud_task`. To swap
to Pub/Sub later, replace the call inside :func:`enqueue` and update queue
config — call sites and worker routes stay untouched.
"""

from __future__ import annotations

import re

# Cloud Tasks task name constraint: https://cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks
_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,500}$")


def enqueue(
    queue_name: str,
    handler_path: str,
    payload: dict,
    *,
    dedup_key: str | None = None,
) -> None:
    """Enqueue a Cloud Tasks HTTP job.

    dedup_key: when set, used as the task name so Cloud Tasks deduplicates
    within its retention window (~1 hour). Must match [A-Za-z0-9_-]{1,500}
    — hash or slugify raw keys (UUIDs with hyphens are fine; colons, slashes,
    and dots are not). Cloud Tasks raises 409 AlreadyExists on a duplicate;
    this function does NOT swallow it (let the caller decide).

    Retries, backoff, maxAttempts, and dead-letter routing live in queue
    config (gcloud tasks queues update), never in app code.
    """
    if dedup_key is not None and not _TASK_NAME_RE.match(dedup_key):
        raise ValueError(f"dedup_key must match [A-Za-z0-9_-]{{1,500}}, got: {dedup_key!r}")

    # Imported lazily to avoid a circular import. This module is the single
    # import point for heavy-path callers, so services import it — and
    # ``app.services.__init__`` eagerly imports the whole service layer. A
    # module-level import here would mean importing task_queue first runs
    # that __init__, which re-enters this module before ``enqueue`` exists.
    from ..services.cloud_tasks_service import enqueue_cloud_task

    enqueue_cloud_task(queue_name, handler_path, payload, task_name=dedup_key)
