"""A video session a client books gets a video room (PABLO-en9h.6).

Every path that turns a client's booking-link booking into a confirmed
appointment: the instant booking, a hold the client confirms, and a
single-slot link under ``auto``. Each takes where the visit happens from the
type's remembered In person / Video, makes the room the clinician's default
service makes, and only then pushes to the calendar, so a Meet conference
rides on the first push. The registry, the calendar and the late Meet read
are fakes; everything between the route and them is the real code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.repositories.booking_link import InMemoryBookingLinkRepository
from app.routes import public_booking as public_booking_module
from app.routes import scheduling
from app.routes.public_booking import get_public_gcal_service
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.telehealth import (
    GOOGLE_MEET,
    ZOOM,
    Clinician,
    MeetingLink,
    MeetingProviderRegistry,
    place_for_modality,
)
from app.settings import get_settings
from app.utcnow import utc_now

from .test_public_booking_routes import (
    OWNER_ID,
    TYPE_ID,
    _book,
    _bookable_date,
    _hold,
    _link,
    _public_app,
    _working_hours_rule,
)
from .test_telehealth_on_the_write_path import (
    MEET_LINK,
    InlineFollowUp,
    RecordingCalendar,
    RecordingProvider,
    SlowMeetCalendar,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

PRACTICE = "practice_room_test"
ZOOM_LINK = "https://z.test/81"


@pytest.fixture
def link_repo() -> InMemoryBookingLinkRepository:
    return InMemoryBookingLinkRepository()


class Rooms:
    """The video services and the calendar a client's booking reaches."""

    def __init__(
        self,
        client: Any,
        monkeypatch: pytest.MonkeyPatch,
        *,
        provider: RecordingProvider,
        calendar: RecordingCalendar,
    ) -> None:
        self.provider = provider
        self.calendar = calendar
        self.follow_up = InlineFollowUp(SchedulingService(client.appt_repo), calendar)  # type: ignore[arg-type]  # SlowMeetCalendar when a test needs the late read
        self.follow_up_schemas: list[str | None] = []
        monkeypatch.setattr(
            public_booking_module,
            "build_meeting_registry",
            lambda: MeetingProviderRegistry([provider]),
        )
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=provider.provider_id),
        )
        monkeypatch.setattr(public_booking_module, "MeetLinkFollowUp", self._follow_up)
        client.app.dependency_overrides[get_public_gcal_service] = lambda: calendar

    def _follow_up(self, practice_schema: str | None) -> InlineFollowUp:
        self.follow_up_schemas.append(practice_schema)
        return self.follow_up


def _meet() -> RecordingProvider:
    return RecordingProvider(GOOGLE_MEET, link=None)


def _zoom(*, raises: bool = False) -> RecordingProvider:
    return RecordingProvider(
        ZOOM, link=MeetingLink(url=ZOOM_LINK, provider=ZOOM, external_id="81"), raises=raises
    )


def _client(
    link_repo: InMemoryBookingLinkRepository,
    monkeypatch: pytest.MonkeyPatch,
    *,
    modality: str | None,
    provider: RecordingProvider,
    calendar: RecordingCalendar | None = None,
) -> tuple[Any, Rooms]:
    client = _public_app(link_repo)
    if modality is not None:
        client.type_repo.record_modality(TYPE_ID, OWNER_ID, modality)
    rooms = Rooms(
        client, monkeypatch, provider=provider, calendar=calendar or RecordingCalendar()
    )
    return client, rooms


def _instant_link(link_repo: InMemoryBookingLinkRepository) -> None:
    link = _link(require_email_confirmation=False)
    link.practice_schema = PRACTICE
    link_repo.create(link)


def _book_instantly(client: Any, link_repo: InMemoryBookingLinkRepository) -> Appointment:
    _instant_link(link_repo)
    date_str = _bookable_date()
    client.rule_repo.create(_working_hours_rule(date_str))
    resp = _book(client, "intro-call", f"{date_str}T09:30:00Z")
    assert resp.status_code == 201, resp.text
    return _only(client)


def _book_by_hold(client: Any, link_repo: InMemoryBookingLinkRepository) -> Appointment:
    link = _link()
    link.practice_schema = PRACTICE
    link_repo.create(link)
    date_str = _bookable_date()
    client.rule_repo.create(_working_hours_rule(date_str))
    _, token = _hold(client, "intro-call", f"{date_str}T09:00:00Z")
    resp = client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 200, resp.text
    return _only(client)


SLOT_SLUG = "slot-0123456789abcdef"


def _slot_start() -> datetime:
    return (utc_now() + timedelta(days=7)).replace(minute=0, second=0, microsecond=0)


