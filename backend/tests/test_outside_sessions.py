# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Sessions another service puts on the main calendar are asked about once, then followed."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from app.calendar_providers.practice_import import Cadence, ProposedSeries, SeriesStatus
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    calendar_source_identifier,
    event_source_identifier,
    ical_source,
)
from app.models.patient import Patient
from app.patients.identifiers import calendar_scope
from app.patients.matching import remember_match, remember_not_a_client
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.external_calendar_event import (
    ANSWER_CLIENT,
    ExternalCalendarEvent,
    InMemoryExternalCalendarEventRepository,
)
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.patient_source_mapping import InMemoryPatientSourceMappingRepository
from app.routes.calendar_import import _source_identifier
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.audit_service import AuditService
from app.services.google_calendar_follow import (
    GoogleChangeFollower,
    GoogleSyncStatus,
    Resolution,
)
from app.services.outside_sessions import OutsideSessions
from app.settings import get_settings
from app.utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Generator

    from app.models import User

USER_ID = "test-user-123"
#: The followed calendar's real id; its answers are remembered under it.
MAIN = "clinician@example.test"
SCOPE = calendar_scope(MAIN)


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """The secret the answered-title digest is keyed under; every answer needs it."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


SERIES = "series-weekly-1"


def _in(days: float, hour: int = 14) -> datetime:
    base = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return base.replace(hour=hour)


def _event(
    event_id: str,
    start: datetime | None = None,
    *,
    title: str = "Weekly 1:1",
    series: str | None = SERIES,
    cancelled: bool = False,
) -> dict[str, Any]:
    """A main-calendar change as ``read_main_calendar_changes`` hands it over."""
    if cancelled:
        return {"google_event_id": event_id, "status": "cancelled", "start": {}, "end": {}}
    assert start is not None
    return {
        "google_event_id": event_id,
        "status": "confirmed",
        "summary": title,
        "series_id": series,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
    }


class _Harness:
    def __init__(self) -> None:
        self.events = InMemoryExternalCalendarEventRepository()
        self.appointments = InMemoryAppointmentRepository()
        self.patients = InMemoryPatientRepository()
        self.mappings = InMemoryPatientSourceMappingRepository()
        self.outside = OutsideSessions(
            self.events, self.appointments, self.patients, self.mappings, main_calendar_id=MAIN
        )
        self.calendar = MagicMock()
        self.follower = GoogleChangeFollower(self.appointments, self.calendar)
        self.audit = AuditService(InMemoryAuditRepository())

    def patient(self, patient_id: str, first: str, last: str) -> Patient:
        now = utc_now()
        patient = self.patients.create(
            Patient(
                id=patient_id, first_name=first, last_name=last, created_at=now, updated_at=now
            ),
            USER_ID,
        )
        self.appointments.grant_access(patient_id, USER_ID)
        return patient

    def poll(self, user: User, changes: list[dict[str, Any]]) -> None:
        """What one scheduled read does: bring events in, then follow them."""
        self.outside.ingest_google(USER_ID, changes, calendar_id=MAIN)
        self.follower.follow(user, self.audit, changes, outside_source=GOOGLE_CALENDAR_SOURCE)

    def open_ids(self) -> list[str]:
        return [row.source_event_id for row in self.events.list_open(USER_ID)]

    def followed(self, event_id: str) -> Appointment | None:
        return self.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, event_id)

    def answer(self, patient_id: str | None, series: str = SERIES) -> None:
        self.outside.answer(
            USER_ID,
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(series, "", 0, "00:00"),
            patient_id=patient_id,
        )


@pytest.fixture
def h() -> _Harness:
    return _Harness()


class TestWhatBecomesAQuestion:
    def test_a_recurring_series_is_held_open_and_counted(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.poll(mock_user, [_event("e1", _in(2)), _event("e2", _in(9))])

        assert h.open_ids() == ["e1", "e2"]
        [question] = h.outside.questions(USER_ID)
        assert len(question.rows) == 2
        assert question.recurring is True

    def test_a_one_off_naming_nobody_stays_a_busy_block(self, h: _Harness, mock_user: User) -> None:
        h.patient("p1", "Jane", "Smith")

        h.poll(mock_user, [_event("dentist", _in(2), title="Dentist", series=None)])

        assert h.open_ids() == []
        assert h.outside.questions(USER_ID) == []

    def test_a_one_off_naming_a_client_is_asked_with_the_match_offered(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")

        h.poll(mock_user, [_event("x", _in(2), title="Jane Smith", series=None)])

        [question] = h.outside.questions(USER_ID)
        # Still a question — a name alone never books anyone — but the
        # clinician only has to confirm it.
        assert question.match.patient_id == "p1"
        assert h.followed("x") is None

    def test_same_titled_events_without_a_series_id_are_one_client_per_slot(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")
        monday = _in(7 - utc_now().weekday())  # a Monday ahead, 14:00 UTC

        h.poll(
            mock_user,
            [
                _event("mon-1", monday, title="Jane Smith", series=None),
                _event("mon-2", monday + timedelta(days=7), title="Jane Smith", series=None),
                _event("thu-1", monday + timedelta(days=3), title="Jane Smith", series=None),
            ],
        )

        # The same weekday and time is one series, stable week to week; the
        # Thursday slot is a separate question.
        questions = h.outside.questions(USER_ID)
        assert sorted(len(q.rows) for q in questions) == [1, 2]

    def test_a_remembered_client_whose_chart_is_gone_is_asked_again(
        self, h: _Harness, mock_user: User
    ) -> None:
        start = _in(2)
        remember_match(
            GOOGLE_CALENDAR_SOURCE,
            event_source_identifier(None, "Dentist", start, UTC),
            "deleted-patient",
            h.outside.context(USER_ID),
            scope=SCOPE,
        )

        h.poll(mock_user, [_event("x", start, title="Dentist", series=None)])

        assert h.open_ids() == ["x"]
        assert h.followed("x") is None


class TestSharedIdentifier:
    def test_a_slot_the_import_remembered_is_offered_in_the_clinicians_zone(
        self, h: _Harness, mock_user: User
    ) -> None:
        zone = ZoneInfo("America/New_York")
        h.outside = h.outside.in_zone(zone)
        h.patient("p1", "Jane", "Smith")
        start = _in(3, hour=19)  # an evening slot: its local weekday can differ from UTC's
        local = start.astimezone(zone)
        # What the import stores for a hand-entered weekly series.
        remember_match(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(
                None, "Weekly 1:1", local.weekday(), local.strftime("%H:%M")
            ),
            "p1",
            h.outside.context(USER_ID),
            scope=SCOPE,
        )

        h.poll(mock_user, [_event("e1", start, series=None)])

        # Found under the same key the import stored — read in the
        # clinician's zone — and offered for one confirm.
        [question] = h.outside.questions(USER_ID)
        assert question.suggested_patient_id == "p1"
        assert h.followed("e1") is None


class TestRememberedSlots:
    def _remember_monday_ten(self, h: _Harness) -> datetime:
        monday = _in(7 - utc_now().weekday(), hour=10)
        remember_match(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(None, "Session", monday.weekday(), monday.strftime("%H:%M")),
            "p1",
            h.outside.context(USER_ID),
            scope=SCOPE,
        )
        return monday

    def test_a_reused_slot_is_asked_about_not_booked_to_the_old_client(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")
        h.patient("p2", "Bob", "Jones")
        monday = self._remember_monday_ten(h)

        # A year on, someone else has Monday 10:00, typed the same way.
        h.poll(
            mock_user,
            [_event("bob-1", monday + timedelta(days=364), title="Session", series=None)],
        )

        assert h.followed("bob-1") is None
        [question] = h.outside.questions(USER_ID)
        assert question.suggested_patient_id == "p1"
        assert question.match.patient_id is None

    def test_a_remembered_provider_series_still_books_without_asking(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")
        h.poll(mock_user, [_event("e1", _in(2))])
        h.answer("p1")

        h.poll(mock_user, [_event("e9", _in(30))])

        booked = h.followed("e9")
        assert booked is not None
        assert booked.patient_id == "p1"

    def test_the_import_and_following_share_one_identifier(self) -> None:
        series = ProposedSeries(
            candidate_key="k",
            summary="Weekly 1:1",
            weekday=0,
            local_start_time="09:00",
            duration_minutes=50,
            cadence=Cadence.WEEKLY,
            occurrences_in_window=4,
            occurrences_ahead=4,
            first_future_start=None,
            last_seen=utc_now(),
            recurrence_rule="RRULE:FREQ=WEEKLY",
            status=SeriesStatus.ACTIVE,
            confidence=1.0,
            preselected=True,
        )

        assert _source_identifier(series) == calendar_source_identifier(
            None, "weekly  1:1", 0, "09:00"
        )
        assert _source_identifier(series).startswith("shape:")


class TestAnswering:
    def test_answering_books_the_series_and_the_next_event_follows_unasked(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")
        h.poll(mock_user, [_event("e1", _in(2)), _event("e2", _in(9))])

        h.answer("p1")

        booked = [h.followed("e1"), h.followed("e2")]
        assert all(a is not None and a.patient_id == "p1" for a in booked)
        assert h.open_ids() == []
        h.poll(mock_user, [_event("e3", _in(16))])
        third = h.followed("e3")
        assert third is not None
        assert third.patient_id == "p1"
        assert third.outside_source == GOOGLE_CALENDAR_SOURCE
        # What the calendar shows for it, and nothing a feed would carry.
        assert (third.title, third.duration_minutes, third.session_type) == (
            "Session",
            50,
            "individual",
        )
        assert (third.ical_uid, third.ical_source, third.ical_sync_status) == (None, None, None)
        assert h.outside.questions(USER_ID) == []

    def test_not_a_client_is_remembered_and_the_series_never_asks_again(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.poll(mock_user, [_event("e1", _in(2))])

        h.answer(None)
        h.poll(mock_user, [_event("e1", _in(2)), _event("e2", _in(9))])

        assert h.open_ids() == []
        assert h.followed("e2") is None

    def test_answering_never_books_over_a_session_already_booked(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")
        start = _in(2)
        h.appointments.create(
            Appointment(
                id="booked-here",
                user_id=USER_ID,
                patient_id="p1",
                title="Session",
                start_at=start,
                end_at=start + timedelta(minutes=50),
                duration_minutes=50,
                status=AppointmentStatus.CONFIRMED,
                session_type="individual",
            )
        )
        h.poll(mock_user, [_event("e1", start)])

        h.answer("p1")

        assert h.followed("e1") is None
        [row] = h.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
        assert row.answer == ANSWER_CLIENT

    def test_an_appointment_is_never_written_without_a_patient(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.poll(mock_user, [_event("e1", _in(2)), _event("e2", _in(9))])

        assert h.appointments.list_by_range(USER_ID, _in(0), _in(30)) == []

    def test_a_remembered_series_over_a_booking_books_nothing_and_reports_nothing(
        self, h: _Harness, mock_user: User
    ) -> None:
        """The event is answered, so it is never asked about, but it is not booked twice."""
        h.patient("p1", "Jane", "Smith")
        start = _in(2)
        h.appointments.create(
            Appointment(
                id="booked-here",
                user_id=USER_ID,
                patient_id="p1",
                title="Session",
                start_at=start,
                end_at=start + timedelta(minutes=50),
                duration_minutes=50,
                status=AppointmentStatus.CONFIRMED,
                session_type="individual",
            )
        )
        h.poll(mock_user, [_event("e1", _in(9))])
        h.answer("p1")

        result = h.outside.ingest_google(USER_ID, [_event("e2", start)])

        assert result.booked == []
        [row] = [
            r
            for r in h.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
            if r.source_event_id == "e2"
        ]
        assert (row.answer, row.patient_id, row.appointment_id) == (ANSWER_CLIENT, "p1", None)
        assert [a.id for a in h.appointments.list_by_range(USER_ID, _in(0), _in(30))] == [
            "booked-here",
            h.followed("e1").id,  # type: ignore[union-attr]
        ]


class TestFollowing:
    @pytest.fixture
    def answered(self, h: _Harness, mock_user: User) -> _Harness:
        h.patient("p1", "Jane", "Smith")
        h.poll(mock_user, [_event(f"e{i}", _in(2 + 7 * i)) for i in range(6)])
        h.answer("p1")
        return h

    def test_a_move_in_google_moves_the_appointment(
        self, answered: _Harness, mock_user: User
    ) -> None:
        new_start = _in(3, hour=10)

        answered.poll(mock_user, [_event("e0", new_start)])

        moved = answered.followed("e0")
        assert moved is not None
        assert moved.start_at == new_start
        assert moved.status == AppointmentStatus.CONFIRMED

    def test_a_deletion_cancels_it_quietly(self, answered: _Harness, mock_user: User) -> None:
        answered.poll(mock_user, [_event("e0", cancelled=True)])

        gone = answered.followed("e0")
        assert gone is not None
        assert gone.status == AppointmentStatus.CANCELLED
        assert gone.google_sync_status == GoogleSyncStatus.REMOVED_IN_GOOGLE
        assert gone.late_cancellation is False

    def test_a_bulk_deletion_is_held_not_cancelled(
        self, answered: _Harness, mock_user: User
    ) -> None:
        answered.poll(mock_user, [_event(f"e{i}", cancelled=True) for i in range(5)])

        for i in range(5):
            held = answered.followed(f"e{i}")
            assert held is not None
            assert held.status == AppointmentStatus.CONFIRMED
            assert held.google_sync_status == GoogleSyncStatus.MISSING_IN_GOOGLE

    def test_a_session_with_a_note_does_not_move(self, answered: _Harness, mock_user: User) -> None:
        started = answered.followed("e0")
        assert started is not None
        started.session_id = "session-1"
        answered.appointments.update(started)

        answered.poll(mock_user, [_event("e0", _in(4))])

        still = answered.followed("e0")
        assert still is not None
        assert still.start_at == started.start_at

    def test_keeping_pablos_side_writes_nothing_to_google(
        self, answered: _Harness, mock_user: User
    ) -> None:
        answered.poll(mock_user, [_event("e0", cancelled=True)])
        gone = answered.followed("e0")
        assert gone is not None

        restored = answered.follower.resolve(USER_ID, gone.id, Resolution.KEEP_PABLO)

        assert restored.status == AppointmentStatus.CONFIRMED
        assert restored.google_sync_status is None
        answered.calendar.push_appointment_event.assert_not_called()

    def test_putting_back_a_held_bulk_deletion_writes_nothing_to_google(
        self, answered: _Harness, mock_user: User
    ) -> None:
        answered.poll(mock_user, [_event(f"e{i}", cancelled=True) for i in range(5)])

        kept = answered.follower.resolve_held(USER_ID, Resolution.KEEP_PABLO)

        assert len(kept) == 5
        assert {a.google_sync_status for a in kept} == {None}
        answered.calendar.push_appointment_event.assert_not_called()

    def test_open_rows_move_and_go_with_their_event(self, h: _Harness, mock_user: User) -> None:
        h.poll(mock_user, [_event("e1", _in(2)), _event("e2", _in(9))])
        new_start = _in(3)

        h.poll(mock_user, [_event("e1", new_start), _event("e2", cancelled=True)])

        [row] = h.events.list_open(USER_ID)
        assert (row.source_event_id, row.start_at) == ("e1", new_start)


class TestReadingAnAnsweredEventAgain:
    """A read brings an answered event in again: moved, retitled, or as it was.

    The row's answer and its appointment are the row's own; the read only says
    where and what the event is now. Nothing is booked a second time.
    """

    @pytest.fixture
    def answered(self, h: _Harness, mock_user: User) -> _Harness:
        h.patient("p1", "Jane", "Smith")
        h.poll(mock_user, [_event("e1", _in(2)), _event("e2", _in(9))])
        h.answer("p1")
        return h

    def test_a_moved_answered_event_keeps_its_one_appointment(self, answered: _Harness) -> None:
        booked = answered.followed("e1")
        assert booked is not None

        # The read alone, before the follower moves the appointment.
        answered.outside.ingest_google(USER_ID, [_event("e1", _in(3, hour=10))])

        [row] = [
            r
            for r in answered.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
            if r.source_event_id == "e1"
        ]
        assert (row.answer, row.patient_id, row.appointment_id) == (
            ANSWER_CLIENT,
            "p1",
            booked.id,
        )
        assert [
            a.outside_event_id
            for a in answered.appointments.list_by_range(USER_ID, _in(0), _in(30))
        ] == ["e1", "e2"]

    def test_a_retitled_answered_event_stays_answered(
        self, answered: _Harness, mock_user: User
    ) -> None:
        """Retitled, the series would be asked again; an event already answered is not."""
        booked = answered.followed("e1")
        assert booked is not None

        answered.poll(mock_user, [_event("e1", _in(2), title="Someone else")])

        [row] = [
            r
            for r in answered.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
            if r.source_event_id == "e1"
        ]
        assert (row.answer, row.patient_id, row.appointment_id) == (
            ANSWER_CLIENT,
            "p1",
            booked.id,
        )
        assert answered.outside.questions(USER_ID) == []

    def test_an_identifier_since_said_to_be_no_client_takes_its_held_row(
        self, h: _Harness, mock_user: User
    ) -> None:
        """Answered elsewhere (the import), the row already held for it goes."""
        h.poll(mock_user, [_event("e1", _in(2))])
        assert h.open_ids() == ["e1"]
        remember_not_a_client(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(SERIES, "", 0, "00:00"),
            h.outside.context(USER_ID),
            scope=SCOPE,
        )

        h.poll(mock_user, [_event("e1", _in(2))])

        assert h.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE) == []
        assert h.outside.questions(USER_ID) == []

    def test_a_one_off_whose_only_candidate_is_gone_stops_being_asked(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")
        h.poll(mock_user, [_event("x", _in(2), title="Jane Smith", series=None)])
        assert h.open_ids() == ["x"]
        h.patients.delete("p1", USER_ID)

        h.poll(mock_user, [_event("x", _in(2), title="Jane Smith", series=None)])

        assert h.open_ids() == []


class TestAFullReadWindow:
    def test_rows_touching_the_edge_of_the_window_are_left_alone(self, h: _Harness) -> None:
        """Ending as the window opens, or starting as it closes, is outside it."""
        start, end = _in(1), _in(30)
        for row_id, starts, ends in [
            ("ends-at-open", start - timedelta(minutes=50), start),
            ("starts-at-close", end, end + timedelta(minutes=50)),
            ("inside", start, start + timedelta(minutes=50)),
        ]:
            h.events.save(
                ExternalCalendarEvent(
                    id=row_id,
                    user_id=USER_ID,
                    source=GOOGLE_CALENDAR_SOURCE,
                    source_event_id=row_id,
                    calendar_id="main",
                    start_at=starts,
                    end_at=ends,
                )
            )

        h.outside.reconcile_full_read(
            USER_ID, present=set(), window=(start, end), calendar_id="main"
        )

        assert sorted(r.id for r in h.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)) == [
            "ends-at-open",
            "starts-at-close",
        ]


class TestClaimingTheMainCalendar:
    def test_only_what_recorded_no_calendar_is_claimed(self, h: _Harness, mock_user: User) -> None:
        """A row or session from another calendar, or from a feed, keeps what it recorded."""
        h.patient("p1", "Jane", "Smith")
        start = _in(3)
        # A row from before calendars were recorded. A read records one now,
        # so it is written as an older image left it.
        h.events.save(
            ExternalCalendarEvent(
                id="unrecorded-row",
                user_id=USER_ID,
                source=GOOGLE_CALENDAR_SOURCE,
                source_event_id="e1",
                calendar_id=None,
                start_at=_in(2),
                end_at=_in(2) + timedelta(minutes=50),
            )
        )
        h.events.save(
            ExternalCalendarEvent(
                id="team-row",
                user_id=USER_ID,
                source=GOOGLE_CALENDAR_SOURCE,
                source_event_id="t1",
                calendar_id="team",
                start_at=start,
                end_at=start + timedelta(minutes=50),
            )
        )
        for appointment_id, source, calendar_id in [
            ("team-session", GOOGLE_CALENDAR_SOURCE, "team"),
            ("unrecorded-session", GOOGLE_CALENDAR_SOURCE, None),
            ("feed-session", ical_source("simplepractice"), None),
        ]:
            h.appointments.create(
                Appointment(
                    id=appointment_id,
                    user_id=USER_ID,
                    patient_id="p1",
                    title="Session",
                    start_at=start,
                    end_at=start + timedelta(minutes=50),
                    duration_minutes=50,
                    status=AppointmentStatus.CONFIRMED,
                    session_type="individual",
                    outside_source=source,
                    outside_event_id=appointment_id,
                    outside_calendar_id=calendar_id,
                )
            )

        claimed = h.outside.claim_unrecorded(USER_ID, "main")

        assert claimed == 1
        assert {
            r.source_event_id: r.calendar_id
            for r in h.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
        } == {"e1": "main", "t1": "team"}
        assert {
            a.id: a.outside_calendar_id
            for a in h.appointments.list_by_range(USER_ID, _in(0), _in(30))
        } == {"team-session": "team", "unrecorded-session": "main", "feed-session": None}

    def test_a_session_a_colleague_already_books_on_the_main_calendar_is_left_alone(
        self, h: _Harness
    ) -> None:
        """Claiming it would be a second live booking: it stays as it was, and the rest go on."""
        h.patient("p1", "Jane", "Smith")
        start = _in(3)

        def session(appointment_id: str, user_id: str, event_id: str, calendar: str | None) -> None:
            h.appointments.create(
                Appointment(
                    id=appointment_id,
                    user_id=user_id,
                    patient_id="p1",
                    title="Session",
                    start_at=start,
                    end_at=start + timedelta(minutes=50),
                    duration_minutes=50,
                    status=AppointmentStatus.CONFIRMED,
                    session_type="individual",
                    outside_source=GOOGLE_CALENDAR_SOURCE,
                    outside_event_id=event_id,
                    outside_calendar_id=calendar,
                )
            )

        session("colleagues", "colleague-1", "e1", "main")
        session("mine-duplicate", USER_ID, "e1", None)
        session("mine-other", USER_ID, "e2", None)

        h.outside.claim_unrecorded(USER_ID, "main")

        assert {
            a.id: a.outside_calendar_id
            for a in h.appointments.list_by_range(USER_ID, _in(0), _in(30))
        } == {"mine-duplicate": None, "mine-other": "main"}
