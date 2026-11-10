# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where an appointment gets its video room, and when its link is worth showing.

Pablo hosts no video. A practice already has a room — a Google Meet through
the calendar it already keeps, a Zoom account it already pays for, a doxy.me
waiting room its clients already recognise — and this is the seam that asks
that room for a link and remembers which room answered.

Three things live here and nothing else:

* **The contract** a meeting provider satisfies (:class:`MeetingProvider`)
  and what it hands back (:class:`MeetingLink`). Vendor detail — API shapes,
  scope strings, URL parameters — stays behind an implementation in
  ``app.meeting_providers`` and never appears above this line.
* **Who answers.** A registry keyed by provider id, narrowed by what the
  deployment offers and by what the clinician has actually connected. A
  provider nobody has connected is not offered, because offering it would
  produce an appointment whose link never arrives.
* **When a link is worth showing.** The join window is one rule, computed in
  one place, so the portal, the clinician's diary and a reminder cannot
  disagree about whether an appointment is joinable.

**A link the clinician typed always wins.** Whatever the preference says and
whatever a vendor would have issued, a URL pasted onto this appointment is
the one the patient gets. It is the escape hatch for every room Pablo has
never heard of, and it is also what a clinician reaches for when a vendor is
having a bad morning — so nothing here is allowed to overwrite it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, runtime_checkable
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from ..models.scheduling import TELEHEALTH_PLACES, is_telehealth
from ..scheduling_engine.models.appointment import AppointmentStatus
from ..settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ..models.user import UserPreferences
    from ..scheduling_engine.models.appointment import Appointment

logger = logging.getLogger(__name__)

#: Provider ids, which are also the registry keys and the value stored in
#: ``appointments.provider``. Short and stable: the column is VARCHAR(16) and
#: these strings outlive whatever a vendor renames itself to.
GOOGLE_MEET: Final = "google_meet"
ZOOM: Final = "zoom"
DOXY_ME: Final = "doxy_me"
MANUAL: Final = "manual"

PROVIDER_IDS: Final[tuple[str, ...]] = (GOOGLE_MEET, ZOOM, DOXY_ME, MANUAL)

#: How the free-text labels a practice already has map onto provider ids.
#:
#: ``video_platform`` predates any of this: it is a label a clinician picked
#: from a dropdown, and rows carry whatever the dropdown offered at the time.
#: Reading it as a provider is a convenience for a practice that has been
#: choosing "Zoom" for a year; anything unrecognised is MANUAL, which is the
#: honest answer — the practice has a room and Pablo does not know it.
_LABEL_ALIASES: Final[Mapping[str, str]] = {
    "google_meet": GOOGLE_MEET,
    "googlemeet": GOOGLE_MEET,
    "meet": GOOGLE_MEET,
    "hangouts": GOOGLE_MEET,
    "zoom": ZOOM,
    "doxy": DOXY_ME,
    "doxy_me": DOXY_ME,
    "doxy.me": DOXY_ME,
    "doxyme": DOXY_ME,
}

DEFAULT_JOIN_WINDOW_MINUTES: Final = 15
"""How long before the start a link is offered when nothing says otherwise.

Early enough that somebody punctual finds the button waiting, late enough
that it is not sitting on their screen all week.
"""


class TelehealthError(RuntimeError):
    """A provider could not produce a meeting. The appointment still stands."""


@dataclass(frozen=True)
class MeetingLink:
    """A room to join, and who to ask about it afterwards.

    ``external_id`` is the vendor's handle — a Zoom meeting id, the opaque
    per-appointment handle a doxy.me URL carries — or None for a provider that
    issues none. It is what :meth:`MeetingProvider.cancel` is given and what a
    vendor webhook is matched against, so it must be stable for the life of
    the appointment and must never be derived from anything about the patient.
    """

    url: str
    provider: str
    external_id: str | None = None


@dataclass(frozen=True)
class Clinician:
    """The clinician's side of a meeting, as a provider needs to see it.

    A deliberately small view rather than the user model: an adapter that
    could reach a ``User`` could reach their caseload, and none of them has
    any business doing so. Assembled by :func:`clinician_from_preferences`.
    """

    id: str
    #: The provider this clinician prefers, from their session defaults.
    preferred_provider: str | None = None
    #: Their own permanent room, for a provider that has one (doxy.me). The
    #: clinician pastes it once in settings; nothing derives it.
    room_url: str | None = None
    #: Their own Zoom meeting room, pasted once, for a deployment that cannot
    #: make a Zoom meeting per appointment.
    zoom_room_url: str | None = None