def _book_slot_link(
    client: Any, link_repo: InMemoryBookingLinkRepository, *, mode: str = "auto"
) -> Appointment:
    client.policy["self_book_mode"] = mode
    start = _slot_start()
    link = _link(slug=SLOT_SLUG)
    link.practice_schema = PRACTICE
    link.slot_start_at = start
    link.slot_end_at = start + timedelta(minutes=30)
    link_repo.create(link)
    resp = _book(client, SLOT_SLUG, start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert resp.status_code == 201, resp.text
    return _only(client)


def _only(client: Any) -> Appointment:
    [appointment] = client.appt_repo._appointments.values()
    return appointment


#: Each path that turns a client's booking into a confirmed appointment.
PATHS = {
    "instant": _book_instantly,
    "hold_confirmed": _book_by_hold,
    "single_slot_auto": _book_slot_link,
}


@pytest.fixture(params=sorted(PATHS))
def book(request: pytest.FixtureRequest) -> Any:
    return PATHS[request.param]


class TestAVideoTypeGetsARoom:
    def test_meet_is_on_the_first_push_and_the_link_is_saved(
        self, book: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, modality="video", provider=_meet())

        appt = book(client, link_repo)

        assert appt.place_of_service == "02"
        assert appt.provider == GOOGLE_MEET
        assert appt.video_link == MEET_LINK
        assert appt.google_event_id == "evt-1"
        # One push, and it already asked for the conference.
        assert len(rooms.calendar.events) == 1
        assert rooms.calendar.last_conference is not None

    def test_zoom_as_the_default_makes_a_zoom_meeting(
        self, book: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, modality="video", provider=_zoom())

        appt = book(client, link_repo)

        assert appt.provider == ZOOM
        assert appt.video_link == ZOOM_LINK
        assert appt.meeting_external_id == "81"
        # The room is for the client who booked, by first name only.
        assert [a.display_name for a in rooms.provider.attendees] == ["Jane"]


class TestNoRoomUnlessTheTypeSaysVideo:
    @pytest.mark.parametrize(("modality", "place"), [("in_person", "11"), (None, None)])
    def test_in_person_or_never_booked_gets_no_room(
        self,
        book: Any,
        link_repo: InMemoryBookingLinkRepository,
        monkeypatch: pytest.MonkeyPatch,
        modality: str | None,
        place: str | None,
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, modality=modality, provider=_zoom())

        appt = book(client, link_repo)

        assert appt.place_of_service == place
        assert appt.provider is None
        assert appt.video_link is None
        assert rooms.provider.attendees == []
        # Still on the calendar, with no conference asked for.
        assert len(rooms.calendar.events) == 1
        assert rooms.calendar.last_conference is None


class TestAVendorFailureKeepsTheBooking:
    def test_the_booking_stands_without_a_link(
        self, book: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(
            link_repo, monkeypatch, modality="video", provider=_zoom(raises=True)
        )

        appt = book(client, link_repo)

        assert appt.status == AppointmentStatus.CONFIRMED
        assert appt.place_of_service == "02"
        assert appt.video_link is None
        assert appt.provider is None
        assert appt.google_event_id == "evt-1"


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A save that never holds for the link, so a pending answer goes to the late read."""
    monkeypatch.setenv("GOOGLE_MEET_LINK_WAIT_SECONDS", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestAPendingMeetAnswerIsReadLater:
    @pytest.mark.usefixtures("no_wait")
    def test_the_late_read_fills_the_link_in_the_link_s_practice(
        self, book: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendar = SlowMeetCalendar(None, later=MEET_LINK)
        client, rooms = _client(
            link_repo, monkeypatch, modality="video", provider=_meet(), calendar=calendar
        )

        appt = book(client, link_repo)

        assert rooms.follow_up.runs == 1
        assert PRACTICE in rooms.follow_up_schemas
        assert appt.video_link == MEET_LINK


class TestNothingIsMadeBeforeTheBookingIsConfirmed:
    def test_a_hold_waiting_for_its_email_has_no_room(
        self, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, modality="video", provider=_zoom())
        link_repo.create(_link())
        date_str = _bookable_date()
        client.rule_repo.create(_working_hours_rule(date_str))

        _hold(client, "intro-call", f"{date_str}T09:00:00Z")

        held = _only(client)
        assert held.status == AppointmentStatus.PENDING
        assert held.place_of_service == "02"
        assert held.video_link is None
        assert rooms.provider.attendees == []

    def test_a_single_slot_request_has_no_room(
        self, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, modality="video", provider=_zoom())

        appt = _book_slot_link(client, link_repo, mode="request")

        assert appt.status == AppointmentStatus.PENDING
        assert appt.place_of_service == "02"
        assert appt.video_link is None
        assert rooms.provider.attendees == []


@pytest.mark.parametrize(
    ("modality", "place"), [("video", "02"), ("in_person", "11"), (None, None)]
)
def test_place_for_modality(modality: str | None, place: str | None) -> None:
    assert place_for_modality(modality) == place  # type: ignore[arg-type]  # the Literal, by value
