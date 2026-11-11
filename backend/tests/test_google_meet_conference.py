# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Google Meet: the event body asks for a conference, and the answer is read.

The contract this pins is between Pablo and the calendar client, in both
directions. Outbound: an appointment on Meet has to leave with a
``createRequest``, and one that is not must not. Inbound: the link Google
sends back has to be found where Google actually puts it, which is why the
event here is the captured shape rather than one written to match the reader.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from app.meeting_providers.meet import CONFERENCE_SOLUTION_TYPE, GoogleMeetProvider
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.google_calendar_service import (
    GoogleCalendarService,
    PushedEvent,
    conference_pending,
    conference_url,
    wait_for_conference,
)
from app.services.telehealth import GOOGLE_MEET, Attendee, Clinician, Practice

FIXTURES = Path(__file__).parent / "fixtures" / "telehealth"
START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


def google_event() -> dict[str, Any]:
    """An event with a conference, in the shape Google documents."""
    return json.loads((FIXTURES / "google_calendar_event_with_conference.json").read_text())


def google_event_still_making(status: str = "pending") -> dict[str, Any]:
    """The same event before Google has made its conference: a request, no link."""
    event = google_event()
    event.pop("hangoutLink")
    request = event["conferenceData"]["createRequest"]
    request["status"] = {"statusCode": status}
    event["conferenceData"] = {"createRequest": request}
    return event


def an_appointment(**overrides: object) -> Appointment:
    fields: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "user_id": "clinician-1",
        "patient_id": "patient-1",
        "title": "Session",
        "start_at": START,
        "end_at": START + timedelta(minutes=50),
        "duration_minutes": 50,
        "status": AppointmentStatus.CONFIRMED,
        "session_type": "individual",
    }
    fields.update(overrides)
    return Appointment(**fields)  # type: ignore[arg-type]


class TestWhatWeAskGoogleFor:
    def test_a_meet_appointment_asks_google_to_make_the_conference(self) -> None:
        event = GoogleCalendarService._appointment_to_event(an_appointment(provider=GOOGLE_MEET))

        create_request = event["conferenceData"]["createRequest"]
        assert create_request["conferenceSolutionKey"]["type"] == CONFERENCE_SOLUTION_TYPE
        assert create_request["requestId"]

    def test_the_request_id_is_stable_so_a_retry_reuses_the_conference(self) -> None:
        appointment = an_appointment(provider=GOOGLE_MEET)
        first = GoogleCalendarService._appointment_to_event(appointment)
        second = GoogleCalendarService._appointment_to_event(appointment)

        assert (
            first["conferenceData"]["createRequest"]["requestId"]
            == second["conferenceData"]["createRequest"]["requestId"]
        )

    def test_asking_again_after_a_later_write_is_a_new_request(self) -> None:
        # Google ignores a requestId it has already answered and returns no
        # conference, so an appointment that gives its room up and asks again
        # (video, in person, video) must not send the first key twice.
        first_ask = an_appointment(provider=GOOGLE_MEET, updated_at=START)
        asked_again = replace(first_ask, updated_at=START + timedelta(minutes=5))

        first = GoogleCalendarService._appointment_to_event(first_ask)
        second = GoogleCalendarService._appointment_to_event(asked_again)

        assert (
            first["conferenceData"]["createRequest"]["requestId"]
            != second["conferenceData"]["createRequest"]["requestId"]
        )

    def test_a_row_with_no_updated_at_still_asks(self) -> None:
        event = GoogleCalendarService._appointment_to_event(
            an_appointment(id="a-1", provider=GOOGLE_MEET, updated_at=None)
        )

        assert event["conferenceData"]["createRequest"]["requestId"] == "pablo-a-1"

    def test_two_appointments_ask_for_two_conferences(self) -> None:
        first = GoogleCalendarService._appointment_to_event(an_appointment(provider=GOOGLE_MEET))
        second = GoogleCalendarService._appointment_to_event(an_appointment(provider=GOOGLE_MEET))

        assert (
            first["conferenceData"]["createRequest"]["requestId"]
            != second["conferenceData"]["createRequest"]["requestId"]
        )

    def test_an_appointment_that_already_has_a_link_sends_the_link_not_a_request(self) -> None:
        """Somebody chose that room. Asking Google for another would replace it."""
        event = GoogleCalendarService._appointment_to_event(
            an_appointment(provider=GOOGLE_MEET, video_link="https://practice.test/room")
        )

        assert "createRequest" not in event["conferenceData"]
        assert event["conferenceData"]["entryPoints"][0]["uri"] == "https://practice.test/room"

    def test_an_appointment_on_no_provider_asks_for_nothing(self) -> None:
        event = GoogleCalendarService._appointment_to_event(an_appointment())
        assert "conferenceData" not in event

    def test_another_provider_s_appointment_asks_google_for_nothing(self) -> None:
        event = GoogleCalendarService._appointment_to_event(an_appointment(provider="zoom"))
        assert "conferenceData" not in event


