# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The seam: who is offered, who wins, and when a link is worth showing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
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
    enabled_provider_ids,
    is_joinable,
    join_opens_at,
    normalise_provider,
    provision_meeting,
    release_meeting,
    reminder_join_link,
    resolve_provider_id,
)

START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


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


class FakeProvider:
    """A provider that answers whatever the test needs it to."""

    def __init__(
        self,
        provider_id: str,
        *,
        connected: bool = True,
        link: MeetingLink | None = None,
        raises: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.display_name = provider_id.title()
        self._connected = connected
        self._link = link
        self._raises = raises
        self.cancelled: list[tuple[str, str]] = []

    def is_connected(self, clinician: Clinician) -> bool:
        return self._connected

    def create_for_appointment(
        self,
        appointment: Appointment,
        clinician: Clinician,
        practice: Practice,
        attendee: Attendee,
    ) -> MeetingLink | None:
        if self._raises:
            raise TelehealthError("vendor said no")
        return self._link

    def cancel(self, external_id: str, clinician: Clinician) -> bool:
        self.cancelled.append((external_id, clinician.id))
        return True


class TestReadingAProviderName:
    def test_an_id_is_itself(self) -> None:
        assert normalise_provider("zoom") == ZOOM
        assert normalise_provider("google_meet") == GOOGLE_MEET

    @pytest.mark.parametrize(
        ("label", "expected"),
        [("Zoom", ZOOM), ("Doxy.me", DOXY_ME), ("meet", GOOGLE_MEET), ("DOXY", DOXY_ME)],
    )
    def test_a_label_a_practice_already_has_is_read_as_its_provider(
        self, label: str, expected: str
    ) -> None:
        assert normalise_provider(label) == expected

    def test_an_unrecognised_label_is_a_room_we_do_not_know(self) -> None:
        """Teams, VSee, a phone bridge — the practice has one and we do not."""
        assert normalise_provider("Microsoft Teams") == MANUAL
        assert normalise_provider("other") == MANUAL

    def test_nothing_stays_nothing(self) -> None:
        """An in-person appointment must not acquire a video room by being read."""
        assert normalise_provider(None) is None
        assert normalise_provider("   ") is None

    def test_a_typo_in_the_deployment_list_costs_one_provider_not_the_boot(self) -> None:
        assert enabled_provider_ids(["zoom", "gogle_meet", "manual"]) == (ZOOM, MANUAL)


class TestWhoIsOffered:
    def test_registration_is_not_the_same_as_connection(self) -> None:
        registry = MeetingProviderRegistry(
            [FakeProvider(ZOOM, connected=False), FakeProvider(MANUAL)]
        )
        assert registry.ids() == (ZOOM, MANUAL)
        assert registry.offered_to(Clinician(id="c")) == (MANUAL,)

    def test_the_booking_request_beats_the_preference(self) -> None:
        chosen = resolve_provider_id(
            requested=ZOOM,
            clinician=Clinician(id="c", preferred_provider=DOXY_ME),
            offered=(ZOOM, DOXY_ME),
        )
        assert chosen == ZOOM

    def test_a_request_for_something_unconnected_falls_back_rather_than_failing(self) -> None:
        """A video link is not what the person was trying to do."""
        chosen = resolve_provider_id(
            requested=ZOOM,
            clinician=Clinician(id="c", preferred_provider=DOXY_ME),
            offered=(DOXY_ME,),
        )
        assert chosen == DOXY_ME

    def test_nobody_connected_means_nobody(self) -> None:
        assert (
            resolve_provider_id(
                requested=None,
                clinician=Clinician(id="c", preferred_provider=ZOOM),
                offered=(),
            )
            is None
        )


class TestProvisioning:
    def test_a_pasted_link_wins_outright(self) -> None:
        """Recorded as manual so the cancel path knows there is no vendor."""
        zoom = FakeProvider(ZOOM, link=MeetingLink(url="https://z.test/1", provider=ZOOM))
        appointment = an_appointment(video_link="https://practice.test/room")

        result = provision_meeting(
            appointment,
            clinician=Clinician(id="c", preferred_provider=ZOOM),
            practice=Practice(),
            registry=MeetingProviderRegistry([zoom]),
        )

        assert result.video_link == "https://practice.test/room"
        assert result.provider == MANUAL
        assert result.meeting_external_id is None

    def test_the_provider_s_link_and_handle_are_recorded(self) -> None:
        zoom = FakeProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )

        result = provision_meeting(
            an_appointment(),
            clinician=Clinician(id="c", preferred_provider=ZOOM),
            practice=Practice(),
            registry=MeetingProviderRegistry([zoom]),
        )

        assert result.video_link == "https://z.test/81"
        assert result.provider == ZOOM
        assert result.meeting_external_id == "81"

    def test_a_provider_that_supplies_the_link_another_way_still_gets_recorded(self) -> None:
        """Meet answers None; the calendar push fills the link in afterwards."""
        meet = FakeProvider(GOOGLE_MEET, link=None)

        result = provision_meeting(
            an_appointment(),
            clinician=Clinician(id="c", preferred_provider=GOOGLE_MEET),
            practice=Practice(),
            registry=MeetingProviderRegistry([meet]),
        )

        assert result.provider == GOOGLE_MEET
        assert result.video_link is None

    def test_an_in_person_appointment_is_left_alone(self) -> None:
        appointment = an_appointment()
        result = provision_meeting(
            appointment,
            clinician=Clinician(id="c"),
            practice=Practice(),
            registry=MeetingProviderRegistry([FakeProvider(ZOOM)]),
        )
        assert result is appointment

    def test_a_vendor_failure_raises_for_the_caller_to_decide_about(self) -> None:
        registry = MeetingProviderRegistry([FakeProvider(ZOOM, raises=True)])
        with pytest.raises(TelehealthError):
            provision_meeting(
                an_appointment(),
                clinician=Clinician(id="c", preferred_provider=ZOOM),
                practice=Practice(),
                registry=registry,
            )


