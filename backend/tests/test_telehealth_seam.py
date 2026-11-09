# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The seam: who is offered, who wins, and when a link is worth showing."""

from __future__ import annotations

import uuid
from dataclasses import replace
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
    RoomChange,
    TelehealthError,
    enabled_provider_ids,
    is_joinable,
    join_opens_at,
    normalise_provider,
    provision_meeting,
    release_meeting,
    reminder_join_link,
    resolve_provider_id,
    room_change,
    wants_a_room,
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
            an_appointment(place_of_service="10"),
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
            an_appointment(place_of_service="10"),
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
                an_appointment(place_of_service="10"),
                clinician=Clinician(id="c", preferred_provider=ZOOM),
                practice=Practice(),
                registry=registry,
            )


class TestOnlyAVideoVisitGetsARoom:
    """A default service says which room to make, not that every visit is video."""

    @pytest.mark.parametrize("place", [None, "11"])
    def test_an_in_person_visit_gets_no_room_from_the_default(self, place: str | None) -> None:
        meet = FakeProvider(GOOGLE_MEET, link=None)
        appointment = an_appointment(place_of_service=place)

        result = provision_meeting(
            appointment,
            clinician=Clinician(id="c", preferred_provider=GOOGLE_MEET),
            practice=Practice(),
            registry=MeetingProviderRegistry([meet]),
        )

        assert result is appointment
        assert result.provider is None
        assert result.video_link is None

    @pytest.mark.parametrize("place", ["02", "10"])
    def test_a_telehealth_place_gets_the_default_service(self, place: str) -> None:
        meet = FakeProvider(GOOGLE_MEET, link=None)

        result = provision_meeting(
            an_appointment(place_of_service=place),
            clinician=Clinician(id="c", preferred_provider=GOOGLE_MEET),
            practice=Practice(),
            registry=MeetingProviderRegistry([meet]),
        )

        assert result.provider == GOOGLE_MEET

    def test_a_service_asked_for_by_name_is_a_video_visit(self) -> None:
        zoom = FakeProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )

        result = provision_meeting(
            an_appointment(),
            clinician=Clinician(id="c"),
            practice=Practice(),
            registry=MeetingProviderRegistry([zoom]),
            requested_provider=ZOOM,
        )

        assert result.provider == ZOOM

    @pytest.mark.parametrize("place", [None, "11", "10"])
    def test_a_pasted_link_is_kept_whatever_the_place(self, place: str | None) -> None:
        result = provision_meeting(
            an_appointment(place_of_service=place, video_link="https://practice.test/room"),
            clinician=Clinician(id="c", preferred_provider=GOOGLE_MEET),
            practice=Practice(),
            registry=MeetingProviderRegistry([FakeProvider(GOOGLE_MEET)]),
        )

        assert result.video_link == "https://practice.test/room"
        assert result.provider == MANUAL

    @pytest.mark.parametrize(
        ("place", "requested", "link", "expected"),
        [
            (None, None, None, False),
            ("11", None, None, False),
            ("02", None, None, True),
            ("10", None, None, True),
            ("11", "zoom", None, True),
            (None, None, "https://practice.test/room", True),
        ],
    )
    def test_the_rule_matches_the_one_the_client_shows(
        self, place: str | None, requested: str | None, link: str | None, expected: bool
    ) -> None:
        appointment = an_appointment(place_of_service=place, video_link=link)
        assert wants_a_room(appointment, requested_provider=requested) is expected


def _both_connected() -> MeetingProviderRegistry:
    return MeetingProviderRegistry([FakeProvider(GOOGLE_MEET), FakeProvider(ZOOM)])


def _decide(
    previous: Appointment, edited: Appointment, *, requested: str | None = None
) -> RoomChange:
    return room_change(
        previous,
        edited,
        requested_provider=requested,
        clinician=Clinician(id="c", preferred_provider=GOOGLE_MEET),
        registry=_both_connected(),
    )


class TestAnEditAndItsRoom:
    def test_in_person_to_video_makes_a_room(self) -> None:
        before = an_appointment(place_of_service="11")
        assert _decide(before, replace(before, place_of_service="10")) is RoomChange.PROVISION

    @pytest.mark.parametrize(
        ("provider", "link", "handle"),
        [(ZOOM, "https://z.test/81", "81"), (GOOGLE_MEET, "https://meet.test/abc", None)],
    )
    def test_video_to_in_person_gives_the_room_up(
        self, provider: str, link: str, handle: str | None
    ) -> None:
        before = an_appointment(
            place_of_service="10", provider=provider, video_link=link, meeting_external_id=handle
        )
        assert _decide(before, replace(before, place_of_service="11")) is RoomChange.RELEASE

    def test_asking_for_another_service_replaces_the_room(self) -> None:
        before = an_appointment(
            place_of_service="10", provider=GOOGLE_MEET, video_link="https://meet.test/abc"
        )
        assert _decide(before, before, requested=ZOOM) is RoomChange.REPLACE

    def test_asking_for_the_service_it_already_has_keeps_the_room(self) -> None:
        before = an_appointment(
            place_of_service="10", provider=ZOOM, video_link="https://z.test/81"
        )
        assert _decide(before, before, requested="Zoom") is RoomChange.KEEP

    def test_moving_between_the_two_telehealth_places_keeps_the_room(self) -> None:
        """The client has the link already; a billing correction must not change it."""
        before = an_appointment(
            place_of_service="10", provider=ZOOM, video_link="https://z.test/81"
        )
        assert _decide(before, replace(before, place_of_service="02")) is RoomChange.KEEP

    def test_an_edit_that_does_not_touch_the_room_keeps_it(self) -> None:
        """A room booked with no place on record stays put when the time moves."""
        before = an_appointment(provider=ZOOM, video_link="https://z.test/81")
        moved = replace(before, start_at=START + timedelta(hours=1))
        assert _decide(before, moved) is RoomChange.KEEP

    def test_a_newly_pasted_link_becomes_the_room(self) -> None:
        before = an_appointment(
            place_of_service="10", provider=ZOOM, video_link="https://z.test/81"
        )
        edited = replace(before, video_link="https://practice.test/room")
        assert _decide(before, edited) is RoomChange.MANUAL

    def test_a_pasted_link_survives_a_move_to_in_person(self) -> None:
        """Only the clinician takes off a link the clinician typed."""
        before = an_appointment(
            place_of_service="10", provider=MANUAL, video_link="https://practice.test/room"
        )
        edited = replace(before, place_of_service="11")
        assert _decide(before, edited) is RoomChange.KEEP
        assert _decide(before, before, requested=ZOOM) is RoomChange.KEEP

    def test_in_person_staying_in_person_has_nothing_to_give_up(self) -> None:
        before = an_appointment(place_of_service=None)
        assert _decide(before, replace(before, place_of_service="11")) is RoomChange.KEEP


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