def clinician_from_preferences(user_id: str, preferences: UserPreferences) -> Clinician:
    """The clinician's telehealth preferences, as a provider sees them.

    One place, so a new pasted room reaches every path that books a video
    appointment rather than only the ones somebody remembered to update.
    """
    return Clinician(
        id=user_id,
        preferred_provider=preferences.default_video_platform,
        room_url=preferences.telehealth_room_url,
        zoom_room_url=preferences.zoom_room_url,
    )


@dataclass(frozen=True)
class Practice:
    """The practice's side: the settings a provider is allowed to read."""

    #: Whether the practice's doxy.me plan includes the Clinic check-in
    #: features, which decides whether its room URLs carry parameters.
    doxy_clinic_features: bool = False


@dataclass(frozen=True)
class Attendee:
    """The one thing about the patient a video room is allowed to know.

    A waiting room that checks somebody in has to have something to call
    them, so a display name is the minimum a room like doxy.me's needs to
    work at all — and it is the maximum any of these adapters ever gets. No
    id, no surname, no date of birth, nothing that would identify the person
    to somebody reading the vendor's access log over their shoulder. The
    caller decides what goes in it; today that is the first name a patient
    gave, and every caller should keep it that way.

    Empty when the appointment has no patient name to offer, which a provider
    must handle rather than assume away.
    """

    display_name: str | None = None


@runtime_checkable
class MeetingProvider(Protocol):
    """A place a session can be held, whoever runs it."""

    provider_id: ClassVar[str]
    display_name: ClassVar[str]

    def is_connected(self, clinician: Clinician) -> bool:
        """Whether this clinician can actually be given a room right now.

        False is not an error and not a misconfiguration — it is a clinician
        who has not connected Zoom, or not pasted their doxy.me room. A
        provider answering False is left out of what the surface offers.
        """
        ...

    def create_for_appointment(
        self,
        appointment: Appointment,
        clinician: Clinician,
        practice: Practice,
        attendee: Attendee,
    ) -> MeetingLink | None:
        """Get a room for this appointment.

        ``None`` means "no link from me, and that is fine": Google Meet
        answers None because the calendar event creates the conference and
        reads the link back, so the link arrives moments later by another
        path. Raise :class:`TelehealthError` for a genuine failure.
        """
        ...

    def cancel(self, external_id: str, clinician: Clinician) -> bool:
        """Release a meeting this provider issued. False if it could not.

        The clinician is here because the meeting lives in their account, and
        releasing it takes the same connection that made it. A provider that
        issued nothing answers False, which is not a failure.
        """
        ...


class MeetingProviderRegistry:
    """The providers this process knows how to talk to.

    An instance rather than a module-level dict: the adapters need clients and
    credentials, so the registry is assembled per request from settings and
    repositories (``app.meeting_providers.registry.build_registry``) exactly
    as the calendar registry is.
    """

    def __init__(self, providers: Iterable[MeetingProvider] = ()) -> None:
        self._providers: dict[str, MeetingProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: MeetingProvider) -> None:
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str | None) -> MeetingProvider | None:
        if provider_id is None:
            return None
        return self._providers.get(provider_id)

    def ids(self) -> tuple[str, ...]:
        """Registered ids in the canonical order, not insertion order."""
        return tuple(pid for pid in PROVIDER_IDS if pid in self._providers)

    def offered_to(self, clinician: Clinician) -> tuple[str, ...]:
        """What this clinician may actually choose between.

        Registration says the deployment can talk to a service; this says the
        clinician has connected it. Only the second is worth putting in front
        of somebody, because a provider they have not connected would take a
        booking and produce no room.
        """
        return tuple(pid for pid in self.ids() if self._providers[pid].is_connected(clinician))


def normalise_provider(value: str | None) -> str | None:
    """Read a provider id, or a legacy free-text platform label, as an id.

    None in, None out — an appointment with no provider is an in-person one
    and must not acquire a video room by being read.
    """
    if value is None:
        return None
    key = value.strip().lower()
    if not key:
        return None
    if key in PROVIDER_IDS:
        return key
    return _LABEL_ALIASES.get(key, MANUAL)


