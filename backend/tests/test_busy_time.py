# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Busy time from the clinician's calendar, in the times Pablo offers.

The engine takes busy windows from a source; the source reads Google
free/busy (when the connection was granted it) and the outside sessions
still waiting for an answer. These tests run the real engine over the real
source, with Google replaced at the service boundary.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from app.calendar_providers.provider import BusyWindow
from app.repositories.external_calendar_event import (
    ANSWER_CLIENT,
    ANSWER_NOT_A_CLIENT,
    ExternalCalendarEvent,
    InMemoryExternalCalendarEventRepository,
)
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from app.scheduling_engine.models.busy import BusyInterval, BusyKind
from app.scheduling_engine.models.conflict import CALENDAR_BUSY
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import InMemoryAvailabilityRuleRepository
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.services.busy_time import FREE_BUSY_TTL_SECONDS, CalendarBusySource, FreeBusyCache
from app.services.google_calendar_service import BusyCalendars, GoogleCalendarService

USER_ID = "user-1"
#: A Wednesday.
DAY = "2026-04-15"
NY = ZoneInfo("America/New_York")


def _at(hour: int, minute: int = 0, *, day: str = DAY) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00")


def _hours_rule() -> AvailabilityRule:
    """09:00-12:00 on Wednesdays, hourly, so the slots are 09, 10 and 11."""
    return AvailabilityRule(
        id="hours",
        user_id=USER_ID,
        rule_type=RuleType.WORKING_HOURS,
        enforcement=EnforcementLevel.HARD,
        params={"day_of_week": 2, "start": "09:00", "end": "12:00"},
    )


def _hourly_rule() -> AvailabilityRule:
    return AvailabilityRule(
        id="defaults",
        user_id=USER_ID,
        rule_type=RuleType.SESSION_DEFAULTS,
        enforcement=EnforcementLevel.SOFT,
        params={"duration_minutes": 50, "alignment": "hour"},
    )


def _appointment(start: datetime, minutes: int = 50) -> Appointment:
    return Appointment(
        id=f"appt-{start.isoformat()}",
        user_id=USER_ID,
        patient_id="patient-1",
        title="Session",
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        duration_minutes=minutes,
        status="confirmed",
        session_type="individual",
    )


