# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An appointment type remembers the place of service it was last booked with.

The only place the learning rule is written down. Every surface that writes
a place of service onto an appointment (booking, editing, coding the visit
while writing its note) calls :func:`remember_place` after the write, so a
type learns the same way whichever screen the clinician entered the code on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ..models.appointment_type import REMEMBERED_PLACES, RememberedPlace

if TYPE_CHECKING:
    from ..models.appointment import Appointment
    from ..repositories.appointment_type import AppointmentTypeRepository


def remember_place(
    type_repo: AppointmentTypeRepository, user_id: str, appointment: Appointment
) -> None:
    """Record the appointment's place of service on its type, if it names one.

    The exact code is kept: a visit coded 10 teaches 10, not "video". Only
    11, 02 and 10 are remembered; any other code, or none, teaches the type
    nothing. Call it after the write succeeded, so a refused booking or edit
    teaches nothing.
    """
    place = appointment.place_of_service
    if appointment.appointment_type_id and place in REMEMBERED_PLACES:
        type_repo.record_place(
            appointment.appointment_type_id, user_id, cast("RememberedPlace", place)
        )
