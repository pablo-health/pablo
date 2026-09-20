# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Google Meet — the conference on the calendar event we already write.

Pablo already pushes each appointment to the clinician's Google Calendar.
Meet is not a second integration on top of that; it is one field on the event
body. The calendar service asks for a conference when the appointment's
provider is Meet, and reads the link Google issues back onto the appointment
(``app.services.google_calendar_service``).

So this adapter creates nothing and returns None. What it contributes is the
part a registry needs: a name, and an honest answer to whether this clinician
can be given a Meet link — which is exactly whether their calendar is
connected. A clinician with no calendar connection is not offered Meet,
because the conference would have no event to live on.

Cancelling is the same story from the other end. The cancellation path
deletes the calendar event, and deleting the event takes the conference with
it, so there is nothing separate here to release.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..services.telehealth import GOOGLE_MEET

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..scheduling_engine.models.appointment import Appointment
    from ..services.telehealth import Attendee, Clinician, MeetingLink, Practice

#: The conference type Google issues a Meet link for. ``eventHangout`` and
#: ``eventNamedHangout`` are the retired consumer products; asking for either
#: on a Workspace calendar gets a conference nobody can join.
#: https://developers.google.com/workspace/calendar/api/v3/reference/events
CONFERENCE_SOLUTION_TYPE = "hangoutsMeet"


class GoogleMeetProvider:
    """A Meet conference, made by the calendar event rather than by us."""

    provider_id: ClassVar[str] = GOOGLE_MEET
    display_name: ClassVar[str] = "Google Meet"

    def __init__(self, is_calendar_connected: Callable[[str], bool]) -> None:
        self._is_calendar_connected = is_calendar_connected

    def is_connected(self, clinician: Clinician) -> bool:
        return self._is_calendar_connected(clinician.id)

    def create_for_appointment(
        self,
        _appointment: Appointment,
        _clinician: Clinician,
        _practice: Practice,
        _attendee: Attendee,
    ) -> MeetingLink | None:
        """None: the calendar push creates the conference and reads it back."""
        return None

    def cancel(self, _external_id: str, _clinician: Clinician) -> bool:
        """Deleting the event removes the conference; nothing to do here."""
        return False
