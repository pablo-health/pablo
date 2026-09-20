# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""doxy.me: what goes in the URL a patient is sent, and what must not."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from app.meeting_providers.doxy import DoxyMeProvider, compose_room_url
from app.meeting_providers.pid import HANDLE_BYTES, handle_matches, room_handle
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.telehealth import DOXY_ME, Attendee, Clinician, Practice
from app.settings import get_settings

START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
ROOM = "https://testclinic.example.test/schuyler"


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handle is derived from the deployment's own secret."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", "A" * 43 + "=")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def an_appointment(appointment_id: str | None = None) -> Appointment:
    return Appointment(
        id=appointment_id or str(uuid.uuid4()),
        user_id="clinician-1",
        patient_id="patient-1",
        title="Session",
        start_at=START,
        end_at=START + timedelta(minutes=50),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
    )


class TestTheHandleInTheUrl:
    def test_it_is_not_the_appointment_id_and_contains_none_of_it(self) -> None:
        """The URL is mailed to people and lands in the vendor's access log."""
        appointment_id = "0f5d3c21-9b8a-4e77-8c1a-1d2e3f405162"
        handle = room_handle(appointment_id)

        assert handle != appointment_id
        assert appointment_id not in handle
        for chunk in appointment_id.split("-"):
            assert chunk not in handle

    def test_the_same_appointment_always_gets_the_same_handle(self) -> None:
        """A vendor callback has to be able to find the row again."""
        appointment_id = str(uuid.uuid4())
        assert room_handle(appointment_id) == room_handle(appointment_id)

    def test_two_appointments_get_different_handles(self) -> None:
        assert room_handle(str(uuid.uuid4())) != room_handle(str(uuid.uuid4()))

    def test_it_is_wide_enough_not_to_be_walked(self) -> None:
        handle = room_handle(str(uuid.uuid4()))
        assert HANDLE_BYTES * 8 >= 96
        assert len(handle) >= 16

    def test_a_handle_can_be_confirmed_against_its_appointment(self) -> None:
        appointment_id = str(uuid.uuid4())
        assert handle_matches(appointment_id, room_handle(appointment_id))
        assert not handle_matches(str(uuid.uuid4()), room_handle(appointment_id))


class TestComposingTheRoomUrl:
    def test_without_the_clinic_features_it_is_the_plain_room(self) -> None:
        """Same room; the patient types their name, as they do today."""
        composed = compose_room_url(ROOM, handle="abc", display_name="Mary", clinic_features=False)
        assert composed == ROOM

    def test_with_them_it_carries_the_documented_parameters(self) -> None:
        composed = compose_room_url(
            ROOM, handle="abc123", display_name="Mary", clinic_features=True
        )
        query = parse_qs(urlsplit(composed).query)

        assert urlsplit(composed).path == "/schuyler"
        assert query["username"] == ["Mary"]
        assert query["autocheckin"] == ["true"]
        assert query["pid"] == ["abc123"]

    def test_a_space_in_a_name_is_percent_encoded_not_a_plus(self) -> None:
        """A plus would check somebody in under a name with a plus in it."""
        composed = compose_room_url(
            ROOM, handle="abc", display_name="Mary Anne", clinic_features=True
        )
        assert "username=Mary%20Anne" in composed
        assert "Mary+Anne" not in composed

    def test_no_name_means_no_username_parameter(self) -> None:
        composed = compose_room_url(ROOM, handle="abc", display_name=None, clinic_features=True)
        query = parse_qs(urlsplit(composed).query)
        assert "username" not in query
        assert query["autocheckin"] == ["true"]

    def test_a_query_left_on_the_pasted_room_is_dropped(self) -> None:
        """A stale username from the last time they mailed somebody a link."""
        composed = compose_room_url(
            f"{ROOM}?username=Someone+Else",
            handle="abc",
            display_name="Mary",
            clinic_features=True,
        )
        query = parse_qs(urlsplit(composed).query)
        assert query["username"] == ["Mary"]


class TestTheProvider:
    def test_no_room_means_not_connected_and_no_link(self) -> None:
        provider = DoxyMeProvider()
        assert provider.is_connected(Clinician(id="c")) is False
        assert (
            provider.create_for_appointment(
                an_appointment(), Clinician(id="c"), Practice(), Attendee()
            )
            is None
        )

    def test_the_handle_is_what_is_stored_and_what_is_in_the_url(self) -> None:
        appointment = an_appointment()
        link = DoxyMeProvider().create_for_appointment(
            appointment,
            Clinician(id="c", room_url=ROOM),
            Practice(doxy_clinic_features=True),
            Attendee(display_name="Mary"),
        )

        assert link is not None
        assert link.provider == DOXY_ME
        assert link.external_id == room_handle(appointment.id)
        assert f"pid={link.external_id}" in link.url
        assert appointment.id not in link.url
        assert appointment.patient_id not in link.url

    def test_there_is_nothing_to_cancel(self) -> None:
        assert DoxyMeProvider().cancel("abc", Clinician(id="c")) is False