def _outside(
    start: datetime, *, answer: str = "open", minutes: int = 50, event_id: str = "ev-1"
) -> ExternalCalendarEvent:
    return ExternalCalendarEvent(
        id=event_id,
        user_id=USER_ID,
        source="google",
        source_event_id=event_id,
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        title="J.M. via another service",
        calendar_id="followed@group.calendar.google.com",
        answer=answer,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _gcal(*windows: tuple[datetime, datetime]) -> MagicMock:
    """A connected calendar with the busy grant, answering with ``windows``."""
    gcal = MagicMock(spec=GoogleCalendarService)
    gcal.busy_calendars.return_value = BusyCalendars(
        account="clinician@example.test",
        calendar_ids=("primary", "followed@group.calendar.google.com"),
    )
    gcal.query_busy_windows.return_value = [BusyWindow(start=s, end=e) for s, e in windows]
    return gcal


class _World:
    def __init__(self, gcal: MagicMock | None = None) -> None:
        self.rules = InMemoryAvailabilityRuleRepository()
        self.rules.create(_hours_rule())
        self.rules.create(_hourly_rule())
        self.appointments = InMemoryAppointmentRepository()
        self.outside = InMemoryExternalCalendarEventRepository()
        self.clock = _Clock()
        self.cache = FreeBusyCache(clock=self.clock)
        self.gcal = gcal if gcal is not None else _gcal()
        self.source = CalendarBusySource(
            calendar=self.gcal, outside_sessions=self.outside, cache=self.cache
        )
        self.engine = AvailabilityEngine(self.rules, self.appointments, self.source)

    def starts(self, day: str = DAY, *, tz: Any = UTC) -> list[str]:
        return [s.start for s in self.engine.get_free_slots(USER_ID, day, tz=tz).slots]


ALL_THREE = ["2026-04-15T09:00:00Z", "2026-04-15T10:00:00Z", "2026-04-15T11:00:00Z"]


# --- The engine's third input ------------------------------------------------


def test_an_engine_built_without_a_busy_source_behaves_as_before() -> None:
    world = _World()
    plain = AvailabilityEngine(world.rules, world.appointments)

    assert [s.start for s in plain.get_free_slots(USER_ID, DAY).slots] == ALL_THREE


def test_free_slots_leave_out_google_busy_time() -> None:
    world = _World(_gcal((_at(10, 15), _at(10, 30))))

    assert world.starts() == ["2026-04-15T09:00:00Z", "2026-04-15T11:00:00Z"]


def test_busy_time_on_either_calendar_counts() -> None:
    """The followed calendar is read with the main one, in one call."""
    world = _World(_gcal((_at(9, 0), _at(9, 30)), (_at(11, 40), _at(12, 0))))

    assert world.starts() == ["2026-04-15T10:00:00Z"]
    world.gcal.query_busy_windows.assert_called_once()
    assert world.gcal.query_busy_windows.call_args.args[1] == (
        "primary",
        "followed@group.calendar.google.com",
    )


def test_busy_time_that_only_touches_a_slot_does_not_remove_it() -> None:
    """Half-open: busy until 10:00 leaves the 10:00 slot standing."""
    world = _World(_gcal((_at(9, 50), _at(10, 0))))

    assert world.starts() == ALL_THREE


def test_a_partial_minute_of_busy_time_holds_the_whole_minute() -> None:
    world = _World(_gcal((_at(9, 49, day=DAY) + timedelta(seconds=30), _at(9, 50))))

    assert world.starts() == ["2026-04-15T10:00:00Z", "2026-04-15T11:00:00Z"]


def test_busy_time_is_read_in_the_clinicians_own_zone() -> None:
    """10:00 in New York is 14:00Z; a busy block there removes the 10:00 local slot."""
    world = _World(_gcal((datetime(2026, 4, 15, 10, 0, tzinfo=NY), _at(14, 50))))

    assert world.starts(tz=NY) == ["2026-04-15T13:00:00Z", "2026-04-15T15:00:00Z"]


def test_free_slots_leave_out_open_outside_sessions() -> None:
    world = _World(_gcal())
    world.outside.save(_outside(_at(11, 0)))

    assert world.starts() == ["2026-04-15T09:00:00Z", "2026-04-15T10:00:00Z"]


def test_answered_outside_sessions_do_not_block_on_their_own() -> None:
    """A session answered as a client has an appointment, which blocks it; one
    answered as not a client is not the clinician's to keep free."""
    world = _World(_gcal())
    world.outside.save(_outside(_at(9, 0), answer=ANSWER_CLIENT, event_id="ev-1"))
    world.outside.save(_outside(_at(10, 0), answer=ANSWER_NOT_A_CLIENT, event_id="ev-2"))

    assert world.starts() == ALL_THREE


def test_no_busy_grant_changes_nothing_and_reads_nothing() -> None:
    gcal = _gcal((_at(10, 0), _at(11, 0)))
    gcal.busy_calendars.return_value = None
    world = _World(gcal)

    assert world.starts() == ALL_THREE
    gcal.query_busy_windows.assert_not_called()


def test_a_deployment_without_google_still_counts_outside_sessions() -> None:
    world = _World()
    world.source = CalendarBusySource(calendar=None, outside_sessions=world.outside)
    world.engine = AvailabilityEngine(world.rules, world.appointments, world.source)
    world.outside.save(_outside(_at(9, 0)))

    assert world.starts() == ["2026-04-15T10:00:00Z", "2026-04-15T11:00:00Z"]


def test_a_closed_day_never_asks_google() -> None:
    world = _World(_gcal((_at(10, 0), _at(11, 0))))

    # 2026-04-16 is a Thursday, with no hours.
    assert world.starts("2026-04-16") == []
    world.gcal.query_busy_windows.assert_not_called()


def test_a_google_error_falls_back_to_rules_and_appointments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gcal = _gcal()
    gcal.query_busy_windows.side_effect = RuntimeError(
        "followed@group.calendar.google.com: J.M. via another service"
    )
    world = _World(gcal)
    world.appointments.create(_appointment(_at(9, 0)))
    world.outside.save(_outside(_at(11, 0)))

    with caplog.at_level(logging.WARNING, logger="app.services.busy_time"):
        starts = world.starts()

    # Appointments and outside sessions still block; Google simply adds nothing.
    assert starts == ["2026-04-15T10:00:00Z"]
    [record] = [r for r in caplog.records if r.name == "app.services.busy_time"]
    message = record.getMessage()
    assert USER_ID in message
    assert "RuntimeError" in message
    # Nothing Google said about the calendar reaches the log.
    assert "followed@" not in message
    assert "J.M." not in message


# --- Buffers -----------------------------------------------------------------


def _buffer(rule_type: str, minutes: int, *, rule_id: str | None = None) -> AvailabilityRule:
    return AvailabilityRule(
        id=rule_id or f"{rule_type}-{minutes}",
        user_id=USER_ID,
        rule_type=rule_type,
        enforcement=EnforcementLevel.HARD,
        params={"minutes": minutes},
    )


def test_without_a_buffer_the_time_right_after_busy_time_is_offered() -> None:
    world = _World(_gcal((_at(10, 0), _at(11, 0))))

    assert world.starts() == ["2026-04-15T09:00:00Z", "2026-04-15T11:00:00Z"]


def test_a_buffer_after_keeps_its_gap_after_google_busy_time() -> None:
    world = _World(_gcal((_at(10, 0), _at(11, 0))))
    world.rules.create(_buffer(RuleType.BUFFER_AFTER, 15))

    assert "2026-04-15T11:00:00Z" not in world.starts()


def test_a_buffer_after_keeps_its_gap_after_an_open_outside_session() -> None:
    """An outside session is a session: the gap after it is kept the same."""
    world = _World(_gcal())
    world.outside.save(_outside(_at(10, 0), minutes=60))
    world.rules.create(_buffer(RuleType.BUFFER_AFTER, 15))

    assert "2026-04-15T11:00:00Z" not in world.starts()


def test_a_buffer_before_keeps_its_gap_before_busy_time() -> None:
    """09:00-09:50 ends ten minutes before a 10:00 busy block; a 15-minute
    buffer before it needs more than that."""
    world = _World(_gcal((_at(10, 0), _at(10, 30))))
    world.rules.create(_buffer(RuleType.BUFFER_BEFORE, 15))

    assert "2026-04-15T09:00:00Z" not in world.starts()


def test_the_larger_buffer_wins_around_busy_time() -> None:
    world = _World(_gcal((_at(10, 0), _at(11, 0))))
    world.rules.create(_buffer(RuleType.BUFFER_AFTER, 5, rule_id="small"))
    world.rules.create(_buffer(RuleType.BUFFER_AFTER, 15, rule_id="large"))

    assert "2026-04-15T11:00:00Z" not in world.starts()


def test_a_buffer_reaches_into_the_day_from_busy_time_just_before_it() -> None:
    """A window ending at midnight UTC still holds the first minutes of the
    next day when a buffer follows it."""
    rules = InMemoryAvailabilityRuleRepository()
    rules.create(
        AvailabilityRule(
            id="early",
            user_id=USER_ID,
            rule_type=RuleType.WORKING_HOURS,
            enforcement=EnforcementLevel.HARD,
            params={"day_of_week": 2, "start": "00:00", "end": "02:00"},
        )
    )
    rules.create(_hourly_rule())
    rules.create(_buffer(RuleType.BUFFER_AFTER, 15))
    gcal = _gcal((_at(23, 0, day="2026-04-14"), _at(0, 0)))
    source = CalendarBusySource(
        calendar=gcal, outside_sessions=None, cache=FreeBusyCache(clock=_Clock())
    )
    engine = AvailabilityEngine(rules, InMemoryAppointmentRepository(), source)

    starts = [s.start for s in engine.get_free_slots(USER_ID, DAY).slots]

    assert "2026-04-15T00:00:00Z" not in starts
    assert "2026-04-15T01:00:00Z" in starts


def test_the_conflict_check_holds_busy_time_to_the_buffers() -> None:
    world = _World(_gcal((_at(10, 0), _at(11, 0))))

    assert (
        world.engine.check_conflicts(USER_ID, _at(11, 0), _at(11, 50), include_busy=True).conflicts
        == []
    )

    world.rules.create(_buffer(RuleType.BUFFER_AFTER, 15))
    result = world.engine.check_conflicts(USER_ID, _at(11, 0), _at(11, 50), include_busy=True)

    assert [c.rule_type for c in result.conflicts] == [CALENDAR_BUSY]


def test_moving_an_appointment_does_not_report_its_own_buffer_as_busy() -> None:
    """The appointment's own event is busy on the calendar. Nudging it later
    must not find that event's buffer in the way."""
    world = _World(_gcal((_at(10, 0), _at(10, 50))))
    world.rules.create(_buffer(RuleType.BUFFER_AFTER, 15))
    world.appointments.create(_appointment(_at(10, 0)))

    result = world.engine.check_conflicts(USER_ID, _at(10, 15), _at(11, 5), include_busy=True)

    assert [c for c in result.conflicts if c.rule_type == CALENDAR_BUSY] == []


# --- The cache ---------------------------------------------------------------


def test_the_cache_holds_one_google_call_per_window() -> None:
    world = _World(_gcal((_at(10, 0), _at(10, 50))))

    world.starts()
    world.starts()
    # The next week of days is inside what the first call fetched.
    world.starts("2026-04-22")
    world.clock.now += FREE_BUSY_TTL_SECONDS - 1
    world.starts()

    assert world.gcal.query_busy_windows.call_count == 1


def test_the_cache_expires_after_its_window() -> None:
    world = _World(_gcal((_at(10, 0), _at(10, 50))))

    world.starts()
    world.clock.now += FREE_BUSY_TTL_SECONDS
    world.starts()

    assert world.gcal.query_busy_windows.call_count == 2


def test_a_day_outside_the_fetched_window_is_fetched_again() -> None:
    world = _World(_gcal())

    world.starts()
    world.starts("2026-06-03")

    assert world.gcal.query_busy_windows.call_count == 2


def test_a_failed_read_is_not_retried_inside_the_window() -> None:
    """A page listing a fortnight of days waits on an unreachable Google once."""
    gcal = _gcal()
    gcal.query_busy_windows.side_effect = RuntimeError("timeout")
    world = _World(gcal)

    world.starts()
    world.starts("2026-04-22")

    assert gcal.query_busy_windows.call_count == 1


def test_the_cache_is_per_connection() -> None:
    """Another account, or a different followed calendar, is a miss."""
    world = _World(_gcal((_at(10, 0), _at(10, 50))))
    world.starts()

    world.gcal.busy_calendars.return_value = BusyCalendars(
        account="clinician@example.test", calendar_ids=("primary",)
    )
    world.gcal.query_busy_windows.return_value = []
    assert world.starts() == ALL_THREE
    assert world.gcal.query_busy_windows.call_count == 2


# --- The conflict check ------------------------------------------------------


def test_the_conflict_check_reports_busy_time_as_soft() -> None:
    world = _World(_gcal((_at(10, 0), _at(10, 30))))

    result = world.engine.check_conflicts(USER_ID, _at(10, 0), _at(10, 50), include_busy=True)

    [conflict] = result.conflicts
    assert conflict.rule is None
    assert conflict.rule_type == CALENDAR_BUSY
    assert conflict.enforcement == EnforcementLevel.SOFT
    assert conflict.message == "Your calendar shows you as busy at this time"


def test_the_conflict_check_reports_an_open_outside_session() -> None:
    world = _World(_gcal())
    world.outside.save(_outside(_at(10, 0)))

    result = world.engine.check_conflicts(USER_ID, _at(10, 30), _at(11, 20), include_busy=True)

    assert [c.message for c in result.conflicts] == [
        "Overlaps a session on your calendar you haven't told Pablo about yet"
    ]


def test_the_booking_write_path_never_sees_busy_time() -> None:
    """Off by default: the service that books calls this too, and busy time
    never refuses or warns a clinician's own booking."""
    world = _World(_gcal((_at(10, 0), _at(10, 30))))

    result = world.engine.check_conflicts(USER_ID, _at(10, 0), _at(10, 50))

    assert result.conflicts == []
    world.gcal.query_busy_windows.assert_not_called()


def test_an_appointments_own_event_is_not_reported_as_busy() -> None:
    """Pablo's appointments are on the connected calendar too. Editing one
    must not report its own event back as a conflict."""
    world = _World(_gcal((_at(10, 0), _at(10, 50))))
    world.appointments.create(_appointment(_at(10, 0)))

    result = world.engine.check_conflicts(USER_ID, _at(10, 0), _at(10, 50), include_busy=True)

    assert result.conflicts == []


def test_busy_time_beyond_an_appointment_is_still_reported() -> None:
    world = _World(_gcal((_at(10, 0), _at(11, 0))))
    world.appointments.create(_appointment(_at(10, 0), minutes=30))

    result = world.engine.check_conflicts(USER_ID, _at(10, 0), _at(10, 50), include_busy=True)

    assert [c.rule_type for c in result.conflicts] == [CALENDAR_BUSY]


def test_busy_time_that_only_touches_the_proposed_time_is_not_a_conflict() -> None:
    world = _World(_gcal((_at(9, 0), _at(10, 0))))

    result = world.engine.check_conflicts(USER_ID, _at(10, 0), _at(10, 50), include_busy=True)

    assert result.conflicts == []


def test_the_source_answers_only_inside_the_window_asked_for() -> None:
    world = _World(_gcal((_at(8, 0), _at(8, 30)), (_at(10, 0), _at(10, 30))))

    windows = world.source.busy_between(USER_ID, _at(9, 0), _at(12, 0))

    assert windows == [BusyInterval(_at(10, 0), _at(10, 30), BusyKind.CALENDAR)]


# --- Which calendars Google is asked about -----------------------------------


@pytest.fixture
def token_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def calendar_service(token_repo: MagicMock) -> GoogleCalendarService:
    return GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
    )


