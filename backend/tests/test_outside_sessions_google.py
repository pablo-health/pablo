# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Following the main calendar end to end, against a fake Google that records every call.

The promise under test: Pablo reads the clinician's own calendar and never
inserts, patches, updates or deletes an event it did not create.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    answered_title_digest,
    calendar_source_identifier,
)
from app.main import app
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
from app.routes.outside_sessions import get_outside_sessions
from app.routes.scheduling import get_google_calendar_service
from app.scheduling_engine.models.appointment import AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.audit_service import AuditService
from app.services.google_calendar_follow import GoogleSyncStatus
from app.services.google_calendar_service import GoogleCalendarService
from app.services.outside_sessions import OutsideSessions
from app.services.sync_scheduler_service import SyncSchedulerService
from app.settings import get_settings
from app.utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from app.models import User
    from fastapi.testclient import TestClient

USER_ID = "test-user-123"
PABLO_CALENDAR = "pablo-made-calendar"
#: The main calendar's real id, which ``primary`` resolves to.
MAIN = "clinician@example.test"
_WRITES = ("insert", "patch", "update", "delete", "move", "quickAdd")


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """The secret the answered-title digest is keyed under; every answer needs it."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


class _FakeCalendarList:
    """The calendar list: the main calendar and one other the account can read."""

    ITEMS: ClassVar[list[dict[str, Any]]] = [
        {"id": MAIN, "summary": MAIN, "primary": True},
        {"id": "team@group.calendar.google.test", "summary": "Team"},
    ]

    def get(self, calendarId: str) -> _Request:  # noqa: N803 — Google's name
        wanted = MAIN if calendarId == "primary" else calendarId
        return _Request(next(item for item in self.ITEMS if item["id"] == wanted))

    def list(self, **_kwargs: Any) -> _Request:
        return _Request({"items": list(self.ITEMS)})


class _FakeGoogle:
    """Calendar v3, as far as a sync touches it. Records every call it gets."""

    def __init__(self) -> None:
        self.next_items: dict[str, list[dict[str, Any]]] = {}
        #: Events a single read by id finds, by calendar.
        self.stored: dict[str, dict[str, dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._tokens = 0
        #: Answer the next resumed read of the main calendar with 410 Gone.
        self.expire_main_token = False

    def events(self) -> _FakeGoogle:
        return self

    def get(self, *, calendarId: str, eventId: str) -> _Request:  # noqa: N803 — Google's names
        return _Request(self.stored[calendarId][eventId])

    def calendarList(self) -> _FakeCalendarList:  # noqa: N802 — Google's name
        return _FakeCalendarList()

    def list(self, **kwargs: Any) -> _Request:
        if self.expire_main_token and kwargs["calendarId"] == MAIN and kwargs.get("syncToken"):
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

    #: The kept flag an older image reads: "following the main calendar".
    follows_main: bool = False

    def set_followed_calendar(
        self, user_id: str, calendar_id: str | None, *, main_calendar: bool = False
    ) -> None:
        self.doc.follow_calendar_id = calendar_id
        self.follows_main = calendar_id is not None and main_calendar

    def resolve_followed_main_calendar(self, user_id: str, calendar_id: str) -> bool:
        if self.doc.follow_calendar_id != "primary":
            return False
        self.doc.follow_calendar_id = calendar_id
        return True


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
                follow_calendar_id="primary",
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

    def poll(self, main_calendar: list[dict[str, Any]], *, calendar: str = MAIN) -> None:
        self.google.next_items[calendar] = main_calendar
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
            answered_title=answered_title_digest("Weekly 1:1"),
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
    stack.tokens.doc.follow_calendar_id = None

    stack.poll([_google_event("o1", _in(3))])

    assert all(kwargs["calendarId"] != MAIN for _, kwargs in stack.google.calls)
    assert stack.events.list_open(USER_ID) == []


def test_nothing_is_read_without_the_grant_to_read_events(stack: _Stack) -> None:
    stack.tokens.doc.granted_capabilities = "busy,push"

    stack.poll([_google_event("o1", _in(3))])

    assert all(kwargs["calendarId"] != MAIN for _, kwargs in stack.google.calls)


def test_the_main_calendar_resumes_from_its_own_sync_token(stack: _Stack) -> None:
    stack.poll([])
    stack.poll([])

    # The fake numbers its tokens in call order: Pablo's calendar is read
    # first (token-1), then the main calendar (token-2), and so on.
    resumed = [(kwargs["calendarId"], kwargs.get("syncToken")) for _, kwargs in stack.google.calls]
    assert resumed == [
        (PABLO_CALENDAR, None),
        (MAIN, None),
        (PABLO_CALENDAR, "token-1"),
        (MAIN, "token-2"),
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


class TestTheFullReadWindow:
    """A full read covers a bounded window, and only that window is judged."""

    def _followed(self, stack: _Stack, event_id: str) -> Any:
        return stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, event_id)

    def test_a_full_read_asks_for_a_bounded_window(self, stack: _Stack) -> None:
        stack.poll([])
        stack.poll([])

        main_reads = [kwargs for _, kwargs in stack.google.calls if kwargs["calendarId"] == MAIN]
        first, resumed = main_reads
        read_to = datetime.fromisoformat(first["timeMax"])
        assert timedelta(days=399) < read_to - utc_now() <= timedelta(days=400)
        assert "timeMax" not in resumed
        assert "syncToken" in resumed

    def test_a_session_beyond_the_window_is_left_alone(self, stack: _Stack) -> None:
        stack.client("p1", "wk")
        stack.poll([_google_event("near", _in(3)), _google_event("far", _in(450))])
        assert self._followed(stack, "far") is not None

        stack.google.expire_main_token = True
        stack.poll([_google_event("near", _in(3))])

        far = self._followed(stack, "far")
        assert far.status == AppointmentStatus.CONFIRMED
        assert far.google_sync_status not in {
            GoogleSyncStatus.MISSING_IN_GOOGLE,
            GoogleSyncStatus.REMOVED_IN_GOOGLE,
        }

    def test_a_session_inside_the_window_still_goes_through_the_follower(
        self, stack: _Stack
    ) -> None:
        stack.client("p1", "wk")
        stack.poll([_google_event("near", _in(3)), _google_event("mid", _in(30))])

        stack.google.expire_main_token = True
        stack.poll([_google_event("near", _in(3))])

        mid = self._followed(stack, "mid")
        assert mid.status == AppointmentStatus.CANCELLED
        assert mid.google_sync_status == GoogleSyncStatus.REMOVED_IN_GOOGLE


TEAM = "team@group.calendar.google.test"


def _reads_of(stack: _Stack, calendar_id: str) -> int:
    return sum(1 for _, kwargs in stack.google.calls if kwargs["calendarId"] == calendar_id)


class TestAChosenCalendar:
    """Following reads the calendar the clinician chose, and only it."""

    def test_the_calendars_on_offer_are_the_readable_ones_main_first(self, stack: _Stack) -> None:
        calendars = stack.calendar.list_readable_calendars(USER_ID)

        assert [(c.id, c.primary) for c in calendars] == [(MAIN, True), (TEAM, False)]

    def test_nothing_is_on_offer_without_the_grant_to_read_events(self, stack: _Stack) -> None:
        stack.tokens.doc.granted_capabilities = "busy,push"

        assert stack.calendar.list_readable_calendars(USER_ID) == []

    def test_primary_is_resolved_to_the_main_calendar_and_keeps_its_read(
        self, stack: _Stack
    ) -> None:
        stack.tokens.doc.main_calendar_sync_token = "carried-on"

        stack.poll([])

        assert stack.tokens.doc.follow_calendar_id == MAIN
        assert ("list", {"calendarId": MAIN, "syncToken": "carried-on"}) in [
            (name, {k: kwargs[k] for k in ("calendarId", "syncToken") if k in kwargs})
            for name, kwargs in stack.google.calls
        ]

    def test_the_read_targets_the_chosen_calendar(self, stack: _Stack) -> None:
        stack.calendar.set_followed_calendar(USER_ID, TEAM)

        stack.poll([_google_event("t1", _in(3))], calendar=TEAM)

        assert _reads_of(stack, TEAM) == 1
        assert _reads_of(stack, MAIN) == 0
        [row] = stack.events.list_open(USER_ID)
        assert (row.source_event_id, row.calendar_id) == ("t1", TEAM)

    def test_choosing_another_calendar_starts_its_read_over(self, stack: _Stack) -> None:
        stack.poll([])
        assert stack.tokens.doc.main_calendar_sync_token is not None

        assert stack.calendar.set_followed_calendar(USER_ID, TEAM) is True

        assert stack.tokens.doc.main_calendar_sync_token is None
        assert stack.calendar.set_followed_calendar(USER_ID, TEAM) is False

    def test_a_session_booked_from_a_calendar_records_it(self, stack: _Stack) -> None:
        stack.client("p1", "wk")

        stack.poll([_google_event("o1", _in(3))])

        booked = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
        assert booked is not None
        assert booked.outside_calendar_id == MAIN

    def test_switching_calendars_cancels_nothing_from_the_old_one(self, stack: _Stack) -> None:
        stack.client("p1", "wk")
        stack.poll([_google_event(f"o{n}", _in(3 + 7 * n)) for n in range(3)])
        booked = [
            stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, f"o{n}")
            for n in range(3)
        ]
        assert all(a is not None for a in booked)

        stack.calendar.set_followed_calendar(USER_ID, TEAM)
        # The first read of the new calendar is a full one, and holds none
        # of the old calendar's events.
        stack.poll([_google_event("t1", _in(4), series="team-wk")], calendar=TEAM)

        for n in range(3):
            kept = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, f"o{n}")
            assert kept is not None
            assert kept.status == AppointmentStatus.CONFIRMED
            # Neither cancelled quietly nor held as a bulk deletion.
            assert kept.google_sync_status not in {
                GoogleSyncStatus.MISSING_IN_GOOGLE,
                GoogleSyncStatus.REMOVED_IN_GOOGLE,
            }

    def test_rows_and_sessions_from_before_calendars_were_recorded_are_claimed(
        self, stack: _Stack
    ) -> None:
        stack.client("p1", "wk")
        stack.poll([_google_event("o1", _in(3))])
        # As a schema from before this change left them.
        [row] = stack.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
        row.calendar_id = None
        stack.events.save(row)
        booked = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
        assert booked is not None
        booked.outside_calendar_id = None
        stack.appointments.update(booked)
        stack.tokens.doc.follow_calendar_id = "primary"

        stack.poll([])

        [row] = stack.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
        assert row.calendar_id == MAIN
        claimed = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
        assert claimed is not None
        assert claimed.outside_calendar_id == MAIN

    def test_a_moved_session_is_read_from_its_own_calendar(self, stack: _Stack) -> None:
        # One event id on two calendars, at different times.
        on_main, on_team = _in(3), _in(4)
        stack.google.stored = {
            MAIN: {"t1": _google_event("t1", on_main)},
            TEAM: {"t1": _google_event("t1", on_team)},
        }

        times = stack.calendar.read_event_times(USER_ID, "t1", followed_calendar=TEAM)

        assert times is not None
        assert times[0] == on_team


class TestChosenCalendarReviewFindings:
    def test_a_choice_made_while_primary_resolves_is_not_overwritten(self, stack: _Stack) -> None:
        # The clinician picks another calendar between the read loading its
        # settings and resolving "primary".
        original = stack.tokens.get

        def switched_meanwhile(user_id: str) -> GoogleCalendarTokenDoc | None:
            doc = original(user_id)
            stack.tokens.doc = GoogleCalendarTokenDoc(
                **{**stack.tokens.doc.to_dict(), "follow_calendar_id": TEAM}
            )
            return doc

        with patch.object(stack.tokens, "get", side_effect=switched_meanwhile):
            read = stack.calendar.read_main_calendar_changes(USER_ID)

        assert stack.tokens.doc.follow_calendar_id == TEAM
        assert read.changes == []
        assert _reads_of(stack, MAIN) == 0

    def test_a_session_whose_row_is_gone_is_still_claimed(self, stack: _Stack) -> None:
        stack.client("p1", "wk")
        stack.poll([_google_event("o1", _in(3))])
        booked = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
        assert booked is not None
        booked.outside_calendar_id = None
        stack.appointments.update(booked)
        for row in stack.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE):
            stack.events.delete(USER_ID, row.id)

        stack.outside.claim_unrecorded(USER_ID, MAIN)

        claimed = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
        assert claimed is not None
        assert claimed.outside_calendar_id == MAIN

    def test_an_event_read_from_the_new_calendar_takes_its_session_along(
        self, stack: _Stack
    ) -> None:
        stack.client("p1", "wk")
        stack.poll([_google_event("o1", _in(3))])
        stack.calendar.set_followed_calendar(USER_ID, TEAM)

        # The same event, invited onto the team calendar under the same id.
        stack.poll([_google_event("o1", _in(3))], calendar=TEAM)

        booked = stack.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
        assert booked is not None
        assert booked.outside_calendar_id == TEAM
        [row] = stack.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
        assert row.calendar_id == TEAM


@pytest.fixture
def api(client: TestClient, stack: _Stack) -> Iterator[_Stack]:
    """The API, over the real calendar service and the fake Google."""
    app.dependency_overrides[get_google_calendar_service] = lambda: stack.calendar
    app.dependency_overrides[get_outside_sessions] = lambda: stack.outside
    return stack


def _follow(client: TestClient, calendar_id: str | None) -> Any:
    return client.put("/api/google-calendar/followed-calendar", json={"calendar_id": calendar_id})


def _followed(client: TestClient) -> Any:
    return client.get("/api/google-calendar/status").json()["follow_calendar_id"]


class TestChoosingACalendarOverTheApi:
    def test_the_calendars_on_offer_are_readable_ones_with_the_followed_one_by_its_id(
        self, client: TestClient, api: _Stack
    ) -> None:
        body = client.get("/api/google-calendar/calendars").json()

        assert [(c["id"], c["primary"]) for c in body["calendars"]] == [(MAIN, True), (TEAM, False)]
        assert body["follow_calendar_id"] == MAIN

    def test_nothing_is_offered_or_followed_without_the_grant_to_read_events(
        self, client: TestClient, api: _Stack
    ) -> None:
        api.tokens.doc.granted_capabilities = "busy,push"
        api.tokens.doc.follow_calendar_id = None

        assert client.get("/api/google-calendar/calendars").status_code == 400
        assert _follow(client, "primary").status_code == 400
        assert api.tokens.doc.follow_calendar_id is None

    def test_following_is_turned_on_for_the_main_calendar_and_off(
        self, client: TestClient, api: _Stack
    ) -> None:
        api.tokens.doc.follow_calendar_id = None

        assert _follow(client, "primary").json() == {"follow_calendar_id": MAIN}
        assert _followed(client) == MAIN
        assert api.tokens.follows_main is True

        assert _follow(client, None).json() == {"follow_calendar_id": None}
        assert _followed(client) is None
        assert api.tokens.follows_main is False

    def test_a_calendar_the_connection_cant_read_is_refused_and_nothing_changes(
        self, client: TestClient, api: _Stack
    ) -> None:
        api.tokens.doc.follow_calendar_id = MAIN

        response = _follow(client, "someone-else@group.calendar.google.test")

        assert response.status_code == 400
        assert _followed(client) == MAIN

    def test_another_calendar_starts_its_read_over_and_an_older_image_stops_following(
        self, client: TestClient, api: _Stack
    ) -> None:
        api.client("p1", "wk")
        api.poll([_google_event("o1", _in(3)), _google_event("q1", _in(5), series="other")])
        assert api.tokens.doc.main_calendar_sync_token is not None
        assert _open_ids(api) == ["q1"]

        assert _follow(client, TEAM).json() == {"follow_calendar_id": TEAM}

        assert _followed(client) == TEAM
        assert api.tokens.doc.main_calendar_sync_token is None
        assert api.tokens.follows_main is False
        # The old calendar's question goes; its booked session stays.
        assert _open_ids(api) == []
        kept = api.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "o1")
        assert kept is not None
        assert kept.status == AppointmentStatus.CONFIRMED

    def test_the_main_calendar_by_its_id_carries_the_read_on(
        self, client: TestClient, api: _Stack
    ) -> None:
        api.tokens.doc.main_calendar_sync_token = "carried-on"
        api.google.next_items[MAIN] = [_google_event("q1", _in(5))]
        api.scheduler.execute(USER_ID)
        api.tokens.doc.follow_calendar_id = "primary"
        api.tokens.doc.main_calendar_sync_token = "carried-on"

        assert _follow(client, MAIN).json() == {"follow_calendar_id": MAIN}

        assert _followed(client) == MAIN
        assert api.tokens.doc.main_calendar_sync_token == "carried-on"
        assert _open_ids(api) == ["q1"]
