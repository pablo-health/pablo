# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Booking through a single-slot link: one window, once.

A single-slot link is a booking link scoped to one slot on the clinician's
diary — typically a time somebody gave back — and spent by its first booking.
Two visitors arriving together must produce exactly one appointment, so the
claim and the booking happen in one transaction:

1. **Claim the link.** A conditional UPDATE on the link row
   (``BookingLinkRepository.claim_slot``). Its row lock is held until the
   transaction ends, so a second visitor waits there and then finds the link
   spent.
2. **Check the slot is still free**, with the engine's own collision check, so
   a time somebody else booked directly is refused before a patient record is
   written for it.
3. **Book it.** ``create_appointment`` runs the collision check again, and the
   unique active-slot index backs it, for a booking that lands between 2 and 3.

A refused claim, a collision or the index all mean the same thing to the
visitor — the time has been taken — and surface as :class:`SlotTakenError`.

The practice's ``self_book_mode`` decides what the booking is: ``request`` (the
default) holds the time as a PENDING request the clinician confirms, expiring
after ``pending_hold_hours`` or at the slot itself, whichever is sooner;
``auto`` books it outright. Availability rules are not applied: the clinician
chose this exact time when they made the link.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..scheduling_engine.exceptions import AppointmentConflictError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models.booking_link import BookingLink
    from ..repositories.booking_link import BookingLinkRepository
    from ..repositories.row import Row
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.models.appointment_type import AppointmentType
    from ..scheduling_engine.services.scheduling import SchedulingService


class SlotTakenError(Exception):
    """The link's one slot is no longer available: used, passed, or booked."""


def slot_window(link: BookingLink) -> tuple[datetime, datetime]:
    """The window a single-slot link books."""
    if link.slot_start_at is None or link.slot_end_at is None:
        raise ValueError("not a single-slot link")
    return link.slot_start_at, link.slot_end_at


def is_slot_gone(link: BookingLink, now: datetime) -> bool:
    """Whether a visitor arriving now can no longer have the link's slot."""
    start, _ = slot_window(link)
    return link.slot_claimed_at is not None or start <= now


def book_slot_link(
    link: BookingLink,
    *,
    link_repo: BookingLinkRepository,
    scheduling: SchedulingService,
    appointment_type: AppointmentType,
    policy: Row,
    patient_for_booking: Callable[[], str],
    notes: str,
    now: datetime,
) -> Appointment:
    """Claim the link and book its slot. Raises :class:`SlotTakenError`.

    ``patient_for_booking`` is called only once the slot is known to be free,
    and returns the id of the patient record the booking is made for; nothing
    is written for a visitor who is turned away.
    """
    start, end = slot_window(link)
    if start <= now or not link_repo.claim_slot(link.id):
        raise SlotTakenError
    try:
        scheduling.reject_if_taken(link.user_id, start, end)
    except AppointmentConflictError as e:
        raise SlotTakenError from e

    data: dict[str, str | int | datetime | None] = {
        "patient_id": patient_for_booking(),
        "title": link.title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": int((end - start).total_seconds() // 60),
        "session_type": appointment_type.name,
        "appointment_type_id": appointment_type.id,
        "notes": notes,
    }
    if policy.get("self_book_mode") != "auto":
        hold_hours = int(
            policy["pending_hold_hours"]
        )  # policy values are typed object; the column is an int
        data["status"] = "pending"
        data["pending_expires_at"] = min(now + timedelta(hours=hold_hours), start)

    # Whether the check saw the clash or the database's no-overlap constraint
    # caught a racing insert, the repository raises the same error.
    try:
        return scheduling.create_appointment(link.user_id, data=data)
    except AppointmentConflictError as e:
        raise SlotTakenError from e
