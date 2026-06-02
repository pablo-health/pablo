# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Queue abstraction for offloading heavy work to Cloud Tasks.

This is the single import point for all heavy-path callers (audio upload,
SOAP generation, document finalize). It lives in the OSS ``app`` layer so
both OSS and the SaaS overlay can import it (SaaS may import from ``app``,
never the reverse).

The current backend is Cloud Tasks via :func:`enqueue_cloud_task`. The
:class:`TaskBackend` Protocol documents the swap seam: to move to Pub/Sub
later, implement the Protocol and change a settings value — call sites and
worker routes stay untouched.
"""

from __future__ import annotations

from typing import Protocol

from ..services.cloud_tasks_service import enqueue_cloud_task


def enqueue(
    queue_name: str,
    handler_path: str,
    payload: dict,
    *,
    dedup_key: str | None = None,
) -> None:
    """Enqueue a Cloud Tasks HTTP job.

    dedup_key: when set, used as the task name so Cloud Tasks deduplicates
    within its retention window (~1 hour). Callers should swallow the 409
    that Cloud Tasks raises on a duplicate — this function does NOT swallow
    it (let the caller decide).

    Retries, backoff, maxAttempts, and dead-letter routing live in queue
    config (gcloud tasks queues update), never in app code.
    """
    enqueue_cloud_task(queue_name, handler_path, payload, task_name=dedup_key)


class TaskBackend(Protocol):
    """Swap seam for the queue backend (Cloud Tasks today, Pub/Sub later).

    Defined but not wired up — documents the interface a future backend
    must satisfy so call sites can stay backend-agnostic.
    """

    def enqueue(
        self,
        queue_name: str,
        handler_path: str,
        payload: dict,
        *,
        dedup_key: str | None = None,
    ) -> None: ...
