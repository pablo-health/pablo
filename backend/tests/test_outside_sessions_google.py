# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Following the main calendar end to end, against a fake Google that records every call.

The promise under test: Pablo reads the clinician's own calendar and never
inserts, patches, updates or deletes an event it did not create.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    calendar_source_identifier,
)
from app.models.patient import Patient
from app.patients.matching import remember_match
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.external_calendar_event import InMemoryExternalCalendarEventRepository
from app.repositories.google_calendar_token import (
    GoogleCalendarTokenDoc,
    GoogleCalendarTokenRepository,
)
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.patient_source_mapping import InMemoryPatientSourceMappingRepository
from app.scheduling_engine.models.appointment import AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.audit_service import AuditService
from app.services.google_calendar_follow import GoogleSyncStatus
from app.services.google_calendar_service import GoogleCalendarService
from app.services.outside_sessions import OutsideSessions
from app.services.sync_scheduler_service import SyncSchedulerService
from app.utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

USER_ID = "test-user-123"
PABLO_CALENDAR = "pablo-made-calendar"
_WRITES = ("insert", "patch", "update", "delete", "move", "quickAdd")


def _in(days: float, hour: int = 14) -> datetime:
    base = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return base.replace(hour=hour)


def _google_event(
    event_id: str, start: datetime, *, series: str | None = "wk", **extra: Any
) -> dict[str, Any]:
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": "Weekly 1:1",
        "recurringEventId": series,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
        **extra,
    }


class _Request:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _GoneError(Exception):
    """Google's answer to a sync token it no longer honours."""

    status_code = 410


class _FakeGoogle:
    """Calendar v3, as far as a sync touches it. Records every call it gets."""

    def __init__(self) -> None:
        self.next_items: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._tokens = 0
        #: Answer the next resumed read of the main calendar with 410 Gone.
        self.expire_main_token = False

    def events(self) -> _FakeGoogle:
        return self

    def list(self, **kwargs: Any) -> _Request:
        if self.expire_main_token and kwargs["calendarId"] == "primary" and kwargs.get("syncToken"):
            self.expire_main_token = False
            raise _GoneError
        self.calls.append(("list", kwargs))
        self._tokens += 1
        items = self.next_items.pop(kwargs["calendarId"], [])
        return _Request({"items": items, "nextSyncToken": f"token-{self._tokens}"})

    def __getattr__(self, name: str) -> Any:
        if name not in _WRITES:
            raise AttributeError(name)

        def write(**kwargs: Any) -> _Request:
            self.calls.append((name, kwargs))
            return _Request({"id": "written"})

        return write

    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if call[0] in _WRITES]


class _Tokens(GoogleCalendarTokenRepository):
    def __init__(self, doc: GoogleCalendarTokenDoc) -> None:
        self.doc = doc

    def get(self, user_id: str) -> GoogleCalendarTokenDoc | None:
        return self.doc if user_id == self.doc.user_id else None

    def list_all(self) -> list[GoogleCalendarTokenDoc]:
        return [self.doc]

    def save(self, token_doc: GoogleCalendarTokenDoc) -> None:
        self.doc = token_doc

    def update_sync_token(self, user_id: str, sync_token: str) -> None:
        self.doc.sync_token = sync_token

    def update_main_calendar_sync_token(self, user_id: str, sync_token: str | None) -> None:
        self.doc.main_calendar_sync_token = sync_token

    def delete(self, user_id: str) -> bool:
        return False

    def exists(self, user_id: str) -> bool:
        return True

    def get_app_calendar_id(self, user_id: str) -> str | None:
        return PABLO_CALENDAR

    def remember_app_calendar_id(self, user_id: str, calendar_id: str) -> None:
        pass

    def set_follow_main_calendar(self, user_id: str, *, follow: bool) -> None:
        self.doc.follow_main_calendar = follow


