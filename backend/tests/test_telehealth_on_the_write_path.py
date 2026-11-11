# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Booking an appointment on a video service, end to end through the route.

The seam's own tests prove the rules; these prove the wiring — that the route
asks the registry, persists what comes back, releases the meeting when the
appointment is cancelled, and never fails a booking over a vendor.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.routes import scheduling
from app.routes.scheduling import (
    MeetLinkFollowUp,
    get_availability_rule_repository,
    get_google_calendar_service,
    get_meet_link_follow_up,
    get_meeting_registry,
    get_scheduling_service,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.google_calendar_service import GoogleCalendarService, PushedEvent
from app.services.telehealth import (
    DOXY_ME,
    GOOGLE_MEET,
    MANUAL,
    ZOOM,
    Attendee,
    Clinician,
    MeetingLink,
    MeetingProviderRegistry,
    Practice,
    TelehealthError,
)
from app.settings import Settings, get_settings
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.scheduling_engine.models.appointment import Appointment
    from fastapi.testclient import TestClient


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "patient_id": "patient-1",
        "title": "Weekly check-in",
        "start_at": "2026-04-15T14:00:00Z",
        "end_at": "2026-04-15T14:50:00Z",
        "duration_minutes": 50,
    }
    payload.update(overrides)
    return payload


class RecordingProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        link: MeetingLink | None,
        raises: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.display_name = provider_id
        self._link = link
        self._raises = raises
        self.attendees: list[Attendee] = []
        self.cancelled: list[str] = []

    def is_connected(self, clinician: Clinician) -> bool:
        return True

    def create_for_appointment(
        self,
        appointment,  # type: ignore[no-untyped-def]
        clinician: Clinician,
        practice: Practice,
        attendee: Attendee,
    ) -> MeetingLink | None:
        if self._raises:
            raise TelehealthError("vendor said no")
        self.attendees.append(attendee)
        return self._link

    def cancel(self, external_id: str, clinician: Clinician) -> bool:
        self.cancelled.append(external_id)
        return True


@pytest.fixture
def write_client(client: TestClient) -> TestClient:
    """The real SchedulingService over in-memory repos, as the route tests use."""
    appt_repo = InMemoryAppointmentRepository()
    rule_repo = InMemoryAvailabilityRuleRepository()
    engine = AvailabilityEngine(rule_repo, appt_repo)
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo, engine)
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    return client


def _registry(provider: RecordingProvider) -> None:
    app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([provider])


