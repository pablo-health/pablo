# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Booking an appointment on a video service, end to end through the route.

The seam's own tests prove the rules; these prove the wiring — that the route
asks the registry, persists what comes back, releases the meeting when the
appointment is cancelled, and never fails a booking over a vendor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.routes import scheduling
from app.routes.scheduling import (
    get_availability_rule_repository,
    get_google_calendar_service,
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

if TYPE_CHECKING:
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