def _token(**overrides: Any) -> GoogleCalendarTokenDoc:
    fields: dict[str, Any] = {
        "user_id": USER_ID,
        "encrypted_tokens": "encrypted",
        "calendar_id": "pablo@group.calendar.google.com",
        "granted_capabilities": "push,busy",
    }
    fields.update(overrides)
    return GoogleCalendarTokenDoc(**fields)


def test_busy_calendars_are_the_main_and_the_followed_calendar(
    calendar_service: GoogleCalendarService, token_repo: MagicMock
) -> None:
    token_repo.get.return_value = _token(follow_calendar_id="followed@group.calendar.google.com")

    assert calendar_service.busy_calendars(USER_ID) == BusyCalendars(
        account="pablo@group.calendar.google.com",
        calendar_ids=("primary", "followed@group.calendar.google.com"),
    )


def test_following_the_main_calendar_reads_it_once(
    calendar_service: GoogleCalendarService, token_repo: MagicMock
) -> None:
    token_repo.get.return_value = _token(
        follow_calendar_id="clinician@example.test", follows_main_calendar=True
    )

    scope = calendar_service.busy_calendars(USER_ID)

    assert scope is not None
    assert scope.calendar_ids == ("primary",)


def test_no_busy_grant_means_no_busy_calendars(
    calendar_service: GoogleCalendarService, token_repo: MagicMock
) -> None:
    token_repo.get.return_value = _token(granted_capabilities="push,import")
    assert calendar_service.busy_calendars(USER_ID) is None

    token_repo.get.return_value = None
    assert calendar_service.busy_calendars(USER_ID) is None


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        return self.payload


