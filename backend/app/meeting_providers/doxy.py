# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""doxy.me — the clinician's own waiting room.

There is no API to create anything. A clinician has one permanent room and
every session happens in it, so this adapter composes a URL rather than
calling a service, and there is nothing to cancel afterwards.

What the URL may carry is documented by the vendor:

* ``username`` pre-fills the name the patient checks in under, with ``%20``
  for a space.
* ``autocheckin=true`` puts them straight into the queue instead of asking
  them to type their name.
* ``pid`` is an identifier stored against the visit.

  https://helpcenter.doxy.me/en/articles/8273336-integrations-auto-check-in

**``pid`` is never a patient id and never an appointment id.** The vendor
calls it a patient identifier and a practice is free to put a chart number
there; Pablo puts an opaque per-appointment handle (``.pid``), because this
URL is mailed to people, sits in their browser history and lands in doxy.me's
access log. A handle that identifies the visit to us and nothing to anyone
else is the only version of that field worth sending.

The parameters only go on when the practice says its plan has the Clinic
check-in features. Without them the room URL still works — it is the same
room — the patient just types their name, which is what they do today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from ..services.telehealth import DOXY_ME, MeetingLink
from .pid import room_handle

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..services.telehealth import Attendee, Clinician, Practice


class DoxyMeProvider:
    """A clinician's doxy.me room, addressed for one appointment."""

    provider_id: ClassVar[str] = DOXY_ME
    display_name: ClassVar[str] = "Doxy.me"

    def is_connected(self, clinician: Clinician) -> bool:
        """There is a room only if the clinician has told us where it is."""
        return bool(clinician.room_url)

    def create_for_appointment(
        self,
        appointment: Appointment,
        clinician: Clinician,
        practice: Practice,
        attendee: Attendee,
    ) -> MeetingLink | None:
        if not clinician.room_url:
            return None
        handle = room_handle(appointment.id)
        return MeetingLink(
            url=compose_room_url(
                clinician.room_url,
                handle=handle,
                display_name=attendee.display_name,
                clinic_features=practice.doxy_clinic_features,
            ),
            provider=DOXY_ME,
            external_id=handle,
        )

    def cancel(self, _external_id: str, _clinician: Clinician) -> bool:
        """The room is permanent. Nothing was reserved and nothing is released."""
        return False


def compose_room_url(
    room_url: str,
    *,
    handle: str,
    display_name: str | None,
    clinic_features: bool,
) -> str:
    """The room URL a patient is given for one appointment.

    Any query the clinician already put on their room URL is dropped rather
    than merged. They pasted a room, and a stale ``username`` left on it from
    the last time they mailed somebody a link would check the wrong name into
    the queue.
    """
    parts = urlsplit(room_url.strip())
    if not clinic_features:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    params: list[tuple[str, str]] = []
    if display_name:
        params.append(("username", display_name))
    params.append(("autocheckin", "true"))
    params.append(("pid", handle))
    # ``quote`` rather than the default ``quote_plus``: the vendor documents a
    # space as %20, and a name arriving as "Mary+Anne" would be checked in
    # under a name with a plus sign in it.
    query = urlencode(params, quote_via=quote)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
