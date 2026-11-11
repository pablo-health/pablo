# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A video appointment saved without a link logs one warning saying why (PABLO-en9h.7).

Every way an appointment can end up by video with no link was silent: no
service connected, a vendor that refused, a calendar push that failed, a
calendar that is not connected, Google still making the conference when the
last read gave up, and a save that never asked for a room at all. Each one
now logs ``telehealth_link_missing`` once, with a reason code and nothing
about the client. These drive the real routes with the fakes the write-path
tests use, and read the record itself rather than the formatted line.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.main import app
from app.repositories.booking_link import InMemoryBookingLinkRepository
from app.routes import patient_booking, scheduling
from app.routes.scheduling import get_google_calendar_service, get_meeting_registry
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.google_calendar_service import GoogleCalendarService, PushedEvent
from app.services.telehealth import (
    GOOGLE_MEET,
    MISSING_LINK_MESSAGE,
    ZOOM,
    Attendee,
    LinkTrail,
    MeetingProviderRegistry,
    MissingLinkReason,
    log_missing_link,
)
from app.settings import get_settings

from .test_client_booking_gets_a_room import (
    PATHS,
    _client,
    _meet,
)
from .test_client_booking_gets_a_room import (
    _zoom as _client_zoom,
)
from .test_telehealth_on_the_write_path import (
    MEET_LINK,
    RecordingCalendar,
    RecordingProvider,
    SlowMeetCalendar,
    _connected,
    _create_payload,
    _meet_booking,
    _prefers,
    _zoom,
    write_client,  # noqa: F401  (a fixture, used by name)
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

TITLE = "Weekly check-in with Mary Quinn"
NOTES = "Mary mentioned her sister Ruth"
FIRST_NAME = "Mary"

#: The only fields the record may add to a standard ``LogRecord``.
_FIELDS = {"reason", "requested_provider", "appointment_id"}
_STANDARD = set(logging.LogRecord("x", logging.INFO, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
}


@pytest.fixture
def warnings(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.WARNING, logger="app.services.telehealth")
    return caplog


def _missing(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.msg == MISSING_LINK_MESSAGE]


def _only_reason(caplog: pytest.LogCaptureFixture) -> str:
    records = _missing(caplog)
    assert len(records) == 1, [getattr(r, "reason", None) for r in records]
    return str(records[0].reason)  # type: ignore[attr-defined]


def _video(**overrides: Any) -> dict[str, Any]:
    return _create_payload(place_of_service="10", title=TITLE, notes=NOTES, **overrides)


class FailingCalendar(RecordingCalendar):
    """A Google push that throws."""

    def push_appointment_event(self, _user_id: str, appt: Appointment) -> PushedEvent:
        raise RuntimeError("google is down")


class DisconnectedCalendar(RecordingCalendar):
    """A clinician with no calendar connection: every push answers None."""

    def push_appointment_event(self, _user_id: str, appt: Appointment) -> PushedEvent | None:
        self.events.append(GoogleCalendarService._appointment_to_event(appt))
        return None


class NoConferenceCalendar(RecordingCalendar):
    """Google takes the event and answers the conference request with nothing."""

    def push_appointment_event(self, _user_id: str, appt: Appointment) -> PushedEvent:
        self.events.append(GoogleCalendarService._appointment_to_event(appt))
        return PushedEvent(event_id="evt-1", conference_url=None, conference_pending=False)


def _calendar(calendar: RecordingCalendar) -> RecordingCalendar:
    app.dependency_overrides[get_google_calendar_service] = lambda: calendar
    return calendar


@pytest.fixture
def wait_budget(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_MEET_LINK_WAIT_SECONDS", "0.3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestEachReasonOnTheClinicianSave:
    def test_no_offered_service(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(RecordingCalendar())
        app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([])
        _prefers(monkeypatch, ZOOM)

        response = write_client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert response.json()["video_link"] is None
        assert _only_reason(warnings) == MissingLinkReason.NO_OFFERED_SERVICE

    def test_provider_failed_when_the_vendor_refuses(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(RecordingCalendar())
        _connected(RecordingProvider(ZOOM, link=None, raises=True))
        _prefers(monkeypatch, ZOOM)

        response = write_client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert _only_reason(warnings) == MissingLinkReason.PROVIDER_FAILED

    def test_provider_failed_when_google_makes_no_conference(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(NoConferenceCalendar())
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert _only_reason(warnings) == MissingLinkReason.PROVIDER_FAILED

    def test_push_failed(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(FailingCalendar())
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert _only_reason(warnings) == MissingLinkReason.PUSH_FAILED

    def test_calendar_disconnected(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calendar = _calendar(DisconnectedCalendar())
        _connected(RecordingProvider(GOOGLE_MEET, link=None))
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert len(calendar.events) == 1
        assert _only_reason(warnings) == MissingLinkReason.CALENDAR_DISCONNECTED

    @pytest.mark.usefixtures("wait_budget")
    def test_conference_pending_timeout_is_logged_by_the_late_read_not_the_save(
        self,
        client: TestClient,
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calendar = SlowMeetCalendar(None)
        follow_up = _meet_booking(client, monkeypatch, calendar)

        response = client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert follow_up.runs == 1
        # One record across the save and the late read: the save stays quiet
        # while a read is still to come.
        assert _only_reason(warnings) == MissingLinkReason.CONFERENCE_PENDING_TIMEOUT

    @pytest.mark.usefixtures("wait_budget")
    def test_a_late_read_google_gave_up_on_is_provider_failed(
        self,
        client: TestClient,
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calendar = SlowMeetCalendar(None)
        follow_up = _meet_booking(client, monkeypatch, calendar)
        # Pending through the save; by the late read Google has given up.
        original = calendar.read_appointment_event

        def read(user_id: str, event_id: str) -> PushedEvent:
            calendar.gave_up = calendar.following_up
            return original(user_id, event_id)

        monkeypatch.setattr(calendar, "read_appointment_event", read)

        response = client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert follow_up.runs == 1
        assert _only_reason(warnings) == MissingLinkReason.PROVIDER_FAILED

    @pytest.mark.usefixtures("wait_budget")
    def test_a_late_read_that_raises_is_conference_pending_timeout(
        self,
        client: TestClient,
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Google busy on the one late read (a 429, a 5xx): the reason is still said."""
        calendar = SlowMeetCalendar(None)
        follow_up = _meet_booking(client, monkeypatch, calendar)
        original = calendar.read_appointment_event

        def read(user_id: str, event_id: str) -> PushedEvent:
            if calendar.following_up:
                raise RuntimeError("google is busy")
            return original(user_id, event_id)

        monkeypatch.setattr(calendar, "read_appointment_event", read)

        response = client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert follow_up.runs == 1
        assert _only_reason(warnings) == MissingLinkReason.CONFERENCE_PENDING_TIMEOUT

    @pytest.mark.usefixtures("wait_budget")
    def test_a_late_read_that_finds_no_calendar_is_calendar_disconnected(
        self,
        client: TestClient,
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The clinician disconnected between the save and the late read."""
        calendar = SlowMeetCalendar(None)
        follow_up = _meet_booking(client, monkeypatch, calendar)
        original = calendar.read_appointment_event

        def read(user_id: str, event_id: str) -> PushedEvent | None:
            if calendar.following_up:
                return None
            return original(user_id, event_id)

        monkeypatch.setattr(calendar, "read_appointment_event", read)

        response = client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert follow_up.runs == 1
        assert _only_reason(warnings) == MissingLinkReason.CALENDAR_DISCONNECTED

    def test_never_provisioned_on_an_edit_that_asks_for_no_room(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An appointment by video with no room, edited without touching where it happens."""
        _calendar(RecordingCalendar())
        app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([])
        _prefers(monkeypatch, ZOOM)
        created = write_client.post("/api/appointments", json=_video())
        assert created.status_code == 201, created.text
        warnings.clear()

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"title": "Moved"}
        )

        assert updated.status_code == 200, updated.text
        assert _only_reason(warnings) == MissingLinkReason.NEVER_PROVISIONED

    def test_marking_a_video_visit_completed_logs_nothing(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Charting a session that already happened is not a missing link."""
        _calendar(RecordingCalendar())
        app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([])
        _prefers(monkeypatch, ZOOM)
        created = write_client.post("/api/appointments", json=_video())
        assert created.status_code == 201, created.text
        warnings.clear()

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"status": "completed"}
        )

        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "completed"
        assert _missing(warnings) == []

    def test_an_edit_to_video_with_nothing_connected_is_no_offered_service(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(RecordingCalendar())
        app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([])
        _prefers(monkeypatch, ZOOM)
        created = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service="11")
        )
        assert _missing(warnings) == []

        updated = write_client.patch(
            f"/api/appointments/{created.json()['id']}", json={"place_of_service": "10"}
        )

        assert updated.status_code == 200, updated.text
        assert _only_reason(warnings) == MissingLinkReason.NO_OFFERED_SERVICE


class TestNothingIsLoggedWhenNothingIsMissing:
    def test_a_video_visit_that_gets_its_zoom_link(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(RecordingCalendar())
        _connected(_zoom())
        _prefers(monkeypatch, ZOOM)

        response = write_client.post("/api/appointments", json=_video())

        assert response.json()["video_link"] == "https://z.test/81"
        assert _missing(warnings) == []

    def test_a_video_visit_that_gets_its_meet_link_on_the_push(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(RecordingCalendar())
        _connected(_meet())
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post("/api/appointments", json=_video())

        assert response.json()["video_link"] == MEET_LINK
        assert _missing(warnings) == []

    @pytest.mark.usefixtures("wait_budget")
    def test_a_meet_link_the_late_read_fills_in(
        self,
        client: TestClient,
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calendar = SlowMeetCalendar(None, later=MEET_LINK)
        follow_up = _meet_booking(client, monkeypatch, calendar)

        response = client.post("/api/appointments", json=_video())

        assert response.status_code == 201, response.text
        assert follow_up.runs == 1
        assert _missing(warnings) == []

    def test_a_pasted_link(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(RecordingCalendar())
        app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([])

        response = write_client.post(
            "/api/appointments", json=_video(video_link="https://practice.test/room")
        )

        assert response.status_code == 201, response.text
        assert _missing(warnings) == []

    @pytest.mark.parametrize("place", ["11", None])
    def test_an_in_person_visit(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        place: str | None,
    ) -> None:
        """Even with every way to a link broken, an in-person visit wants none."""
        _calendar(FailingCalendar())
        app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([])
        _prefers(monkeypatch, GOOGLE_MEET)

        response = write_client.post(
            "/api/appointments", json=_create_payload(place_of_service=place)
        )

        assert response.status_code == 201, response.text
        assert _missing(warnings) == []


class TestTheRecordCarriesNothingAboutTheClient:
    def test_only_the_reason_the_service_and_the_id(
        self,
        write_client: TestClient,  # noqa: F811
        warnings: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _calendar(RecordingCalendar())
        _connected(RecordingProvider(ZOOM, link=None, raises=True))
        _prefers(monkeypatch, ZOOM)
        monkeypatch.setattr(
            scheduling, "_attendee_view", lambda _repo, _appt: Attendee(display_name=FIRST_NAME)
        )

        response = write_client.post("/api/appointments", json=_video(provider=ZOOM))

        assert response.status_code == 201, response.text
        [record] = _missing(warnings)
        assert record.getMessage() == MISSING_LINK_MESSAGE
        assert not record.args
        extra = {k: v for k, v in record.__dict__.items() if k not in _STANDARD}
        assert extra == {
            "reason": MissingLinkReason.PROVIDER_FAILED.value,
            "requested_provider": ZOOM,
            "appointment_id": response.json()["id"],
        }
        assert set(extra) == _FIELDS
        everything = json.dumps({k: str(v) for k, v in record.__dict__.items()})
        for phi in (TITLE, NOTES, FIRST_NAME, "Quinn", "Ruth", "patient-1"):
            assert phi not in everything


@pytest.fixture
def link_repo() -> InMemoryBookingLinkRepository:
    return InMemoryBookingLinkRepository()


class TestAClientBookingLogsTheSameWay:
    @pytest.mark.parametrize("path", sorted(PATHS))
    def test_a_vendor_that_refuses_is_provider_failed(
        self,
        path: str,
        link_repo: InMemoryBookingLinkRepository,
        monkeypatch: pytest.MonkeyPatch,
        warnings: pytest.LogCaptureFixture,
    ) -> None:
        client, _rooms = _client(
            link_repo, monkeypatch, place="02", provider=_client_zoom(raises=True)
        )

        appt = PATHS[path](client, link_repo)

        assert appt.video_link is None
        assert _only_reason(warnings) == MissingLinkReason.PROVIDER_FAILED

    @pytest.mark.parametrize("path", sorted(PATHS))
    def test_a_booking_that_gets_its_link_logs_nothing(
        self,
        path: str,
        link_repo: InMemoryBookingLinkRepository,
        monkeypatch: pytest.MonkeyPatch,
        warnings: pytest.LogCaptureFixture,
    ) -> None:
        client, _rooms = _client(link_repo, monkeypatch, place="02", provider=_client_zoom())

        appt = PATHS[path](client, link_repo)

        assert appt.video_link is not None
        assert _missing(warnings) == []

    @pytest.mark.parametrize("place", ["11", None])
    def test_an_in_person_booking_logs_nothing(
        self,
        place: str | None,
        link_repo: InMemoryBookingLinkRepository,
        monkeypatch: pytest.MonkeyPatch,
        warnings: pytest.LogCaptureFixture,
    ) -> None:
        client, _rooms = _client(
            link_repo, monkeypatch, place=place, provider=_client_zoom(raises=True)
        )

        PATHS["instant"](client, link_repo)

        assert _missing(warnings) == []


class TestAPortalBooking:
    """The client portal asks no video service for a room, so its reason is never_provisioned.

    Driven at the portal's push, over fakes for the owner session: the route
    itself needs a real practice database (``tests_integration``).
    """

    def _push(
        self, monkeypatch: pytest.MonkeyPatch, appt: Appointment, calendar: RecordingCalendar
    ) -> Appointment:
        repo = InMemoryAppointmentRepository()
        repo.create(appt)
        service = SchedulingService(repo)
        user = MagicMock(id=appt.user_id)
        monkeypatch.setattr(
            patient_booking,
            "PostgresUserRepository",
            lambda _session: MagicMock(get=lambda _id: user),
        )
        monkeypatch.setattr(
            patient_booking, "_owner_calendar", lambda _session: (service, calendar)
        )
        return patient_booking._push_to_calendar(MagicMock(), appt.user_id, appt)

    def test_a_video_booking_is_never_provisioned(
        self, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
    ) -> None:
        pushed = self._push(monkeypatch, _appointment(place_of_service="02"), RecordingCalendar())

        assert pushed.google_event_id == "evt-1"
        assert _only_reason(warnings) == MissingLinkReason.NEVER_PROVISIONED

    def test_even_when_the_push_itself_fails(
        self, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
    ) -> None:
        self._push(monkeypatch, _appointment(place_of_service="02"), FailingCalendar())

        assert _only_reason(warnings) == MissingLinkReason.NEVER_PROVISIONED

    def test_even_when_the_owner_lookup_fails(
        self, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
    ) -> None:
        """A failure around the push, not inside it, still says why there is no link."""

        def broken(_session: Any) -> Any:
            raise RuntimeError("owner session is gone")

        monkeypatch.setattr(patient_booking, "PostgresUserRepository", broken)
        appt = _appointment(place_of_service="02")

        returned = patient_booking._push_to_calendar(MagicMock(), appt.user_id, appt)

        assert returned is appt
        assert _only_reason(warnings) == MissingLinkReason.NEVER_PROVISIONED

    def test_a_meet_answer_still_pending_with_no_late_read_to_come(
        self, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
    ) -> None:
        """The portal's push waits for nothing, so a pending conference is already late."""
        appt = _appointment(place_of_service="02", provider=GOOGLE_MEET)

        self._push(monkeypatch, appt, SlowMeetCalendar(None))

        assert _only_reason(warnings) == MissingLinkReason.CONFERENCE_PENDING_TIMEOUT

    def test_an_in_person_booking_logs_nothing(
        self, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
    ) -> None:
        self._push(monkeypatch, _appointment(place_of_service="11"), RecordingCalendar())

        assert _missing(warnings) == []


def _appointment(**overrides: Any) -> Appointment:
    fields: dict[str, Any] = {
        "id": "appt-1",
        "user_id": "owner-1",
        "patient_id": "patient-1",
        "title": TITLE,
        "start_at": datetime(2026, 12, 1, 14, tzinfo=UTC),
        "end_at": datetime(2026, 12, 1, 14, 50, tzinfo=UTC),
        "duration_minutes": 50,
        "status": AppointmentStatus.CONFIRMED,
        "session_type": "individual",
        "notes": NOTES,
    }
    fields.update(overrides)
    return Appointment(**fields)


class TestTheHelperOnItsOwn:
    @pytest.mark.parametrize(
        "status",
        [
            AppointmentStatus.PENDING,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.NO_SHOW,
            AppointmentStatus.COMPLETED,
        ],
    )
    def test_an_appointment_not_going_ahead_logs_nothing(
        self, status: AppointmentStatus, warnings: pytest.LogCaptureFixture
    ) -> None:
        """A request gets its room when it is agreed to; nobody joins a cancelled,
        missed or finished one."""
        logged = log_missing_link(
            _appointment(place_of_service="02", status=status), trail=LinkTrail()
        )

        assert logged is False
        assert _missing(warnings) == []

    def test_a_read_still_to_come_logs_nothing(self, warnings: pytest.LogCaptureFixture) -> None:
        logged = log_missing_link(
            _appointment(place_of_service="02", provider=GOOGLE_MEET),
            trail=LinkTrail(reading_back=True),
        )

        assert logged is False
        assert _missing(warnings) == []

    def test_a_service_asked_for_by_name_makes_it_a_video_visit(
        self, warnings: pytest.LogCaptureFixture
    ) -> None:
        logged = log_missing_link(
            _appointment(place_of_service=None),
            trail=LinkTrail(MissingLinkReason.NO_OFFERED_SERVICE),
            requested_provider="Zoom",
        )

        assert logged is True
        [record] = _missing(warnings)
        assert record.requested_provider == ZOOM  # type: ignore[attr-defined]
