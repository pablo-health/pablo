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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from app.meeting_providers.meet import CONFERENCE_SOLUTION_TYPE, GoogleMeetProvider
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.google_calendar_service import GoogleCalendarService, conference_url
from app.services.telehealth import GOOGLE_MEET, Attendee, Clinician, Practice

FIXTURES = Path(__file__).parent / "fixtures" / "telehealth"
START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


def google_event() -> dict[str, Any]:
    """An event with a conference, in the shape Google documents."""
    return json.loads((FIXTURES / "google_calendar_event_with_conference.json").read_text())


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
