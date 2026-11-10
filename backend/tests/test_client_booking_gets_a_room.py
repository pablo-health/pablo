"""A video session a client books gets a video room (PABLO-en9h.6).

Every path that turns a client's booking-link booking into a confirmed
appointment: the instant booking, a hold the client confirms, a single-slot
link under ``auto``, and a request the clinician agrees to in the app. Each
takes where the visit happens from the type's remembered In person / Video,
makes the room the clinician's default service makes, and only then pushes to
the calendar, so a Meet conference rides on the first push. The registry, the
calendar and the late Meet read are fakes; everything between the route and
them is the real code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.repositories.booking_link import InMemoryBookingLinkRepository
from app.routes import public_booking as public_booking_module
from app.routes import scheduling
from app.routes.public_booking import get_public_gcal_service
from app.routes.scheduling import (
    get_google_calendar_service,
    get_meet_link_follow_up,
    get_meeting_registry,
    get_scheduling_service,
)
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.telehealth import (
    GOOGLE_MEET,
    MANUAL,
    ZOOM,
    Clinician,
    MeetingLink,
    MeetingProviderRegistry,
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

    from fastapi.testclient import TestClient

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
    place: str | None,
    provider: RecordingProvider,
    calendar: RecordingCalendar | None = None,
) -> tuple[Any, Rooms]:
    client = _public_app(link_repo)
    if place is not None:
        client.type_repo.record_place(TYPE_ID, OWNER_ID, place)
    rooms = Rooms(client, monkeypatch, provider=provider, calendar=calendar or RecordingCalendar())
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
        client, rooms = _client(link_repo, monkeypatch, place="02", provider=_meet())

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
        client, rooms = _client(link_repo, monkeypatch, place="02", provider=_zoom())

        appt = book(client, link_repo)

        assert appt.provider == ZOOM
        assert appt.video_link == ZOOM_LINK
        assert appt.meeting_external_id == "81"
        # The room is for the client who booked, by first name only.
        assert [a.display_name for a in rooms.provider.attendees] == ["Jane"]

    def test_a_type_taught_with_10_books_the_client_as_10_with_a_room(
        self, book: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The client's booking takes the type's exact code; 10 never becomes 02."""
        client, _rooms = _client(link_repo, monkeypatch, place="10", provider=_zoom())

        appt = book(client, link_repo)

        assert appt.place_of_service == "10"
        assert appt.provider == ZOOM
        assert appt.video_link == ZOOM_LINK


class TestNoRoomUnlessTheTypeSaysVideo:
    @pytest.mark.parametrize("place", ["11", None])
    def test_in_person_or_never_booked_gets_no_room(
        self,
        book: Any,
        link_repo: InMemoryBookingLinkRepository,
        monkeypatch: pytest.MonkeyPatch,
        place: str | None,
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, place=place, provider=_zoom())

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
        client, _rooms = _client(link_repo, monkeypatch, place="02", provider=_zoom(raises=True))

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
            link_repo, monkeypatch, place="02", provider=_meet(), calendar=calendar
        )

        appt = book(client, link_repo)

        assert rooms.follow_up.runs == 1
        assert PRACTICE in rooms.follow_up_schemas
        assert appt.video_link == MEET_LINK


class TestNothingIsMadeBeforeTheBookingIsConfirmed:
    def test_a_hold_waiting_for_its_email_has_no_room(
        self, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, place="02", provider=_zoom())
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
        client, rooms = _client(link_repo, monkeypatch, place="02", provider=_zoom())

        appt = _book_slot_link(client, link_repo, mode="request")

        assert appt.status == AppointmentStatus.PENDING
        assert appt.place_of_service == "02"
        assert appt.video_link is None
        assert rooms.provider.attendees == []


