# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a booking link can take a booking, and if not, why.

A booking link books exactly one appointment type, and a stranger holding the
link is a new client by definition. So a link is bookable only when every
switch that governs a new client self-booking that type says yes:

* the practice allows new clients to book themselves at all
  (``scheduling_policy.self_book_new``);
* the type is for new clients (``audience`` is ``new`` or ``both``);
* the type is one clients may take without the clinician in the loop
  (``self_bookable``);
* the type is one Pablo offers times for at all (``offerable``).

Two audiences read the answer differently, and both read it from here so they
can never disagree. The public surface refuses a non-bookable link with the
same response as a missing one: a booker is told nothing about which switch is
off, or whether the type exists. The owner sees the reason in plain words,
because the most likely outcome of a silent refusal is a therapist sending a
dead link and never learning why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .scheduling_policy import may_self_book

if TYPE_CHECKING:
    from ..models.appointment_type import AppointmentType

REASON_TYPE_MISSING = (
    "The appointment type this link books no longer exists. Edit the link and pick another."
)
REASON_PRACTICE_CLOSED = (
    "Your practice does not let new clients book themselves. "
    "Turn that on under Scheduling to open this link."
)
REASON_EXISTING_ONLY = (
    "This appointment type is for existing clients only, "
    "and a booking link is how a new client reaches you."
)
REASON_NOT_SELF_BOOKABLE = "This appointment type is not marked as bookable by clients."
REASON_NOT_OFFERABLE = "This appointment type is switched off, so no times are offered for it."


@dataclass(frozen=True)
class LinkBookability:
    """The answer, with the owner-facing reason when it is no."""

    bookable: bool
    reason: str | None = None


def assess_link(
    appointment_type: AppointmentType | None, policy: dict[str, object]
) -> LinkBookability:
    """Decide whether a link booking ``appointment_type`` can take a booking.

    ``policy`` is the practice's scheduling policy as ``load_policy`` returns
    it, defaults included. The checks run in the order a therapist would fix
    them: a missing type first (nothing else can be judged), then the
    practice-wide switch, then the three per-type switches.
    """
    if appointment_type is None:
        return LinkBookability(False, REASON_TYPE_MISSING)
    if not may_self_book(policy, is_new_patient=True):
        return LinkBookability(False, REASON_PRACTICE_CLOSED)
    if appointment_type.audience == "existing":
        return LinkBookability(False, REASON_EXISTING_ONLY)
    if not appointment_type.self_bookable:
        return LinkBookability(False, REASON_NOT_SELF_BOOKABLE)
    if not appointment_type.offerable:
        return LinkBookability(False, REASON_NOT_OFFERABLE)
    return LinkBookability(True)