class TestReleasing:
    def test_the_vendor_is_told_which_meeting_and_whose(self) -> None:
        zoom = FakeProvider(ZOOM)
        released = release_meeting(
            an_appointment(provider=ZOOM, meeting_external_id="81"),
            clinician=Clinician(id="clinician-1"),
            registry=MeetingProviderRegistry([zoom]),
        )
        assert released is True
        assert zoom.cancelled == [("81", "clinician-1")]

    def test_nothing_to_release_is_not_a_failure(self) -> None:
        zoom = FakeProvider(ZOOM)
        released = release_meeting(
            an_appointment(provider=ZOOM),
            clinician=Clinician(id="clinician-1"),
            registry=MeetingProviderRegistry([zoom]),
        )
        assert released is False
        assert zoom.cancelled == []


class TestTheJoinWindow:
    def test_it_opens_the_configured_number_of_minutes_before_the_start(self) -> None:
        assert join_opens_at(an_appointment(), window_minutes=15) == START - timedelta(minutes=15)

    @pytest.mark.parametrize(
        ("minutes_from_start", "expected"),
        [(-20, False), (-15, True), (-1, True), (0, True), (49, True), (50, True), (51, False)],
    )
    def test_it_runs_from_the_window_opening_until_the_scheduled_end(
        self, minutes_from_start: int, expected: bool
    ) -> None:
        appointment = an_appointment(video_link="https://z.test/1")
        now = START + timedelta(minutes=minutes_from_start)
        assert is_joinable(appointment, now=now, window_minutes=15) is expected

    def test_an_appointment_with_no_link_is_never_joinable(self) -> None:
        assert is_joinable(an_appointment(), now=START, window_minutes=15) is False

    def test_a_cancelled_appointment_keeps_its_link_and_never_offers_it(self) -> None:
        """The row is the record of what was booked. The button is not."""
        appointment = an_appointment(
            video_link="https://z.test/1", status=AppointmentStatus.CANCELLED
        )
        assert appointment.video_link
        assert is_joinable(appointment, now=START, window_minutes=15) is False


class TestWhatAReminderMaySay:
    def test_off_by_default_means_no_link(self) -> None:
        appointment = an_appointment(video_link="https://z.test/1")
        assert reminder_join_link(appointment, include_join_link=False) is None

    def test_on_means_the_link(self) -> None:
        appointment = an_appointment(video_link="https://z.test/1")
        assert reminder_join_link(appointment, include_join_link=True) == "https://z.test/1"

    def test_on_with_nothing_to_say_says_nothing(self) -> None:
        assert reminder_join_link(an_appointment(), include_join_link=True) is None
