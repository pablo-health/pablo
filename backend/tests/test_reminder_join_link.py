# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a message to a client carries the appointment's video link.

On by default. The practice's own switch and the deployment's each turn it
off, and an appointment recorded as in person never carries one. Decided in
one place (``telehealth.client_join_link``), so a reminder and a booking
message cannot answer it differently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from app.db.platform_models import PracticeRow
from app.meeting_providers.doxy import compose_room_url
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.reminder_service import ReminderService
from app.services.telehealth import client_join_link, practice_sends_client_video_link
from app.settings import get_settings

START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
LINK = "https://z.test/81"
ENV = "TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS"


def an_appointment(
    video_link: str | None = LINK, place_of_service: str | None = "10"
) -> Appointment:
    return Appointment(
        id=str(uuid.uuid4()),
        user_id="clinician-1",
        patient_id="patient-1",
        title="Session",
        start_at=START,
        end_at=START + timedelta(minutes=50),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
        video_link=video_link,
        provider="zoom" if video_link else None,
        place_of_service=place_of_service,
    )


def a_session_holding(practice: object | None) -> MagicMock:
    session = MagicMock()
    session.get.return_value = practice
    return session


@pytest.fixture(autouse=True)
def _deployment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every case starts from the deployment's own default, not the runner's env."""
    monkeypatch.delenv(ENV, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _deployment_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "false")
    get_settings.cache_clear()


def test_a_new_practice_sends_the_link() -> None:
    """The column's default is on, in the model and in the database."""
    column = PracticeRow.__table__.c.send_clients_video_link
    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg is True
    assert column.server_default is not None
    assert str(column.server_default.arg) == "true"


def test_the_deployment_allows_it_by_default() -> None:
    assert get_settings().telehealth_include_join_link_in_reminders is True


class TestAReminder:
    def test_a_practice_that_kept_the_default_gets_the_link(self) -> None:
        """Acceptance 1: no stored preference, a video appointment with a link."""
        session = a_session_holding(PracticeRow(id="p-1", send_clients_video_link=True))
        sends = practice_sends_client_video_link(session, "p-1")

        fields = ReminderService.reminder_fields(an_appointment(), practice_sends_link=sends)

        assert fields == {"join_url": LINK}
        session.get.assert_called_once_with(PracticeRow, "p-1")

    def test_a_practice_that_turned_it_off_gets_none(self) -> None:
        session = a_session_holding(PracticeRow(id="p-1", send_clients_video_link=False))
        sends = practice_sends_client_video_link(session, "p-1")

        assert ReminderService.reminder_fields(an_appointment(), practice_sends_link=sends) == {}

    def test_the_deployment_switch_wins_over_the_practice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _deployment_sends_nothing(monkeypatch)
        assert ReminderService.reminder_fields(an_appointment(), practice_sends_link=True) == {}

    def test_the_fields_carry_nothing_else_about_the_appointment(self) -> None:
        """Not the patient, not the id, not which service it is held on."""
        appointment = an_appointment()

        fields = ReminderService.reminder_fields(appointment, practice_sends_link=True)

        assert set(fields) == {"join_url"}
        assert appointment.patient_id not in str(fields)
        assert appointment.id not in str(fields)


class TestWhichAppointmentsCarryOne:
    @pytest.mark.parametrize("place", ["02", "10"])
    def test_a_telehealth_place_carries_it(self, place: str) -> None:
        assert client_join_link(an_appointment(place_of_service=place), practice_sends_link=True)

    def test_an_appointment_with_no_link_carries_none(self) -> None:
        assert client_join_link(an_appointment(video_link=None), practice_sends_link=True) is None

    def test_an_in_person_appointment_carries_none_even_with_a_pasted_link(self) -> None:
        """A visit moved to the office keeps its pasted room; the client is not sent it."""
        appointment = an_appointment(place_of_service="11")
        assert appointment.video_link
        assert client_join_link(appointment, practice_sends_link=True) is None

    def test_an_appointment_from_before_places_were_recorded_keeps_its_link(self) -> None:
        appointment = an_appointment(place_of_service=None)
        assert client_join_link(appointment, practice_sends_link=True) == LINK


class TestReadingThePractice:
    def test_on_when_the_column_is_on(self) -> None:
        practice = SimpleNamespace(send_clients_video_link=True)
        assert practice_sends_client_video_link(a_session_holding(practice), "p-1") is True

    def test_off_when_the_column_is_off(self) -> None:
        practice = SimpleNamespace(send_clients_video_link=False)
        assert practice_sends_client_video_link(a_session_holding(practice), "p-1") is False

    def test_a_practice_that_cannot_be_found_sends_nothing(self) -> None:
        assert practice_sends_client_video_link(a_session_holding(None), "p-gone") is False

    def test_no_practice_id_sends_nothing_and_reads_nothing(self) -> None:
        session = a_session_holding(SimpleNamespace(send_clients_video_link=True))
        assert practice_sends_client_video_link(session, None) is False
        session.get.assert_not_called()


class TestTheLinkCarriesNoName:
    """A message to a client can sit on a lock screen or be forwarded: no name in it."""

    def a_doxy_appointment(self, display_name: str) -> Appointment:
        """The link exactly as the Doxy provider stores it with clinic features on."""
        room = compose_room_url(
            "https://doxy.me/drsmith",
            handle="64MHO85_THBnifdk",
            display_name=display_name,
            clinic_features=True,
        )
        return an_appointment(video_link=room)

    @pytest.mark.parametrize("name", ["Siobhán", "Mary Anne", "Ana+Lu"])
    def test_a_doxy_room_goes_out_without_the_client_name(self, name: str) -> None:
        appointment = self.a_doxy_appointment(name)
        assert "username=" in (appointment.video_link or "")

        link = client_join_link(appointment, practice_sends_link=True)

        assert link is not None
        assert "username" not in link
        assert parse_qs(urlsplit(link).query) == {
            "autocheckin": ["true"],
            "pid": ["64MHO85_THBnifdk"],
        }
        assert urlsplit(link).path == "/drsmith"

    def test_the_reminder_fields_carry_no_name_either(self) -> None:
        fields = ReminderService.reminder_fields(
            self.a_doxy_appointment("Siobhán"), practice_sends_link=True
        )

        assert fields == {
            "join_url": "https://doxy.me/drsmith?autocheckin=true&pid=64MHO85_THBnifdk"
        }

    @pytest.mark.parametrize("key", ["username", "UserName", "user%6Eame"])
    def test_any_link_loses_a_username_however_it_is_spelled(self, key: str) -> None:
        appointment = an_appointment(video_link=f"https://v.test/r?{key}=Siobh%C3%A1n&pwd=a%2Bb")

        assert (
            client_join_link(appointment, practice_sends_link=True) == "https://v.test/r?pwd=a%2Bb"
        )

    def test_a_link_with_only_a_name_loses_the_question_mark_too(self) -> None:
        appointment = an_appointment(video_link="https://v.test/r?username=Siobh%C3%A1n")

        assert client_join_link(appointment, practice_sends_link=True) == "https://v.test/r"

    def test_every_other_parameter_goes_out_as_stored(self) -> None:
        stored = "https://z.test/j/81?pwd=a%2Bb%3D&uname=&room=a%20b&flag&x=1#frag"
        appointment = an_appointment(video_link=stored)

        assert client_join_link(appointment, practice_sends_link=True) == stored


def test_an_empty_stored_link_is_none_not_an_empty_string() -> None:
    """A later caller (the booking message) reads client_join_link directly, not via the fields."""
    assert client_join_link(an_appointment(video_link=""), practice_sends_link=True) is None
