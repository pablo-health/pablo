# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deployment-configurable post-message processing.

A message lands in the store and, for some deployments, something should
happen next — a practice that routes incoming messages to whoever is on
duty, one that files them beside the rest of its correspondence, one that
runs its own triage. None of that belongs in the engine, and all of it
needs the same thing: to be told, once, that a message was written.

So the engine offers a seam rather than a feature. A deployment registers a
callback here at startup; the send routes hand every registered callback the
message that was just stored. **The default is no callbacks at all**, which
is what a self-hosted install gets: plain secure messaging, nothing
downstream, no configuration to discover.

Three properties are load-bearing, and each one is a bug somebody else has
already had:

* **Plain arguments, never a session or an ORM row.** :class:`PatientMessageEvent`
  is a frozen dataclass of strings and datetimes. A callback that needs the
  database opens its own session and sets its own ``search_path``; the
  request's session is closed by the time it runs, and a detached ORM row
  would either raise or silently lazy-load against whatever schema the
  borrowed connection happened to be pointing at.
* **A raising callback never fails the send.** A patient's message may be
  the one that matters most, and it must not bounce off something
  downstream. Exceptions are caught, logged by handle, and the next
  callback still runs.
* **Nothing PHI-shaped is logged.** The event carries a subject and a body
  because a callback needs them. This module logs neither — only the
  message id and the failing callback's type.

Registration is a deployment-level statement, so it happens at startup, not
per request. :meth:`PatientMessageHookRegistry.clear` exists for tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatientMessageEvent:
    """One stored message, described in plain values.

    ``practice_schema`` is here because a callback runs outside the request
    that produced it: there is no tenant context left to read, so the event
    has to say which schema the ids belong to. It is ``None`` only where the
    clinician's own request had no resolved schema, which a callback should
    treat as "I cannot place these ids" and skip, rather than guessing at a
    default and writing into the wrong tenant.

    ``sender`` is carried so a callback can tell a patient's message from
    the practice's own reply — both are dispatched, because a deployment
    that mirrors messages somewhere needs the whole conversation, not half
    of it.
    """

    practice_schema: str | None
    patient_id: str
    thread_id: str
    message_id: str
    sender: str
    subject: str | None
    body: str
    created_at: datetime


@runtime_checkable
class PatientMessageHook(Protocol):
    """A callback invoked once per stored message.

    Implementations must be safe to call from a worker thread with no
    request, no tenant context and no open session, and should not block for
    long — the dispatch runs after the response, but it still runs on the
    server.
    """

    def __call__(self, event: PatientMessageEvent) -> None: ...


class PatientMessageHookRegistry:
    """The callbacks a deployment has registered, in registration order.

    Order is preserved because it is the only thing a deployment can use to
    express precedence, and a ``set`` would silently reorder on every run.
    """

    def __init__(self) -> None:
        self._hooks: list[PatientMessageHook] = []

    def register(self, hook: PatientMessageHook) -> None:
        self._hooks.append(hook)

    def clear(self) -> None:
        """Drop every registered callback. For test isolation."""
        self._hooks.clear()

    @property
    def hooks(self) -> tuple[PatientMessageHook, ...]:
        return tuple(self._hooks)


_registry = PatientMessageHookRegistry()


def get_patient_message_hook_registry() -> PatientMessageHookRegistry:
    """The process-wide registry. A FastAPI dependency and a startup handle."""
    return _registry


def dispatch_patient_message(event: PatientMessageEvent) -> None:
    """Hand *event* to every registered callback, surviving each one.

    Called off the request via ``BackgroundTasks``, so it runs after the
    handler has returned and the message is stored. A callback that raises
    is logged and skipped; the rest still run, and the send has already
    succeeded either way.
    """
    for hook in _registry.hooks:
        try:
            hook(event)
        except Exception:
            # Type name and message id only. The event carries a body; a
            # traceback from a callback can carry it too, and this log is
            # not where either belongs (guardrail #5).
            logger.warning(
                "patient message hook failed: hook=%s message_id=%s",
                type(hook).__name__,
                event.message_id,
            )