class AgreeingInTheApp:
    """The clinician's PATCH over the real SchedulingService, holding one request."""

    def __init__(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        user_id: str,
        *,
        provider: RecordingProvider,
        also: tuple[RecordingProvider, ...] = (),
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.provider = provider
        self.calendar = RecordingCalendar()
        self.appt_repo = InMemoryAppointmentRepository()
        engine = AvailabilityEngine(InMemoryAvailabilityRuleRepository(), self.appt_repo)
        service = SchedulingService(self.appt_repo, engine)
        app.dependency_overrides[get_scheduling_service] = lambda: service
        app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry(
            [provider, *also]
        )
        app.dependency_overrides[get_google_calendar_service] = lambda: self.calendar
        follow_up = InlineFollowUp(service, self.calendar)
        app.dependency_overrides[get_meet_link_follow_up] = lambda: follow_up
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, uid: Clinician(id=uid, preferred_provider=provider.provider_id),
        )

    def request(
        self,
        *,
        place: str | None = "02",
        status: str = AppointmentStatus.PENDING,
        video_link: str | None = None,
        provider: str | None = None,
    ) -> Appointment:
        """A request as a single-slot link under ``request`` leaves it: no room yet."""
        start = _slot_start()
        return self.appt_repo.create(
            Appointment(
                id="appt-request",
                user_id=self.user_id,
                patient_id="patient-1",
                title="Intro call",
                start_at=start,
                end_at=start + timedelta(minutes=30),
                duration_minutes=30,
                status=status,
                session_type="Intro call",
                place_of_service=place,
                video_link=video_link,
                provider=MANUAL if video_link else provider,
            )
        )

    def patch(self, **body: Any) -> dict[str, Any]:
        resp = self.client.patch("/api/appointments/appt-request", json=body)
        assert resp.status_code == 200, resp.text
        result: dict[str, Any] = resp.json()
        return result


@pytest.fixture
def agree(client: TestClient, monkeypatch: pytest.MonkeyPatch, mock_user_id: str) -> Any:
    def make(
        provider: RecordingProvider, also: tuple[RecordingProvider, ...] = ()
    ) -> AgreeingInTheApp:
        return AgreeingInTheApp(client, monkeypatch, mock_user_id, provider=provider, also=also)

    return make


class TestARequestAgreedToInTheAppGetsItsRoom:
    def test_zoom_room_is_made_when_the_request_is_confirmed(self, agree: Any) -> None:
        inbox = agree(_zoom())
        inbox.request()

        body = inbox.patch(status="confirmed")

        assert body["status"] == "confirmed"
        assert body["provider"] == ZOOM
        assert body["video_link"] == ZOOM_LINK
        assert body["meeting_external_id"] == "81"
        # Made once, and the one push carries the link.
        assert len(inbox.provider.attendees) == 1
        assert len(inbox.calendar.events) == 1

    def test_meet_is_on_the_push_that_follows(self, agree: Any) -> None:
        inbox = agree(_meet())
        inbox.request()

        body = inbox.patch(status="confirmed")

        assert body["provider"] == GOOGLE_MEET
        assert body["video_link"] == MEET_LINK
        assert len(inbox.calendar.events) == 1
        assert inbox.calendar.last_conference is not None

    def test_an_in_person_request_gets_no_room(self, agree: Any) -> None:
        inbox = agree(_zoom())
        inbox.request(place="11")

        body = inbox.patch(status="confirmed")

        assert body["status"] == "confirmed"
        assert body["provider"] is None
        assert body["video_link"] is None
        assert inbox.provider.attendees == []
        assert inbox.calendar.last_conference is None

    def test_a_pasted_link_is_kept_and_no_vendor_is_asked(self, agree: Any) -> None:
        inbox = agree(_zoom())
        inbox.request(video_link="https://practice.test/r")

        body = inbox.patch(status="confirmed")

        assert body["video_link"] == "https://practice.test/r"
        assert body["provider"] == MANUAL
        assert inbox.provider.attendees == []

    def test_a_meet_room_already_asked_for_is_not_replaced(self, agree: Any) -> None:
        # The request already holds a Meet room whose link Google has not
        # handed back yet; the clinician's default is now Zoom. Agreeing to
        # the request keeps the room it has rather than making a second one.
        meet = _meet()
        inbox = agree(_zoom(), also=(meet,))
        inbox.request(provider=GOOGLE_MEET)

        body = inbox.patch(status="confirmed")

        assert body["provider"] == GOOGLE_MEET
        assert inbox.provider.attendees == []

    def test_a_vendor_failure_still_confirms(self, agree: Any) -> None:
        inbox = agree(_zoom(raises=True))
        inbox.request()

        body = inbox.patch(status="confirmed")

        assert body["status"] == "confirmed"
        assert body["video_link"] is None
        assert body["provider"] is None


