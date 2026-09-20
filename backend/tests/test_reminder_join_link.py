# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a reminder is allowed to say about a telehealth appointment.

The default is the whole point: a reminder is read on a lock screen and
forwarded, so it carries the time and the practice's name until a practice
decides otherwise. One switch, applied in one place, so two dispatchers
cannot answer it differently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.reminder_service import ReminderService
from app.settings import get_settings

START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


def an_appointment(video_link: str | None = "https://z.test/81") -> Appointment:
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
        provider="zoom",
    )


@pytest.fixture(autouse=True)
def _clear_settings() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_reminder_carries_no_link_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS", raising=False)
    get_settings.cache_clear()
    assert ReminderService.reminder_fields(an_appointment()) == {}


def test_a_practice_that_turns_it_on_gets_the_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS", "true")
    get_settings.cache_clear()
    assert ReminderService.reminder_fields(an_appointment()) == {"join_url": "https://z.test/81"}


def test_on_with_nothing_to_say_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An in-person appointment gets the same reminder either way."""
    monkeypatch.setenv("TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS", "true")
    get_settings.cache_clear()
    assert ReminderService.reminder_fields(an_appointment(video_link=None)) == {}


def test_the_fields_carry_nothing_else_about_the_appointment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the patient, not the id, not which service it is held on."""
    monkeypatch.setenv("TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS", "true")
    get_settings.cache_clear()
    appointment = an_appointment()

    fields = ReminderService.reminder_fields(appointment)

    assert set(fields) == {"join_url"}
    assert appointment.patient_id not in str(fields)
    assert appointment.id not in str(fields)
