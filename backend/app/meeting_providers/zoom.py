# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Zoom — a meeting per appointment, in the clinician's own Zoom account.

The clinician connects their account once; after that every telehealth
appointment gets its own meeting rather than everybody joining one personal
room. That is the point of using the API at all: a personal meeting room has
one link for ever, so a client who kept last month's message can walk into
this afternoon's session.

Three settings are chosen here rather than left to the account's defaults,
because the account default is whatever the clinician's plan shipped with and
the wrong value is not visible until somebody is in the room who should not
be:

* ``waiting_room`` **on** — nobody is in the room until the clinician admits
  them, which is what stops the next client joining the tail of this one.
* ``join_before_host`` **off** — the same guarantee from the other side.
* ``meeting_authentication`` **off** — requiring a Zoom login to join would
  turn "click the link" into "make an account", and a client who cannot get
  in has not been protected.

**The topic never carries the patient's name.** A Zoom account's meeting list
is visible to whoever can see that account, it appears in calendar invitations
the vendor generates, and it is read by whatever else the clinician has
plugged into Zoom. The appointment already knows who it is with; the meeting
does not need to.

Pablo retrieves no Zoom recording, ever. Session audio is captured on the
clinician's own machine, inside Pablo's boundary, the same way for every
provider — which is why ``auto_recording`` is left alone rather than set: it
is the account's business and nothing here reads what it produces.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING, ClassVar, Protocol

from ..calendar_providers.event_titles import DEFAULT_EVENT_SUMMARY
from ..services.telehealth import ZOOM, MeetingLink, TelehealthError
from .zoom_client import (
    MEETING_TYPE_SCHEDULED,
    ZoomError,
    ZoomGrant,
    api_request,
    refresh_grant,
)

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..services.telehealth import Attendee, Clinician, Practice

logger = logging.getLogger(__name__)


class ZoomConnectionStore(Protocol):
    """Where a clinician's Zoom grant lives between requests."""

    def get(self, user_id: str) -> ZoomGrant | None: ...

    def save(self, user_id: str, grant: ZoomGrant) -> None: ...

    def delete(self, user_id: str) -> bool: ...


class ZoomProvider:
    """A scheduled Zoom meeting, in the clinician's own account."""

    provider_id: ClassVar[str] = ZOOM
    display_name: ClassVar[str] = "Zoom"

    def __init__(
        self,
        *,
        store: ZoomConnectionStore,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._store = store
        self._client_id = client_id
        self._client_secret = client_secret

    def is_connected(self, clinician: Clinician) -> bool:
        return self._store.get(clinician.id) is not None

    def create_for_appointment(
        self,
        appointment: Appointment,
        clinician: Clinician,
        _practice: Practice,
        _attendee: Attendee,
    ) -> MeetingLink | None:
        grant = self._usable_grant(clinician.id)
        if grant is None:
            return None
        try:
            created = api_request(
                "POST",
                "/users/me/meetings",
                access_token=grant.access_token,
                json_body=meeting_body(appointment),
            )
        except ZoomError as exc:
            raise TelehealthError("Zoom could not create the meeting") from exc

        join_url = str(created.get("join_url") or "")
        meeting_id = created.get("id")
        if not join_url or meeting_id is None:
            raise TelehealthError("Zoom created a meeting without a link")
        return MeetingLink(url=join_url, provider=ZOOM, external_id=str(meeting_id))

    def reschedule(self, appointment: Appointment, clinician: Clinician) -> bool:
        """Move the existing meeting to the appointment's new time.

        False when there is nothing to move, which covers both an appointment
        with no Zoom meeting and a clinician who has since disconnected.
        """
        if not appointment.meeting_external_id:
            return False
        grant = self._usable_grant(clinician.id)
        if grant is None:
            return False
        try:
            api_request(
                "PATCH",
                f"/meetings/{appointment.meeting_external_id}",
                access_token=grant.access_token,
                json_body={
                    "start_time": zoom_start_time(appointment),
                    "duration": appointment.duration_minutes,
                    "timezone": "UTC",
                },
            )
        except ZoomError:
            logger.warning("zoom_reschedule_failed")
            return False
        return True

    def cancel(self, external_id: str, clinician: Clinician) -> bool:
        """Delete the meeting from the account that created it.

        False when the clinician has since disconnected, which is the right
        answer rather than an error: the grant that could delete it is gone,
        and the appointment is being cancelled either way.
        """
        grant = self._usable_grant(clinician.id)
        if grant is None:
            return False
        try:
            api_request("DELETE", f"/meetings/{external_id}", access_token=grant.access_token)
        except ZoomError:
            logger.warning("zoom_cancel_failed")
            return False
        return True

    def _usable_grant(self, user_id: str) -> ZoomGrant | None:
        """The stored grant, refreshed if it is spent. None if not connected.

        The refreshed grant is stored before it is used, because Zoom retires
        the refresh token it was presented with: a refresh that succeeds and
        is not stored leaves a connection that can never refresh again.
        """
        grant = self._store.get(user_id)
        if grant is None:
            return None
        if not grant.is_expired():
            return grant
        try:
            refreshed = refresh_grant(
                client_id=self._client_id,
                client_secret=self._client_secret,
                grant=grant,
            )
        except ZoomError:
            logger.warning("zoom_refresh_failed")
            return None
        self._store.save(user_id, refreshed)
        return refreshed


def zoom_start_time(appointment: Appointment) -> str:
    """The start, as Zoom's ``yyyy-MM-ddTHH:mm:ssZ``.

    Sent in UTC with ``timezone`` saying so, rather than in the practice's
    zone, because a daylight-saving change between booking and session would
    otherwise move the meeting an hour away from the appointment.
    """
    return appointment.start_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def meeting_body(appointment: Appointment) -> dict[str, object]:
    """The create/update body for one appointment.

    Its own function so the choices above can be asserted without a Zoom
    account: a test reads this and sees the waiting room on, join-before-host
    off, and a topic with nobody's name in it.
    """
    return {
        "topic": DEFAULT_EVENT_SUMMARY,
        "type": MEETING_TYPE_SCHEDULED,
        "start_time": zoom_start_time(appointment),
        "duration": appointment.duration_minutes,
        "timezone": "UTC",
        "settings": {
            "waiting_room": True,
            "join_before_host": False,
            "meeting_authentication": False,
        },
    }