def enabled_provider_ids(configured: Iterable[str]) -> tuple[str, ...]:
    """The deployment's provider list, normalised and in canonical order.

    An unrecognised name is dropped rather than rejected: the setting is
    environment configuration, and a typo there should cost one provider, not
    the ability to start.
    """
    wanted = {normalise_provider(name) for name in configured}
    return tuple(pid for pid in PROVIDER_IDS if pid in wanted)


def resolve_provider_id(
    *,
    requested: str | None,
    clinician: Clinician,
    offered: Iterable[str],
) -> str | None:
    """Which provider should make this appointment's room.

    In order: what the booking asked for, then what the clinician prefers.
    Either only counts if it is on offer — a request for a provider this
    clinician has not connected falls through to the preference rather than
    failing the booking, because a video link is not what the person was
    trying to do.

    None means nobody: an in-person appointment, or a clinician who has
    connected nothing.
    """
    available = tuple(offered)
    in_order = (
        normalise_provider(requested),
        normalise_provider(clinician.preferred_provider),
    )
    for candidate in in_order:
        if candidate is not None and candidate in available:
            return candidate
    return None


def wants_a_room(appointment: Appointment, *, requested_provider: str | None) -> bool:
    """Whether this appointment should be given a video room at all.

    The same rule that decides whether a visit is telehealth everywhere else
    (:func:`app.models.scheduling.is_telehealth`, which the client mirrors):
    a video service asked for by name, a link the clinician pasted, or a
    telehealth place of service. Asked of the booking rather than of the room
    the appointment already holds, so an edit to in person is not kept on
    video by the very room it is giving up.

    An appointment with no place of service and no service named gets no
    room. A default video service says which room to make for a video visit;
    it does not make every visit a video one.
    """
    return is_telehealth(
        provider=normalise_provider(requested_provider),
        video_link=appointment.video_link,
        place_of_service=appointment.place_of_service,
    )


def provision_meeting(
    appointment: Appointment,
    *,
    clinician: Clinician,
    practice: Practice,
    registry: MeetingProviderRegistry,
    attendee: Attendee = Attendee(),
    requested_provider: str | None = None,
) -> Appointment:
    """Give the appointment a room, and return it with the room recorded.

    Returns the appointment unchanged when there is nothing to do, which is
    the common case: an in-person appointment (see :func:`wants_a_room`), or
    one the clinician pasted a URL onto. A provider failure is also unchanged
    plus a raise, so the caller decides whether a missing room is worth
    failing a booking over — it is not, and :mod:`app.routes.scheduling`
    treats it the same way it treats a calendar push that did not go through.
    """
    if appointment.video_link:
        # A pasted URL is the clinician's decision about this one appointment
        # and outranks every preference. Recorded as MANUAL so the cancel path
        # knows there is no vendor to tell.
        return replace(appointment, provider=MANUAL)

    if not wants_a_room(appointment, requested_provider=requested_provider):
        return appointment

    provider_id = resolve_provider_id(
        requested=requested_provider,
        clinician=clinician,
        offered=registry.offered_to(clinician),
    )
    provider = registry.get(provider_id)
    if provider is None:
        return appointment

    link = provider.create_for_appointment(appointment, clinician, practice, attendee)
    if link is None:
        # The provider will supply the room by another route — Google Meet
        # through the calendar event. Record who owns it so that route knows
        # to ask, and so cancelling reaches the right place.
        return replace(appointment, provider=provider.provider_id)
    return replace(
        appointment,
        provider=link.provider,
        video_link=link.url,
        meeting_external_id=link.external_id,
    )


class RoomChange(StrEnum):
    """What an edit to an appointment means for its video room."""

    #: Nothing room-related changed, or the room it has is still the right one.
    KEEP = "keep"
    #: The clinician pasted a link. It becomes the room; any vendor room goes.
    MANUAL = "manual"
    #: The visit is no longer by video. The room goes and nothing replaces it.
    RELEASE = "release"
    #: The visit is now by video and has no room yet.
    PROVISION = "provision"
    #: A different service was asked for. The old room goes, a new one is made.
    REPLACE = "replace"