class _FakeFreeBusy:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def query(self, body: dict[str, Any]) -> _FakeRequest:
        self.calls.append(body)
        return _FakeRequest(self.payload)


class _FakeCalendarApi:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.resource = _FakeFreeBusy(payload)

    def freebusy(self) -> _FakeFreeBusy:
        return self.resource


def test_one_query_reads_every_calendar_and_skips_one_google_refuses(
    calendar_service: GoogleCalendarService,
    token_repo: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token_repo.get.return_value = _token()
    api = _FakeCalendarApi(
        {
            "calendars": {
                "primary": {
                    "busy": [{"start": "2026-04-15T10:00:00Z", "end": "2026-04-15T10:30:00Z"}]
                },
                "followed@group.calendar.google.com": {
                    "errors": [{"domain": "global", "reason": "notFound"}],
                    "busy": [],
                },
            }
        }
    )
    with (
        patch("app.services.google_calendar_service.decrypt_tokens", return_value={}),
        patch(
            "app.services.google_calendar_service._make_credentials",
            return_value=MagicMock(expired=False),
        ),
        patch("app.services.google_calendar_service._build_calendar_service", return_value=api),
        caplog.at_level(logging.WARNING, logger="app.services.google_calendar_service"),
    ):
        windows = calendar_service.query_busy_windows(
            USER_ID,
            ("primary", "followed@group.calendar.google.com"),
            _at(0),
            _at(23),
        )

    assert windows == [BusyWindow(start=_at(10, 0), end=_at(10, 30))]
    assert api.resource.calls[0]["items"] == [
        {"id": "primary"},
        {"id": "followed@group.calendar.google.com"},
    ]
    assert "calendar 2 of 2" in caplog.text
    assert "followed@" not in caplog.text