class TestReadingGooglesAnswer:
    def test_the_link_is_read_from_the_video_entry_point(self) -> None:
        assert conference_url(google_event()) == "https://meet.example.test/abc-defg-hij"

    def test_a_non_video_entry_point_is_not_mistaken_for_the_room(self) -> None:
        event = google_event()
        event["conferenceData"]["entryPoints"] = [
            {"entryPointType": "more", "uri": "https://tel.example.test/x"}
        ]
        event.pop("hangoutLink")
        assert conference_url(event) is None

    def test_an_event_carrying_only_hangout_link_still_answers(self) -> None:
        event = google_event()
        event.pop("conferenceData")
        assert conference_url(event) == "https://meet.example.test/abc-defg-hij"

    def test_an_event_with_no_conference_answers_nothing(self) -> None:
        assert conference_url({"id": "e1"}) is None

    def test_a_conference_google_has_not_finished_making_is_not_a_failure(self) -> None:
        """``createRequest.status`` comes back pending; the next push reads it."""
        event = google_event()
        event.pop("hangoutLink")
        event["conferenceData"] = {
            "createRequest": {
                "requestId": "pablo-1",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
                "status": {"statusCode": "pending"},
            }
        }
        assert conference_url(event) is None


class TestThePushCarriesTheConferenceFlag:
    def test_an_insert_sends_conference_data_version_one(self) -> None:
        """Without it Google ignores the request, and an update drops the room."""
        service, calendar = _service_with_calendar()
        calendar.events.return_value.insert.return_value.execute.return_value = google_event()

        with patch(
            "app.services.google_calendar_service._build_calendar_service",
            return_value=calendar,
        ):
            pushed = service.push_appointment_event(
                "clinician-1", an_appointment(provider=GOOGLE_MEET)
            )

        assert calendar.events.return_value.insert.call_args.kwargs["conferenceDataVersion"] == 1
        assert pushed is not None
        assert pushed.conference_url == "https://meet.example.test/abc-defg-hij"

    def test_an_update_sends_it_too(self) -> None:
        service, calendar = _service_with_calendar()
        calendar.events.return_value.update.return_value.execute.return_value = google_event()

        with patch(
            "app.services.google_calendar_service._build_calendar_service",
            return_value=calendar,
        ):
            service.push_appointment_event(
                "clinician-1", an_appointment(provider=GOOGLE_MEET, google_event_id="evt-1")
            )

        assert calendar.events.return_value.update.call_args.kwargs["conferenceDataVersion"] == 1


class TestWhoIsOfferedMeet:
    def test_a_clinician_with_no_calendar_is_not_offered_it(self) -> None:
        """The conference lives on the calendar event. No event, no room."""
        provider = GoogleMeetProvider(lambda _user_id: False)
        assert provider.is_connected(Clinician(id="c")) is False

    def test_a_connected_clinician_is(self) -> None:
        provider = GoogleMeetProvider(lambda _user_id: True)
        assert provider.is_connected(Clinician(id="c")) is True

    def test_the_adapter_makes_no_link_of_its_own(self) -> None:
        provider = GoogleMeetProvider(lambda _user_id: True)
        assert (
            provider.create_for_appointment(
                an_appointment(), Clinician(id="c"), Practice(), Attendee()
            )
            is None
        )


def _service_with_calendar() -> tuple[GoogleCalendarService, MagicMock]:
    """A calendar service whose credentials, token row and client are stubs."""
    token_repo = MagicMock()
    token_repo.get.return_value = MagicMock(
        calendar_id="cal-1", event_titling="generic", titling_attested_account=""
    )
    service = GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
        patient_repo=MagicMock(),
    )
    calendar = MagicMock()
    service._get_credentials = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    service._summary_for = MagicMock(return_value="Therapy Session")  # type: ignore[method-assign]
    return service, calendar


class TestTellingAPendingConferenceApart:
    def test_a_request_google_is_still_working_on_is_pending(self) -> None:
        assert conference_pending(google_event_still_making()) is True

    def test_a_finished_conference_is_not(self) -> None:
        assert conference_pending(google_event()) is False

    def test_one_google_gave_up_on_is_not_worth_waiting_for(self) -> None:
        assert conference_pending(google_event_still_making("failure")) is False

    def test_a_link_settles_it_whatever_the_status_says(self) -> None:
        event = google_event()
        event["conferenceData"]["createRequest"]["status"] = {"statusCode": "pending"}
        assert conference_pending(event) is False

    def test_an_event_with_no_conference_is_not(self) -> None:
        assert conference_pending({"id": "e1"}) is False

    def test_the_push_says_so(self) -> None:
        service, calendar = _service_with_calendar()
        calendar.events.return_value.insert.return_value.execute.return_value = (
            google_event_still_making()
        )

        with patch(
            "app.services.google_calendar_service._build_calendar_service",
            return_value=calendar,
        ):
            pushed = service.push_appointment_event(
                "clinician-1", an_appointment(provider=GOOGLE_MEET)
            )

        assert pushed == PushedEvent(
            event_id="ssssssssssssssssssssssssss", conference_url=None, conference_pending=True
        )