def room_change(
    previous: Appointment,
    edited: Appointment,
    *,
    requested_provider: str | None,
    clinician: Clinician,
    registry: MeetingProviderRegistry,
) -> RoomChange:
    """Decide what an edit does to the room, from the appointment before and after.

    Only what the edit itself changed is read: the place of service, a pasted
    link, or a service asked for by name. An edit that moves the time or fixes
    a title leaves the room exactly as it was, whatever the rule would say
    about an appointment booked before the rule existed.

    A link the clinician pasted is never replaced and never cleared by
    anything here. A new paste wins outright; an existing one is kept even
    when the visit moves to in person, because the clinician typed it and
    only the clinician takes it off.
    """
    if edited.video_link and edited.video_link != previous.video_link:
        return RoomChange.MANUAL
    requested = normalise_provider(requested_provider)
    if _holds_a_pasted_link(edited) or (
        requested is None and edited.place_of_service == previous.place_of_service
    ):
        return RoomChange.KEEP
    current = edited.provider
    if not wants_a_room(replace(edited, video_link=None), requested_provider=requested):
        return RoomChange.RELEASE if current else RoomChange.KEEP
    if current is None:
        return RoomChange.PROVISION
    # Still by video. Only a service asked for by name changes the room: a move
    # between the two telehealth places keeps the link the client already has.
    target = (
        resolve_provider_id(
            requested=requested,
            clinician=clinician,
            offered=registry.offered_to(clinician),
        )
        if requested is not None
        else None
    )
    return RoomChange.KEEP if target in (None, current) else RoomChange.REPLACE


def _holds_a_pasted_link(appointment: Appointment) -> bool:
    """A link the clinician typed: recorded MANUAL, or from before providers were recorded."""
    return appointment.provider == MANUAL or bool(
        appointment.video_link and appointment.provider is None
    )


def release_meeting(
    appointment: Appointment,
    *,
    clinician: Clinician,
    registry: MeetingProviderRegistry,
) -> bool:
    """Tell the provider the meeting is off. False when there was nothing to tell.

    Best-effort by contract. A cancellation that the vendor did not hear
    about leaves a room nobody joins, which is untidy; refusing to cancel the
    appointment over it would leave a patient expected at a time the practice
    has already given away.
    """
    provider = registry.get(appointment.provider)
    if provider is None or not appointment.meeting_external_id:
        return False
    return provider.cancel(appointment.meeting_external_id, clinician)


def join_opens_at(appointment: Appointment, *, window_minutes: int) -> datetime:
    """The instant the join link starts being offered."""
    return appointment.start_at - timedelta(minutes=window_minutes)


def is_joinable(
    appointment: Appointment,
    *,
    now: datetime,
    window_minutes: int = DEFAULT_JOIN_WINDOW_MINUTES,
) -> bool:
    """Whether the link is worth offering at ``now``.

    From ``window_minutes`` before the start until the scheduled end, and only
    for an appointment that is still going ahead. A cancelled appointment
    keeps its link in the row — the record of what was booked — and must
    never offer it.
    """
    if not appointment.video_link:
        return False
    if appointment.status in {"cancelled", "no_show"}:
        return False
    return join_opens_at(appointment, window_minutes=window_minutes) <= now <= appointment.end_at


def client_join_link(appointment: Appointment, *, practice_sends_link: bool) -> str | None:
    """The video link a message to the client may carry, or None.

    Every message to a client that could carry the link — a reminder, a
    booking confirmation — asks here, so they cannot disagree about it.

    Sent by default. Two switches stop it and either one is enough: the
    practice's own (``platform.practices.send_clients_video_link``, read with
    :func:`practice_sends_client_video_link`), and the deployment's
    (``TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS``), for a deployment that must
    send no link to anybody. The deployment switch is read here rather than
    passed in, so no caller can forget it.

    An appointment recorded as in person carries no link, even one a clinician
    pasted before the visit moved: the room stays on the appointment for the
    clinician to take off (:func:`room_change`), but a client told to come to
    the office should not also be sent somewhere to join. An appointment with
    no place of service keeps its link, because that is how every appointment
    booked before places were recorded looks.

    The link goes out without the client's name. A Doxy room is stored with
    ``username=<first name>`` so the clinician's waiting room checks the right
    person in, but a message can be read on a lock screen, forwarded, or passed
    through a mail vendor's link tracking: see :func:`_without_client_name`.
    """
    if not (practice_sends_link and get_settings().telehealth_include_join_link_in_reminders):
        return None
    place = appointment.place_of_service
    if place and place not in TELEHEALTH_PLACES:
        return None
    if not appointment.video_link:
        return None
    return _without_client_name(appointment.video_link)


