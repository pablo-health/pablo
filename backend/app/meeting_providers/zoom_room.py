# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Zoom without a Zoom app: the clinician's own meeting room, pasted once.

Many clinicians hold every session in one personal Zoom room. A deployment
that has registered no Zoom app cannot make a meeting per appointment, but it
can hand every video appointment the room the clinician already uses, which
is the doxy.me shape (``.doxy``) with a different vendor's address.

So this is what "Zoom" means in that deployment: the pasted room, offered
only once there is one. Where the deployment does have a Zoom app,
:class:`ZoomAppOrRoom` puts the per-meeting provider first and keeps the
pasted room for a clinician who has not connected it.

The room URL goes to the patient exactly as pasted. Unlike a
doxy.me room, a Zoom room's query string is load-bearing: ``?pwd=`` is the
passcode that lets the patient in without typing it.

There is nothing to cancel. The room is permanent and nothing was reserved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..models.validators import is_zoom_room_url
from ..services.telehealth import ZOOM, MeetingLink

__all__ = ["ZoomAppOrRoom", "ZoomRoomProvider", "is_zoom_room_url"]

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..services.telehealth import Attendee, Clinician, MeetingProvider, Practice


class ZoomRoomProvider:
    """The clinician's own Zoom room, used for every video appointment."""

    provider_id: ClassVar[str] = ZOOM
    display_name: ClassVar[str] = "Zoom"

    def is_connected(self, clinician: Clinician) -> bool:
        """There is a room only if the clinician has told us where it is."""
        return bool(clinician.zoom_room_url)

    def create_for_appointment(
        self,
        appointment: Appointment,  # noqa: ARG002 - the same room for every appointment
        clinician: Clinician,
        practice: Practice,  # noqa: ARG002
        attendee: Attendee,  # noqa: ARG002 - nothing about the patient goes in the URL
    ) -> MeetingLink | None:
        if not clinician.zoom_room_url:
            return None
        return MeetingLink(url=clinician.zoom_room_url.strip(), provider=ZOOM)

    def cancel(self, _external_id: str, _clinician: Clinician) -> bool:
        """The room is permanent. Nothing was reserved and nothing is released."""
        return False


class ZoomAppOrRoom:
    """Zoom in a deployment that has a Zoom app: a meeting per appointment first.

    A clinician who connected the app gets a meeting made for each
    appointment, and the pasted room is not used. One who has not, but pasted
    a room, gets the room. Either way the appointment is recorded as Zoom.

    Cancelling goes to the app. Only the app issues an external id, so a room
    link never reaches :meth:`cancel` (``release_meeting`` asks only when
    there is one).
    """

    provider_id: ClassVar[str] = ZOOM
    display_name: ClassVar[str] = "Zoom"

    def __init__(self, app: MeetingProvider, room: ZoomRoomProvider | None = None) -> None:
        self._app = app
        self._room = room or ZoomRoomProvider()

    def is_connected(self, clinician: Clinician) -> bool:
        return self._app.is_connected(clinician) or self._room.is_connected(clinician)

    def create_for_appointment(
        self,
        appointment: Appointment,
        clinician: Clinician,
        practice: Practice,
        attendee: Attendee,
    ) -> MeetingLink | None:
        if self._app.is_connected(clinician):
            return self._app.create_for_appointment(appointment, clinician, practice, attendee)
        return self._room.create_for_appointment(appointment, clinician, practice, attendee)

    def cancel(self, external_id: str, clinician: Clinician) -> bool:
        return self._app.cancel(external_id, clinician)