class TestReadingTheEventBack:
    def test_it_reads_the_event_and_what_its_conference_says(self) -> None:
        service, calendar = _service_with_calendar()
        calendar.events.return_value.get.return_value.execute.return_value = google_event()

        with patch(
            "app.services.google_calendar_service._build_calendar_service",
            return_value=calendar,
        ):
            read = service.read_appointment_event("clinician-1", "evt-1")

        assert read == PushedEvent(
            event_id="ssssssssssssssssssssssssss",
            conference_url="https://meet.example.test/abc-defg-hij",
            conference_pending=False,
        )

    def test_it_asks_for_the_event_with_only_what_a_get_takes(self) -> None:
        """``conferenceDataVersion`` is a write parameter; the client refuses it on a get."""
        service, calendar = _service_with_calendar()
        calendar.events.return_value.get.return_value.execute.return_value = (
            google_event_still_making()
        )

        with patch(
            "app.services.google_calendar_service._build_calendar_service",
            return_value=calendar,
        ):
            read = service.read_appointment_event("clinician-1", "evt-1")

        assert calendar.events.return_value.get.call_args.kwargs == {
            "calendarId": "cal-1",
            "eventId": "evt-1",
        }
        assert read is not None
        assert read.conference_pending is True

    def test_a_clinician_no_longer_connected_reads_nothing(self) -> None:
        service, calendar = _service_with_calendar()
        service._get_credentials = MagicMock(return_value=None)  # type: ignore[method-assign]

        assert service.read_appointment_event("clinician-1", "evt-1") is None
        calendar.events.assert_not_called()


class FakeClock:
    """Time that moves only when the code under test sleeps, or a read takes some."""

    def __init__(self, read_takes: float = 0.0) -> None:
        self.now = 100.0
        self.read_takes = read_takes
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _reader(clock: FakeClock, *answers: PushedEvent | None) -> tuple[list[float], Any]:
    """Answers in turn, repeating the last; records when each read started."""
    started: list[float] = []

    def read() -> PushedEvent | None:
        started.append(clock.now)
        clock.now += clock.read_takes
        return answers[min(len(started), len(answers)) - 1]

    return started, read


PENDING = PushedEvent(event_id="evt-1", conference_url=None, conference_pending=True)
READY = PushedEvent(
    event_id="evt-1", conference_url="https://meet.test/x", conference_pending=False
)
GAVE_UP = PushedEvent(event_id="evt-1", conference_url=None, conference_pending=False)


class TestWaitingForTheLink:
    def test_it_stops_at_the_first_read_with_a_link(self) -> None:
        clock = FakeClock()
        started, read = _reader(clock, PENDING, READY, PENDING)

        settled = wait_for_conference(read, budget_seconds=2, sleep=clock.sleep, clock=clock.clock)

        assert settled == READY
        assert len(started) == 2

    def test_it_waits_before_each_read_and_backs_off(self) -> None:
        clock = FakeClock()
        _started, read = _reader(clock, PENDING)

        wait_for_conference(read, budget_seconds=10, sleep=clock.sleep, clock=clock.clock)

        assert clock.sleeps[:4] == [0.25, 0.5, 1.0, 1.0]

    def test_it_never_waits_past_the_budget(self) -> None:
        clock = FakeClock(read_takes=0.1)
        started, read = _reader(clock, PENDING)

        settled = wait_for_conference(read, budget_seconds=2, sleep=clock.sleep, clock=clock.clock)

        assert settled == PENDING
        assert started[-1] <= 100.0 + 2
        # The budget plus the one read already under way when it ran out.
        assert clock.now <= 100.0 + 2 + clock.read_takes
        # Reads start at 0.25, 0.85 and 1.95 s; a fourth would start past 2 s.
        assert len(started) == 3

    def test_a_budget_of_zero_reads_nothing(self) -> None:
        clock = FakeClock()
        started, read = _reader(clock, READY)

        assert (
            wait_for_conference(read, budget_seconds=0, sleep=clock.sleep, clock=clock.clock)
            is None
        )
        assert started == []
        assert clock.sleeps == []

    def test_a_conference_google_gave_up_on_ends_the_wait(self) -> None:
        clock = FakeClock()
        started, read = _reader(clock, GAVE_UP, READY)

        settled = wait_for_conference(read, budget_seconds=2, sleep=clock.sleep, clock=clock.clock)

        assert settled == GAVE_UP
        assert len(started) == 1

    def test_a_read_that_fails_ends_the_wait_with_what_was_known(self) -> None:
        clock = FakeClock()
        calls: list[int] = []

        def read() -> PushedEvent:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("Google said no")
            return PENDING

        settled = wait_for_conference(read, budget_seconds=5, sleep=clock.sleep, clock=clock.clock)

        assert settled == PENDING
        assert len(calls) == 2