class TestBookingGetsARoom:
    def test_the_provider_s_link_and_handle_are_persisted(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        response = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["provider"] == ZOOM
        assert body["video_link"] == "https://z.test/81"
        assert body["meeting_external_id"] == "81"

    def test_a_pasted_link_is_not_replaced_by_the_preference(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        payload = _create_payload()
        payload["video_link"] = "https://practice.test/my-room"
        response = write_client.post("/api/appointments", json=payload)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["video_link"] == "https://practice.test/my-room"
        assert body["provider"] == MANUAL
        assert provider.attendees == []

    def test_a_vendor_failure_never_fails_the_booking(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Somebody is expected at a time. The missing piece is a link."""
        _registry(RecordingProvider(ZOOM, link=None, raises=True))
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        response = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )

        assert response.status_code == 201, response.text
        assert response.json()["video_link"] is None

    def test_only_a_first_name_reaches_the_provider(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            DOXY_ME, link=MeetingLink(url="https://d.test/r", provider=DOXY_ME, external_id="h")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=DOXY_ME),
        )
        monkeypatch.setattr(
            scheduling, "_attendee_view", lambda _repo, _appt: Attendee(display_name="Mary")
        )

        response = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )

        assert response.status_code == 201, response.text
        assert provider.attendees == [Attendee(display_name="Mary")]


class TestCancelling:
    def test_the_vendor_meeting_is_released(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )
        assert created.status_code == 201, created.text

        cancelled = write_client.delete(f"/api/appointments/{created.json()['id']}")

        assert cancelled.status_code == 200, cancelled.text
        assert provider.cancelled == ["81"]

    def test_an_in_person_appointment_releases_nothing(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(ZOOM, link=None)
        _registry(provider)
        monkeypatch.setattr(
            scheduling, "_clinician_view", lambda _repo, user_id: Clinician(id=user_id)
        )

        created = write_client.post("/api/appointments", json=_create_payload())
        assert created.status_code == 201, created.text

        write_client.delete(f"/api/appointments/{created.json()['id']}")

        assert provider.cancelled == []


MEET_LINK = "https://meet.test/abc-defg-hij"


class RecordingCalendar:
    """Google Calendar as the route sees it: what each push would have written.

    The event body is built by the real ``_appointment_to_event``, so a test
    reading ``conferenceData`` here reads what Google would have been sent.
    Like Google, it answers a conference ``createRequest`` with a Meet link,
    and, like Google, it ignores a ``requestId`` it has already answered and
    hands back no conference for it.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.request_ids: list[str] = []

    def push_appointment_event(self, _user_id: str, appt: Appointment) -> PushedEvent:
        event = GoogleCalendarService._appointment_to_event(appt)
        self.events.append(event)
        request = event.get("conferenceData", {}).get("createRequest")
        if request is None:
            return PushedEvent(event_id="evt-1", conference_url=None)
        answered = request["requestId"] in self.request_ids
        self.request_ids.append(request["requestId"])
        return PushedEvent(event_id="evt-1", conference_url=None if answered else MEET_LINK)

    def push_appointment(self, user_id: str, appt: Appointment) -> str:
        return self.push_appointment_event(user_id, appt).event_id

    @property
    def last_conference(self) -> dict[str, Any] | None:
        conference: dict[str, Any] | None = self.events[-1].get("conferenceData")
        return conference


@pytest.fixture
def calendar() -> RecordingCalendar:
    recording = RecordingCalendar()
    app.dependency_overrides[get_google_calendar_service] = lambda: recording
    return recording


def _connected(*providers: RecordingProvider) -> None:
    app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry(providers)


def _prefers(monkeypatch: pytest.MonkeyPatch, provider_id: str) -> None:
    monkeypatch.setattr(
        scheduling,
        "_clinician_view",
        lambda _repo, user_id: Clinician(id=user_id, preferred_provider=provider_id),
    )


def _zoom() -> RecordingProvider:
    return RecordingProvider(
        ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
    )


class TestBookingInPersonOrByVideo:
    """Meet is the connected default throughout: the case that put rooms on every visit."""

    @pytest.mark.parametrize("place", ["11", None])
    def test_an_in_person_visit_gets_no_room_and_no_conference(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
        place: str | None,
    ) -> None:
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service=place)
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["provider"] is None
        assert body["video_link"] is None
        assert calendar.last_conference is None

    @pytest.mark.parametrize("place", ["02", "10"])
    def test_a_video_visit_gets_its_meet_room(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
        place: str,
    ) -> None:
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service=place)
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["provider"] == GOOGLE_MEET
        assert body["video_link"] == MEET_LINK
        assert body["place_of_service"] == place

    @pytest.mark.parametrize("place", ["11", "10"])
    def test_a_pasted_link_is_kept_either_way(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
        place: str,
    ) -> None:
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post(
            "/api/appointments",
            json=_create_payload(place_of_service=place, video_link="https://practice.test/r"),
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["video_link"] == "https://practice.test/r"
        assert body["provider"] == MANUAL


class TestEditingWhereTheVisitHappens:
    def test_in_person_to_video_makes_a_room(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        zoom = _zoom()
        _connected(zoom)
        _prefers(monkeypatch, ZOOM)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="11")
        )
        assert created.json()["provider"] is None

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"place_of_service": "10"}
        )

        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["provider"] == ZOOM
        assert body["video_link"] == "https://z.test/81"
        assert body["meeting_external_id"] == "81"

    def test_video_to_in_person_releases_the_zoom_meeting(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        zoom = _zoom()
        _connected(zoom)
        _prefers(monkeypatch, ZOOM)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )
        assert created.json()["provider"] == ZOOM

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"place_of_service": "11"}
        )

        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert zoom.cancelled == ["81"]
        assert body["provider"] is None
        assert body["video_link"] is None
        assert body["meeting_external_id"] is None
        assert calendar.last_conference is None

    def test_video_to_in_person_takes_the_meet_conference_off_the_event(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="02")
        )
        assert calendar.last_conference is not None

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"place_of_service": "11"}
        )

        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["provider"] is None
        assert body["video_link"] is None
        # The event is written whole, so a body without conferenceData is
        # what takes the conference off it.
        assert calendar.last_conference is None

    def test_meet_to_zoom_swaps_the_room(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        zoom = _zoom()
        _connected(RecordingProvider(GOOGLE_MEET, link=None), zoom)
        _prefers(monkeypatch, GOOGLE_MEET)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )
        assert created.json()["video_link"] == MEET_LINK

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"provider": ZOOM}
        )

        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["provider"] == ZOOM
        assert body["video_link"] == "https://z.test/81"
        assert body["meeting_external_id"] == "81"
        conference = calendar.last_conference
        assert conference is not None
        assert "createRequest" not in conference
        assert [p["uri"] for p in conference["entryPoints"]] == ["https://z.test/81"]

    def test_video_in_person_video_gets_a_fresh_meet_room(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )
        appointment_id = created.json()["id"]
        write_client.patch(f"/api/appointments/{appointment_id}", json={"place_of_service": "11"})

        back = write_client.patch(
            f"/api/appointments/{appointment_id}", json={"place_of_service": "10"}
        )

        assert back.status_code == 200, back.text
        assert back.json()["provider"] == GOOGLE_MEET
        assert back.json()["video_link"] == MEET_LINK
        assert len(calendar.request_ids) == 2
        assert len(set(calendar.request_ids)) == 2

    def test_meet_zoom_meet_gets_a_fresh_meet_room(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _connected(RecordingProvider(GOOGLE_MEET, link=None), _zoom())
        _prefers(monkeypatch, GOOGLE_MEET)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )
        appointment_id = created.json()["id"]
        write_client.patch(f"/api/appointments/{appointment_id}", json={"provider": ZOOM})

        back = write_client.patch(
            f"/api/appointments/{appointment_id}", json={"provider": GOOGLE_MEET}
        )

        assert back.status_code == 200, back.text
        assert back.json()["provider"] == GOOGLE_MEET
        assert back.json()["video_link"] == MEET_LINK
        assert len(set(calendar.request_ids)) == 2

    def test_a_pasted_link_replaces_the_vendor_room_and_is_recorded_manual(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        zoom = _zoom()
        _connected(zoom)
        _prefers(monkeypatch, ZOOM)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}",
            json={"video_link": "https://practice.test/r"},
        )

        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["video_link"] == "https://practice.test/r"
        assert body["provider"] == MANUAL
        assert body["meeting_external_id"] is None
        assert zoom.cancelled == ["81"]

    def test_a_pasted_link_stays_when_the_visit_moves_to_in_person(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _connected(_zoom())
        _prefers(monkeypatch, ZOOM)
        created = write_client.post(
            "/api/appointments",
            json=_create_payload(place_of_service="10", video_link="https://practice.test/r"),
        )

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"place_of_service": "11"}
        )

        assert updated.status_code == 200, updated.text
        assert updated.json()["video_link"] == "https://practice.test/r"
        assert updated.json()["provider"] == MANUAL

    def test_an_edit_that_does_not_touch_the_room_leaves_it_alone(
        self,
        write_client: TestClient,
        calendar: RecordingCalendar,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        zoom = _zoom()
        _connected(zoom)
        _prefers(monkeypatch, ZOOM)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="10")
        )

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"title": "Renamed"}
        )

        assert updated.status_code == 200, updated.text
        assert updated.json()["video_link"] == "https://z.test/81"
        assert zoom.cancelled == []
        assert len(zoom.attendees) == 1