class _Stack:
    def __init__(self, user: User) -> None:
        self.google = _FakeGoogle()
        self.tokens = _Tokens(
            GoogleCalendarTokenDoc(
                user_id=USER_ID,
                encrypted_tokens="unused",
                write_target="app_calendar",
                granted_capabilities="busy,import,push",
                calendar_id=PABLO_CALENDAR,
                follow_main_calendar=True,
            )
        )
        self.appointments = InMemoryAppointmentRepository()
        self.patients = InMemoryPatientRepository()
        self.mappings = InMemoryPatientSourceMappingRepository()
        self.events = InMemoryExternalCalendarEventRepository()
        self.outside = OutsideSessions(self.events, self.appointments, self.patients, self.mappings)
        self.calendar = GoogleCalendarService(
            self.tokens,
            self.appointments,
            client_id="id",
            client_secret="test-secret",  # noqa: S106
        )
        users = MagicMock()
        users.get.return_value = user
        reminders = MagicMock()
        reminders.check_and_send_reminders.return_value = {}
        ical = MagicMock()
        ical.sync.return_value = []
        self.scheduler = SyncSchedulerService(
            ical_config_repo=MagicMock(),
            google_token_repo=self.tokens,
            user_repo=users,
            ical_sync_service=ical,
            google_calendar_service=self.calendar,
            reminder_service=reminders,
            appointment_repo=self.appointments,
            audit_service=AuditService(InMemoryAuditRepository()),
            outside_sessions=self.outside,
        )

    def poll(self, main_calendar: list[dict[str, Any]]) -> None:
        self.google.next_items["primary"] = main_calendar
        self.scheduler.execute(USER_ID)

    def client(self, patient_id: str, series: str) -> None:
        now = utc_now()
        self.patients.create(
            Patient(
                id=patient_id, first_name="Jane", last_name="Smith", created_at=now, updated_at=now
            ),
            USER_ID,
        )
        self.appointments.grant_access(patient_id, USER_ID)
        remember_match(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(series, "", 0, "00:00"),
            patient_id,
            self.outside.context(USER_ID),
        )


@pytest.fixture
def stack(mock_user: User) -> Iterator[_Stack]:
    s = _Stack(mock_user)
    with (
        patch(
            "app.services.google_calendar_service._build_calendar_service",
            return_value=s.google,
        ),
        patch.object(GoogleCalendarService, "_get_credentials", return_value=object()),
    ):
        yield s


def test_following_a_client_series_writes_nothing_to_google(stack: _Stack) -> None:
    stack.client("p1", "wk")
    start = _in(3)

    stack.poll([_google_event("o1", start), _google_event("o2", _in(10))])
    stack.poll([_google_event("o1", start + timedelta(hours=2))])
    stack.poll([{"id": "o2", "status": "cancelled"}])

    moved = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
    gone = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o2")
    assert moved is not None
    assert moved.start_at == start + timedelta(hours=2)
    assert gone is not None
    assert gone.status == AppointmentStatus.CANCELLED
    assert stack.google.writes() == []


def test_open_questions_write_nothing_to_google(stack: _Stack) -> None:
    stack.poll([_google_event("o1", _in(3)), _google_event("o2", _in(10))])
    stack.poll([{"id": "o1", "status": "cancelled"}])

    assert [row.source_event_id for row in stack.events.list_open(USER_ID)] == ["o2"]
    assert stack.google.writes() == []


def test_nothing_is_read_while_following_is_off(stack: _Stack) -> None:
    stack.tokens.doc.follow_main_calendar = False

    stack.poll([_google_event("o1", _in(3))])

    assert all(kwargs["calendarId"] != "primary" for _, kwargs in stack.google.calls)
    assert stack.events.list_open(USER_ID) == []


def test_nothing_is_read_without_the_grant_to_read_events(stack: _Stack) -> None:
    stack.tokens.doc.granted_capabilities = "busy,push"

    stack.poll([_google_event("o1", _in(3))])

    assert all(kwargs["calendarId"] != "primary" for _, kwargs in stack.google.calls)