#: Query keys that name the person joining. ``username`` is the one Doxy
#: reads (``meeting_providers.doxy.compose_room_url``); it is stripped
#: from any link, whatever service stored it, so a new provider that copies
#: the convention cannot put a name in a client message by accident.
_NAME_QUERY_KEYS: Final = frozenset({"username"})


def _without_client_name(link: str) -> str:
    """``link`` with every query parameter that names the client taken off.

    Works on the raw ``&``-separated pairs rather than parsing and re-encoding,
    so every other parameter (Doxy's ``pid`` and ``autocheckin``, a Zoom
    password) goes out byte for byte as stored.
    """
    parts = urlsplit(link)
    kept = [
        pair
        for pair in parts.query.split("&")
        if unquote_plus(pair.partition("=")[0]).lower() not in _NAME_QUERY_KEYS
    ]
    return urlunsplit(parts._replace(query="&".join(kept)))


def practice_sends_client_video_link(session: Session, practice_id: str | None) -> bool:
    """Whether this practice sends its clients their video link.

    The column says, and it is on unless the practice turned it off. A practice
    that cannot be found sends nothing: the link goes out on a practice's
    decision, and there is no practice here to have made one.
    """
    from ..db.platform_models import PracticeRow

    if not practice_id:
        return False
    practice = session.get(PracticeRow, practice_id)
    return bool(practice is not None and practice.send_clients_video_link)


#: The message of the one warning a video appointment saved without a link
#: logs. Fixed, so a log-based alert can count it; why is in ``reason``.
MISSING_LINK_MESSAGE: Final = "telehealth_link_missing"


class MissingLinkReason(StrEnum):
    """Why a video appointment was saved without a link."""

    #: No video service the clinician has connected was asked for or preferred.
    NO_OFFERED_SERVICE = "no_offered_service"
    #: The service was asked and could not make a room, or Google answered
    #: the conference request with none.
    PROVIDER_FAILED = "provider_failed"
    #: The calendar push that makes a Meet conference did not go through.
    PUSH_FAILED = "push_failed"
    #: A Meet room was wanted and there is no calendar connection to make it on.
    CALENDAR_DISCONNECTED = "calendar_disconnected"
    #: Google was still making the conference when the last read gave up.
    CONFERENCE_PENDING_TIMEOUT = "conference_pending_timeout"
    #: Nothing on the way asked for a room at all.
    NEVER_PROVISIONED = "never_provisioned"


@dataclass
class LinkTrail:
    """What a save learned about its video link, for :func:`log_missing_link`.

    Starts at NEVER_PROVISIONED, because a save that asks nobody for a room
    is exactly that. The steps a save takes (the room, the calendar push)
    each mark what they found, and the last one to mark wins: it is the step
    nearest to the missing link. ``reading_back`` is set when a late read of
    a Meet link is still to come, which is not a missing link yet.
    """

    reason: MissingLinkReason = MissingLinkReason.NEVER_PROVISIONED
    reading_back: bool = False


#: Statuses where a missing link is expected. A request gets its room when it
#: is agreed to, and nobody joins a cancelled, missed or finished session.
_NO_LINK_EXPECTED: Final = frozenset(
    {
        AppointmentStatus.PENDING,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
        AppointmentStatus.COMPLETED,
    }
)


def log_missing_link(
    appointment: Appointment,
    *,
    trail: LinkTrail,
    requested_provider: str | None = None,
) -> bool:
    """Log one warning when a video appointment was saved without a link.

    Called once at the end of every save that can give an appointment a room,
    after the room and the calendar push have settled. Logs nothing for an
    in-person appointment, one that has its link, one whose Meet link is
    still being read back, or one that is not going ahead. Never changes the
    appointment: this says why the link is missing and nothing more.

    The record carries the reason, the service the save asked for and the
    appointment id, and nothing about the client or the session.
    """
    if appointment.video_link or trail.reading_back:
        return False
    if appointment.status in _NO_LINK_EXPECTED:
        return False
    requested = normalise_provider(requested_provider)
    if not is_telehealth(
        provider=requested or appointment.provider,
        video_link=None,
        place_of_service=appointment.place_of_service,
    ):
        return False
    logger.warning(
        MISSING_LINK_MESSAGE,
        extra={
            "reason": trail.reason.value,
            "requested_provider": requested,
            "appointment_id": appointment.id,
        },
    )
    return True