class SlowMeetCalendar(RecordingCalendar):
    """Google answering a conference request with "still making it".

    The push comes back pending, and each read of the event during the save
    answers from ``reads`` in turn — ``None`` for still pending, a link once
    made — repeating the last answer when the script runs out. Once the
    follow-up has started, reads answer ``later`` instead: what Google has
    made by the time the save is long gone. ``read_times`` says when each
    read happened.
    """

    def __init__(self, *reads: str | None, later: str | None = None) -> None:
        super().__init__()
        self._reads = list(reads) or [None]
        self.later = later
        self.following_up = False
        self.read_times: list[float] = []
        self.gave_up = False

    def push_appointment_event(self, _user_id: str, appt: Appointment) -> PushedEvent:
        event = GoogleCalendarService._appointment_to_event(appt)
        self.events.append(event)
        if event.get("conferenceData", {}).get("createRequest") is None:
            return PushedEvent(event_id="evt-1", conference_url=None)
        return PushedEvent(event_id="evt-1", conference_url=None, conference_pending=True)

    def read_appointment_event(self, _user_id: str, event_id: str) -> PushedEvent:
        self.read_times.append(time.monotonic())
        if self.following_up:
            link = self.later
        else:
            link = self._reads[min(len(self.read_times), len(self._reads)) - 1]
        return PushedEvent(
            event_id=event_id,
            conference_url=link,
            conference_pending=link is None and not self.gave_up,
        )


