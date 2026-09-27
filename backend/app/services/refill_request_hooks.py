# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deployment-configurable processing after a refill request is made or decided.

The same seam as :mod:`app.services.patient_message_hooks`, for the same
reason: some deployments want something to happen next — screen what the
patient wrote, put the request in front of whoever works the practice's
queue, tell the patient their request was answered — and none of that
belongs in the engine. **The default is no callbacks at all.**

One registry, one event, two moments. ``kind`` says which: ``submitted``
when a patient asks, ``decided`` when a prescriber answers. A callback that
cares about one ignores the other.

The load-bearing properties are the messaging seam's, restated because each
is a bug somebody has already had:

* **Plain arguments, never a session or an ORM row.** A callback that needs
  the database opens its own session in the schema the event names.
* **A raising callback never fails the request.** The patient's request is
  stored, or the prescriber's decision is, before any callback runs.
* **Nothing PHI-shaped is logged.** The event carries the medication and the
  patient's note because a callback screening them needs them. This module
  logs neither — only the request id and the failing callback's type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)

RefillRequestEventKind = Literal["submitted", "decided"]


@dataclass(frozen=True)
class RefillRequestEvent:
    """A refill request at the moment it was made or decided, in plain values.

    ``practice_schema`` is ``None`` only where the request that produced the
    event had no resolved schema; a callback should skip it rather than guess.
    ``decided_at`` is set on ``decided`` events and ``None`` on ``submitted``.
    """

    kind: RefillRequestEventKind
    practice_schema: str | None
    request_id: str
    patient_id: str
    status: str
    medication_text: str
    patient_note: str | None
    created_at: datetime
    decided_at: datetime | None = None


@runtime_checkable
class RefillRequestHook(Protocol):
    """A callback invoked once per event.

    Runs after the response on a worker thread, with no request, no tenant
    context and no open session.
    """

    def __call__(self, event: RefillRequestEvent) -> None: ...


class RefillRequestHookRegistry:
    """The callbacks a deployment has registered, in registration order."""

    def __init__(self) -> None:
        self._hooks: list[RefillRequestHook] = []

    def register(self, hook: RefillRequestHook) -> None:
        self._hooks.append(hook)

    def clear(self) -> None:
        """Drop every registered callback. For test isolation."""
        self._hooks.clear()

    @property
    def hooks(self) -> tuple[RefillRequestHook, ...]:
        return tuple(self._hooks)


_registry = RefillRequestHookRegistry()


def get_refill_request_hook_registry() -> RefillRequestHookRegistry:
    """The process-wide registry. A startup handle."""
    return _registry


def dispatch_refill_request_event(event: RefillRequestEvent) -> None:
    """Hand *event* to every registered callback, surviving each one."""
    for hook in _registry.hooks:
        try:
            hook(event)
        except Exception:
            # Type name and request id only (guardrail #5): the event carries
            # a medication and a note, and a traceback can carry them too.
            logger.warning(
                "refill request hook failed: hook=%s kind=%s request_id=%s",
                type(hook).__name__,
                event.kind,
                event.request_id,
            )