class TestNoRoomWithoutAgreeing:
    def test_editing_a_request_that_stays_pending_makes_no_room(self, agree: Any) -> None:
        inbox = agree(_zoom())
        inbox.request()

        body = inbox.patch(title="Intro call (moved)")

        assert body["status"] == "pending"
        assert body["video_link"] is None
        assert inbox.provider.attendees == []

    def test_re_saving_where_a_pending_request_happens_makes_no_room(self, agree: Any) -> None:
        # The edit form sends the place back unchanged; the request is still
        # waiting for an answer, so it still has no room.
        inbox = agree(_zoom())
        inbox.request()

        body = inbox.patch(place_of_service="02")

        assert body["status"] == "pending"
        assert body["video_link"] is None
        assert inbox.provider.attendees == []

    def test_confirming_a_confirmed_appointment_makes_no_room(self, agree: Any) -> None:
        # Not a request agreed to: a confirmed visit with no room is one the
        # clinician left that way, and saving it again does not change that.
        inbox = agree(_zoom())
        inbox.request(status=AppointmentStatus.CONFIRMED)

        body = inbox.patch(status="confirmed")

        assert body["video_link"] is None
        assert inbox.provider.attendees == []


def _confirm_and_keep_the_token(
    client: Any, link_repo: InMemoryBookingLinkRepository
) -> tuple[Appointment, str]:
    """Book by hold and confirm, keeping the token: it is also the manage link's."""
    link = _link()
    link.practice_schema = PRACTICE
    link_repo.create(link)
    date_str = _bookable_date()
    client.rule_repo.create(_working_hours_rule(date_str))
    _, token = _hold(client, "intro-call", f"{date_str}T09:00:00Z")
    resp = client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 200, resp.text
    return _only(client), token


def _cancel_from_the_manage_link(client: Any, token: str) -> Appointment:
    resp = client.post("/api/public/booking-links/intro-call/manage/cancel", json={"token": token})
    assert resp.status_code == 200, resp.text
    cancelled = _only(client)
    assert cancelled.status == AppointmentStatus.CANCELLED
    return cancelled


class TestAClientCancellingReleasesTheRoom:
    """The third way out of a booking: the client cancels from the manage link."""

    def test_the_zoom_meeting_is_released_at_the_vendor(
        self, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, place="02", provider=_zoom())
        appt, token = _confirm_and_keep_the_token(client, link_repo)
        assert appt.meeting_external_id == "81"

        _cancel_from_the_manage_link(client, token)

        assert rooms.provider.cancelled == ["81"]

    def test_a_vendor_that_refuses_the_release_does_not_stop_the_cancel(
        self, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _zoom()

        def refuse(external_id: str, clinician: Clinician) -> bool:
            raise RuntimeError("zoom is down")

        monkeypatch.setattr(provider, "cancel", refuse)
        client, _rooms = _client(link_repo, monkeypatch, place="02", provider=provider)
        _appt, token = _confirm_and_keep_the_token(client, link_repo)

        _cancel_from_the_manage_link(client, token)

    def test_an_in_person_booking_has_nothing_to_release(
        self, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, rooms = _client(link_repo, monkeypatch, place="11", provider=_zoom())
        _appt, token = _confirm_and_keep_the_token(client, link_repo)

        _cancel_from_the_manage_link(client, token)

        assert rooms.provider.cancelled == []