class InlineFollowUp(MeetLinkFollowUp):
    """The follow-up on the test's own services, with no delay.

    The test client runs background tasks before it hands the response back,
    so whatever the follow-up wrote is there for the next request to read.
    """

    delay_seconds = 0.0

    def __init__(self, service: SchedulingService, calendar: SlowMeetCalendar) -> None:
        super().__init__(practice_schema=None)
        self._service = service
        self._calendar = calendar
        self.runs = 0

    @contextlib.contextmanager
    def services(self, user_id: str) -> Iterator[tuple[SchedulingService, Any]]:
        self.runs += 1
        self._calendar.following_up = True
        yield self._service, self._calendar


@pytest.fixture
def wait_budget(monkeypatch: pytest.MonkeyPatch) -> Iterator[float]:
    """A short read-back budget, so a test that runs it out stays fast."""
    monkeypatch.setenv("GOOGLE_MEET_LINK_WAIT_SECONDS", "0.6")
    get_settings.cache_clear()
    yield 0.6
    get_settings.cache_clear()


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment that never holds a save for the link."""
    monkeypatch.setenv("GOOGLE_MEET_LINK_WAIT_SECONDS", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _meet_booking(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, calendar: SlowMeetCalendar
) -> InlineFollowUp:
    """Meet connected and preferred, the calendar and follow-up wired in."""
    appt_repo = InMemoryAppointmentRepository()
    rule_repo = InMemoryAvailabilityRuleRepository()
    service = SchedulingService(appt_repo, AvailabilityEngine(rule_repo, appt_repo))
    follow_up = InlineFollowUp(service, calendar)
    app.dependency_overrides[get_scheduling_service] = lambda: service
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    app.dependency_overrides[get_google_calendar_service] = lambda: calendar
    app.dependency_overrides[get_meet_link_follow_up] = lambda: follow_up
    _connected(RecordingProvider(GOOGLE_MEET, link=None))
    _prefers(monkeypatch, GOOGLE_MEET)
    return follow_up


class TestTheMeetLinkIsThereWhenTheSaveReturns:
    def test_a_link_that_arrives_on_the_first_read_back_is_in_the_create_response(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendar = SlowMeetCalendar(MEET_LINK)
        follow_up = _meet_booking(client, monkeypatch, calendar)

        response = client.post("/api/appointments", json=_create_payload(place_of_service="10"))

        assert response.status_code == 201, response.text
        assert response.json()["video_link"] == MEET_LINK
        assert len(calendar.read_times) == 1
        assert follow_up.runs == 0

    def test_an_edit_to_video_waits_for_its_link_the_same_way(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendar = SlowMeetCalendar(None, MEET_LINK)
        follow_up = _meet_booking(client, monkeypatch, calendar)
        created = client.post("/api/appointments", json=_create_payload(place_of_service="11"))

        updated = client.patch(
            f"/api/appointments/{created.json()['id']}", json={"place_of_service": "10"}
        )

        assert updated.status_code == 200, updated.text
        assert updated.json()["video_link"] == MEET_LINK
        assert follow_up.runs == 0

    def test_past_the_budget_the_save_returns_without_it_and_the_follow_up_writes_it(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, wait_budget: float
    ) -> None:
        calendar = SlowMeetCalendar(None, later=MEET_LINK)
        follow_up = _meet_booking(client, monkeypatch, calendar)
        # The follow-up runs inside the test client's call, after the response
        # is built; time the save up to its last read, which is the wait.
        started = time.monotonic()

        response = client.post("/api/appointments", json=_create_payload(place_of_service="10"))

        assert response.status_code == 201, response.text
        assert response.json()["video_link"] is None
        save_reads = calendar.read_times[:-1]
        assert len(save_reads) >= 2, "the save stopped reading before its budget ran out"
        assert save_reads[-1] - started < wait_budget + 0.5
        assert follow_up.runs == 1
        stored = client.get(f"/api/appointments/{response.json()['id']}")
        assert stored.json()["video_link"] == MEET_LINK

    def test_a_link_still_not_made_at_the_follow_up_leaves_the_appointment_alone(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, wait_budget: float
    ) -> None:
        calendar = SlowMeetCalendar(None)
        follow_up = _meet_booking(client, monkeypatch, calendar)

        response = client.post("/api/appointments", json=_create_payload(place_of_service="10"))

        assert response.status_code == 201, response.text
        assert follow_up.runs == 1
        stored = client.get(f"/api/appointments/{response.json()['id']}")
        assert stored.json()["video_link"] is None
        assert stored.json()["provider"] == GOOGLE_MEET

    def test_a_conference_google_gave_up_on_is_not_read_again(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendar = SlowMeetCalendar(None)
        calendar.gave_up = True
        follow_up = _meet_booking(client, monkeypatch, calendar)

        response = client.post("/api/appointments", json=_create_payload(place_of_service="10"))

        assert response.status_code == 201, response.text
        assert len(calendar.read_times) == 1
        assert follow_up.runs == 0

    @pytest.mark.usefixtures("no_wait")
    def test_a_budget_of_zero_reads_nothing_during_the_save(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendar = SlowMeetCalendar(MEET_LINK, later=MEET_LINK)
        follow_up = _meet_booking(client, monkeypatch, calendar)

        response = client.post("/api/appointments", json=_create_payload(place_of_service="10"))

        assert response.status_code == 201, response.text
        assert response.json()["video_link"] is None
        assert len(calendar.read_times) == 1, "only the follow-up should have read"
        assert follow_up.runs == 1
        stored = client.get(f"/api/appointments/{response.json()['id']}")
        assert stored.json()["video_link"] == MEET_LINK

    def test_the_request_lets_go_of_its_connection_before_it_waits(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendar = SlowMeetCalendar(MEET_LINK)
        _meet_booking(client, monkeypatch, calendar)
        released_before_reads: list[int] = []
        monkeypatch.setattr(
            scheduling,
            "release_db_connection",
            lambda: released_before_reads.append(len(calendar.read_times)),
        )

        client.post("/api/appointments", json=_create_payload(place_of_service="10"))

        assert released_before_reads == [0]

    def test_an_ordinary_appointment_is_never_read_back(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendar = SlowMeetCalendar(MEET_LINK)
        follow_up = _meet_booking(client, monkeypatch, calendar)

        response = client.post("/api/appointments", json=_create_payload(place_of_service="11"))

        assert response.status_code == 201, response.text
        assert calendar.read_times == []
        assert follow_up.runs == 0


class TestTheFollowUpWritesOnlyWhatIsStillWanted:
    """The appointment may have changed in the seconds before the late read."""

    @pytest.fixture
    def booked(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, no_wait: None
    ) -> tuple[SchedulingService, SlowMeetCalendar, str]:
        calendar = SlowMeetCalendar(None)
        _meet_booking(client, monkeypatch, calendar)
        created = client.post("/api/appointments", json=_create_payload(place_of_service="10"))
        service = app.dependency_overrides[get_scheduling_service]()
        calendar.later = MEET_LINK
        return service, calendar, created.json()["id"]

    def _fill(self, booked: tuple[SchedulingService, SlowMeetCalendar, str], user_id: str) -> Any:
        service, calendar, appointment_id = booked
        scheduling._fill_meet_link(service, calendar, user_id, appointment_id, "evt-1")  # type: ignore[arg-type]
        return service.get_appointment(appointment_id, user_id)

    def test_the_link_lands_on_an_appointment_left_as_it_was(
        self, booked: tuple[SchedulingService, SlowMeetCalendar, str], mock_user_id: str
    ) -> None:
        assert self._fill(booked, mock_user_id).video_link == MEET_LINK

    def test_not_onto_one_moved_in_person(
        self, booked: tuple[SchedulingService, SlowMeetCalendar, str], mock_user_id: str
    ) -> None:
        service, _calendar, appointment_id = booked
        service.update_appointment(appointment_id, mock_user_id, provider=None)

        assert self._fill(booked, mock_user_id).video_link is None

    def test_not_over_a_link_pasted_in_meanwhile(
        self, booked: tuple[SchedulingService, SlowMeetCalendar, str], mock_user_id: str
    ) -> None:
        service, _calendar, appointment_id = booked
        service.update_appointment(appointment_id, mock_user_id, video_link="https://p.test/r")

        assert self._fill(booked, mock_user_id).video_link == "https://p.test/r"

    def test_not_onto_one_whose_event_changed(
        self, booked: tuple[SchedulingService, SlowMeetCalendar, str], mock_user_id: str
    ) -> None:
        service, _calendar, appointment_id = booked
        service.update_appointment(appointment_id, mock_user_id, google_event_id="evt-2")

        assert self._fill(booked, mock_user_id).video_link is None

    def test_not_onto_a_cancelled_one(
        self, booked: tuple[SchedulingService, SlowMeetCalendar, str], mock_user_id: str
    ) -> None:
        service, _calendar, appointment_id = booked
        service.update_appointment(appointment_id, mock_user_id, status="cancelled")

        assert self._fill(booked, mock_user_id).video_link is None

    def test_not_onto_another_clinician_s(
        self, booked: tuple[SchedulingService, SlowMeetCalendar, str], mock_user_id: str
    ) -> None:
        service, calendar, appointment_id = booked
        scheduling._fill_meet_link(service, calendar, "someone-else", appointment_id, "evt-1")  # type: ignore[arg-type]

        assert service.get_appointment(appointment_id, mock_user_id).video_link is None


class TestTheFollowUpOwnSession:
    def test_without_a_practice_it_reads_nothing_and_does_not_raise(self) -> None:
        follow_up = MeetLinkFollowUp(practice_schema=None)
        follow_up.delay_seconds = 0.0

        follow_up.run("clinician-1", "appt-1", "evt-1")


class TestTheBudgetSetting:
    def test_it_defaults_to_two_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_MEET_LINK_WAIT_SECONDS", raising=False)

        assert Settings().google_meet_link_wait_seconds == 2.0

    def test_a_deployment_can_set_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_MEET_LINK_WAIT_SECONDS", "3.5")

        assert Settings().google_meet_link_wait_seconds == 3.5

    def test_a_negative_budget_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_MEET_LINK_WAIT_SECONDS", "-1")

        with pytest.raises(ValidationError, match="google_meet_link_wait_seconds"):
            Settings()
