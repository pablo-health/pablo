# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The open-slot event: a client gave a time back, and it is free again.

When a client cancels their own appointment the time it held becomes free, and
a deployment may want to act on that — offer it to someone waiting for an
earlier time, put it on a list, count it. This module raises that fact as an
event and lets a deployment subscribe to it in-process. It decides nothing
about who should hear about the slot and sends nothing to anyone.

**Only a client's own cancellation opens a slot.** A clinician cancelling has
decided what to do with their own hour; a hold that lapsed was never a booked
appointment; a cancellation the system made (a calendar sync, a sweep) is
bookkeeping. None of those raise anything. A client MOVING an appointment does
not raise it either: the cancelled half of a move is linked to its
replacement, which may sit in an overlapping time, so the old slot is not
necessarily free.

**Subscribers hear about it after the cancellation has committed.** The caller
publishes once its transaction is done, so a subscriber never acts on a
cancellation that was rolled back. A subscriber that raises is logged by id and
skipped; it cannot undo the cancellation or stop the next subscriber.

**Nothing here carries a person.** The event names the appointment, the
clinician and the slot — never the client, and never the client's id, because
the slot is free and whoever held it no longer matters to it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Literal

from ..models.appointment import AppointmentStatus, CancellationActor

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ..models.appointment import Appointment

logger = logging.getLogger(__name__)

Modality = Literal["telehealth", "in_person"]


@dataclass(frozen=True)
class OpenSlot:
    """A time a client gave back, and what kind of appointment it held."""

    #: The cancelled appointment the slot came from.
    appointment_id: str
    #: The clinician whose diary the slot is in.
    clinician_id: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    #: The appointment type it was booked as; None when that is not known.
    appointment_type_id: str | None
    #: The type's name as booked.
    session_type: str
    modality: Modality


_handlers: list[Callable[[OpenSlot], None]] = []
_lock = Lock()


def subscribe(handler: Callable[[OpenSlot], None]) -> Callable[[], None]:
    """Call ``handler`` with every open slot from now on. Returns an unsubscribe."""
    with _lock:
        _handlers.append(handler)

    def unsubscribe() -> None:
        with _lock:
            if handler in _handlers:
                _handlers.remove(handler)

    return unsubscribe


def modality_of(appointment: Appointment) -> Modality:
    """Whether an appointment was held by video or in person.

    There is no modality column on an appointment, so it is derived: one that
    carries a meeting link or a meeting provider was a telehealth visit, and
    anything else was in person. ``provider`` is set when the engine issued the
    meeting and ``video_link`` when a clinician pasted one; either is enough.
    """
    return "telehealth" if appointment.video_link or appointment.provider else "in_person"


def open_slot_for(appointment: Appointment) -> OpenSlot | None:
    """The open slot a cancelled appointment leaves, or None if it leaves none.

    The one rule for which cancellations count: a cancelled row that the client
    cancelled outright. Every other row — still booked, cancelled by a
    clinician or by the system, cancelled before anyone recorded who did it,
    or the given-up half of a move — answers None.
    """
    if appointment.status != AppointmentStatus.CANCELLED:
        return None
    if appointment.cancelled_by != CancellationActor.PATIENT:
        return None
    if appointment.superseded_by_id is not None:
        return None
    return OpenSlot(
        appointment_id=appointment.id,
        clinician_id=appointment.user_id,
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        duration_minutes=appointment.duration_minutes,
        appointment_type_id=appointment.appointment_type_id,
        session_type=appointment.session_type,
        modality=modality_of(appointment),
    )


def publish(slot: OpenSlot) -> None:
    """Hand ``slot`` to every subscriber, in the order they subscribed.

    Call this after the cancellation has committed. A subscriber that raises is
    logged with the appointment id alone and the rest still run.
    """
    with _lock:
        handlers = list(_handlers)
    for handler in handlers:
        try:
            handler(slot)
        except Exception:
            logger.exception(
                "open-slot subscriber failed: appointment_id=%s handler=%s",
                slot.appointment_id,
                getattr(handler, "__qualname__", type(handler).__name__),
            )