def test_the_main_calendar_resumes_from_its_own_sync_token(stack: _Stack) -> None:
    stack.poll([])
    stack.poll([])

    # The fake numbers its tokens in call order: Pablo's calendar is read
    # first (token-1), then the main calendar (token-2), and so on.
    resumed = [(kwargs["calendarId"], kwargs.get("syncToken")) for _, kwargs in stack.google.calls]
    assert resumed == [
        (PABLO_CALENDAR, None),
        ("primary", None),
        (PABLO_CALENDAR, "token-1"),
        ("primary", "token-2"),
    ]


def test_pablos_own_all_day_and_declined_events_are_left_out(stack: _Stack) -> None:
    mine = _google_event(
        "own", _in(3), extendedProperties={"private": {"pablo_appointment_id": "a1"}}
    )
    all_day = {
        "id": "holiday",
        "status": "confirmed",
        "recurringEventId": "yearly",
        "start": {"date": "2099-01-01"},
        "end": {"date": "2099-01-02"},
    }
    declined = _google_event("no", _in(4), attendees=[{"self": True, "responseStatus": "declined"}])

    stack.poll([mine, all_day, declined, _google_event("yes", _in(5))])

    assert [row.source_event_id for row in stack.events.list_open(USER_ID)] == ["yes"]


def _open_ids(stack: _Stack) -> list[str]:
    return sorted(row.source_event_id for row in stack.events.list_open(USER_ID))


class TestAFullReRead:
    """A lapsed sync token means reading everything again, from now on.

    That read never reports what was deleted while the token lapsed, so what
    it leaves out is what is gone.
    """

    def test_an_event_deleted_in_the_gap_takes_its_question_with_it(self, stack: _Stack) -> None:
        stack.poll([_google_event("o1", _in(3)), _google_event("o2", _in(10))])

        stack.google.expire_main_token = True
        stack.poll([_google_event("o2", _in(10))])

        assert _open_ids(stack) == ["o2"]

    def test_answering_afterwards_books_nothing_for_the_vanished_event(self, stack: _Stack) -> None:
        stack.poll([_google_event("o1", _in(3)), _google_event("o2", _in(10))])
        stack.google.expire_main_token = True
        stack.poll([_google_event("o2", _in(10))])
        stack.client("p1", "other-series")

        stack.outside.answer(
            USER_ID,
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier("wk", "", 0, "00:00"),
            patient_id="p1",
        )

        assert (
            stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1") is None
        )
        assert stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o2")

    def _six_booked(self, stack: _Stack) -> list[str]:
        stack.client("p1", "wk")
        ids = [f"o{i}" for i in range(6)]
        stack.poll([_google_event(i, _in(3 + 7 * n)) for n, i in enumerate(ids)])
        return ids

    def test_losing_most_answered_sessions_at_once_is_held_not_cancelled(
        self, stack: _Stack
    ) -> None:
        ids = self._six_booked(stack)

        stack.google.expire_main_token = True
        stack.poll([_google_event("o0", _in(3))])

        for event_id in ids[1:]:
            held = stack.appointments.get_by_outside_event(
                USER_ID, GOOGLE_CALENDAR_SOURCE, event_id
            )
            assert held is not None
            assert held.status == AppointmentStatus.CONFIRMED
            assert held.google_sync_status == GoogleSyncStatus.MISSING_IN_GOOGLE
        assert stack.google.writes() == []

    def test_one_session_gone_in_the_gap_is_cancelled_quietly(self, stack: _Stack) -> None:
        ids = self._six_booked(stack)

        stack.google.expire_main_token = True
        stack.poll([_google_event(i, _in(3 + 7 * n)) for n, i in enumerate(ids) if i != "o5"])

        gone = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o5")
        assert gone is not None
        assert gone.status == AppointmentStatus.CANCELLED
        assert gone.google_sync_status == GoogleSyncStatus.REMOVED_IN_GOOGLE
