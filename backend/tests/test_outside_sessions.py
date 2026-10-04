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
from app.calendar_providers.disconnect import forget_google_calendar
from app.calendar_providers.practice_import import Cadence, ProposedSeries, SeriesStatus
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    answered_title_digest,
    calendar_source_identifier,
    event_source_identifier,
    ical_source,
)
from app.models.patient import Patient
from app.models.user import BOOK_SESSIONS_NAMED_IN_TITLE_BY_DEFAULT, UserPreferences
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
from app.services.outside_sessions import OutsideSessions, Question
from app.settings import get_settings
from app.utcnow import utc_now

from ._name_booking import choosing

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
        # The clinician lets a title naming one client book; tests of the
        # other choice turn it off.
        self.users = choosing(books=True, user_ids=(USER_ID,))
        self.outside = OutsideSessions(
            self.events,
            self.appointments,
            self.patients,
            self.mappings,
            main_calendar_id=MAIN,
            users=self.users,
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

    def test_a_one_off_naming_an_inactive_client_is_asked_with_the_match_offered(
        self, h: _Harness, mock_user: User
    ) -> None:
        patient = h.patient("p1", "Jane", "Smith")
        patient.status = "inactive"
        h.patients.update(patient)

        h.poll(mock_user, [_event("x", _in(2), title="Jane Smith", series=None)])

        [question] = h.outside.questions(USER_ID)
        # Still a question — an inactive chart never books — but the
        # clinician only has to confirm it.
        assert question.match.patient_id == "p1"
        assert h.followed("x") is None

    def test_same_titled_events_without_a_series_id_are_one_client_per_slot(
        self, h: _Harness, mock_user: User
    ) -> None:
        # Two charts share the name, so the title alone books neither.
        h.patient("p1", "Jane", "Smith")
        h.patient("p2", "Jane", "Smith")
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


class TestWhatATitleSuggests:
    """A calendar title names its client in many ways; each is read before matching.

    A full name exactly one chart bears books (``TestATitleThatNamesOneClient``);
    anything less is a suggestion.
    """

    @pytest.fixture
    def clients(self, h: _Harness) -> _Harness:
        h.patient("p1", "Jane", "Smith")
        h.patient("p2", "Robert", "Jones")
        return h

    @staticmethod
    def _asked(h: _Harness, mock_user: User, title: str) -> Question:
        h.poll(mock_user, [_event("e1", _in(2), title=title)])
        [question] = h.outside.questions(USER_ID)
        return question

    @pytest.mark.parametrize(
        "title",
        [
            "Jane Smith",
            "Smith, Jane",
            "Session with Jane Smith",
            "Jane Smith - Therapy",
            "Jane Smith \N{EN DASH} Therapy",
            "Therapy: Jane Smith",
        ],
    )
    def test_a_title_that_carries_one_clients_full_name_books_them(
        self, clients: _Harness, mock_user: User, title: str
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title=title)])

        booked = clients.followed("e1")
        assert booked is not None
        assert booked.patient_id == "p1"
        assert clients.outside.questions(USER_ID) == []

    @pytest.mark.parametrize("title", ["J.S.", "JS", "J. S."])
    def test_initials_that_fit_one_client_suggest_them(
        self, clients: _Harness, mock_user: User, title: str
    ) -> None:
        question = self._asked(clients, mock_user, title)

        assert question.match.patient_id == "p1"
        assert clients.followed("e1") is None

    @pytest.mark.parametrize("title", ["J. Smith", "Jane S."])
    def test_a_name_cut_short_offers_the_client_without_choosing(
        self, clients: _Harness, mock_user: User, title: str
    ) -> None:
        question = self._asked(clients, mock_user, title)

        assert question.match.patient_id is None
        assert question.match.possible_ids == ["p1"]

    def test_a_title_naming_no_one_suggests_no_one(
        self, clients: _Harness, mock_user: User
    ) -> None:
        question = self._asked(clients, mock_user, "Therapy Session")

        assert (question.match.patient_id, question.match.possible_ids) == (None, [])

    def test_initials_two_clients_share_are_offered_both(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.patient("p3", "John", "Stone")

        question = self._asked(clients, mock_user, "J.S.")

        assert question.match.patient_id is None
        assert sorted(question.match.possible_ids) == ["p1", "p3"]

    def test_a_title_naming_two_clients_chooses_neither(
        self, clients: _Harness, mock_user: User
    ) -> None:
        question = self._asked(clients, mock_user, "Jane Smith / Robert Jones")

        assert question.match.patient_id is None
        assert sorted(question.match.possible_ids) == ["p1", "p2"]

    def test_a_title_that_names_a_client_still_books_nothing(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(
            mock_user,
            [_event("e1", _in(2), title="J.S."), _event("e2", _in(9), title="J.S.")],
        )

        assert clients.followed("e1") is None
        assert clients.followed("e2") is None
        assert clients.appointments.list_by_range(USER_ID, _in(0), _in(30)) == []
        assert sorted(clients.open_ids()) == ["e1", "e2"]

    def test_a_remembered_answer_still_decides_whatever_the_title_says(
        self, clients: _Harness, mock_user: User
    ) -> None:
        """A series answered under its title follows the answer, whoever the title names."""
        remember_match(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(SERIES, "", 0, "00:00"),
            "p1",
            clients.outside.context(USER_ID),
            scope=SCOPE,
            answered_title=answered_title_digest("Robert Jones"),
        )

        clients.poll(mock_user, [_event("e2", _in(9), title="Robert Jones")])

        booked = clients.followed("e2")
        assert booked is not None
        assert booked.patient_id == "p1"


class TestATitleThatNamesOneClient:
    """A full name exactly one active chart bears books, as a feed's does.

    The same rule as a feed's (``same_name_charts``, middle names aside).
    Anything less certain stays a question, and every booking guard holds.
    """

    @pytest.fixture
    def clients(self, h: _Harness) -> _Harness:
        h.patient("p1", "Jane", "Smith")
        h.patient("p2", "Robert", "Jones")
        return h

    @staticmethod
    def _series_answer(h: _Harness) -> Any:
        return h.outside.context(USER_ID).lookup(
            GOOGLE_CALENDAR_SOURCE, SCOPE, calendar_source_identifier(SERIES, "", 0, "00:00")
        )

    def test_a_one_off_books_and_nothing_is_remembered_for_its_slot(
        self, clients: _Harness, mock_user: User
    ) -> None:
        start = _in(2)

        clients.poll(mock_user, [_event("x", start, title="Jane Smith", series=None)])

        booked = clients.followed("x")
        assert booked is not None
        assert booked.patient_id == "p1"
        assert clients.outside.questions(USER_ID) == []
        slot = event_source_identifier(None, "Jane Smith", start, UTC)
        assert clients.outside.context(USER_ID).lookup(GOOGLE_CALENDAR_SOURCE, SCOPE, slot) is None

    def test_a_series_books_and_its_later_events_follow(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])
        clients.poll(mock_user, [_event("e2", _in(9), title="Jane Smith")])

        for event_id in ("e1", "e2"):
            booked = clients.followed(event_id)
            assert booked is not None
            assert booked.patient_id == "p1"
        remembered = self._series_answer(clients)
        assert remembered is not None
        assert remembered.patient_id == "p1"
        assert remembered.answered_title == answered_title_digest("Jane Smith")

    def test_middle_names_aside_as_for_a_feed(self, h: _Harness, mock_user: User) -> None:
        h.patient("p1", "Jane Q", "Smith")

        h.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])

        booked = h.followed("e1")
        assert booked is not None
        assert booked.patient_id == "p1"

    @pytest.mark.parametrize("series", [SERIES, None])
    def test_a_name_two_charts_share_is_asked(
        self, clients: _Harness, mock_user: User, series: str | None
    ) -> None:
        clients.patient("p3", "Jane", "Smith")

        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith", series=series)])

        assert clients.followed("e1") is None
        [question] = clients.outside.questions(USER_ID)
        assert sorted(question.match.possible_ids) == ["p1", "p3"]

    def test_initials_are_asked(self, clients: _Harness, mock_user: User) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="J.S.")])

        assert clients.followed("e1") is None
        assert clients.open_ids() == ["e1"]

    def test_a_one_off_under_initials_books_nothing(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("x", _in(2), title="J.S.", series=None)])

        assert clients.followed("x") is None

    def test_a_title_that_could_be_two_clients_is_asked(
        self, clients: _Harness, mock_user: User
    ) -> None:
        """One reading names Jane Smith; another is Robert Jones's initials."""
        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith - RJ")])

        assert clients.followed("e1") is None
        [question] = clients.outside.questions(USER_ID)
        assert sorted(question.match.possible_ids) == ["p1", "p2"]

    def test_a_one_off_naming_no_one_stays_busy_time(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("x", _in(2), title="Alex Rivera", series=None)])

        assert clients.followed("x") is None
        assert clients.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE) == []

    def test_an_inactive_chart_is_asked(self, h: _Harness, mock_user: User) -> None:
        patient = h.patient("p1", "Jane", "Smith")
        patient.status = "inactive"
        h.patients.update(patient)

        h.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])

        assert h.followed("e1") is None
        [question] = h.outside.questions(USER_ID)
        assert question.match.patient_id == "p1"
        assert question.client_inactive is True

    def test_a_chart_only_a_colleague_sees_books_nothing(
        self, h: _Harness, mock_user: User
    ) -> None:
        now = utc_now()
        h.patients.create(
            Patient(
                id="theirs", first_name="Jane", last_name="Smith", created_at=now, updated_at=now
            ),
            "colleague",
        )

        h.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])

        assert h.followed("e1") is None

    def test_a_series_retitled_to_someone_else_asks_again(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])

        clients.poll(mock_user, [_event("e2", _in(9), title="Robert Jones")])

        assert clients.followed("e2") is None
        [question] = clients.outside.questions(USER_ID)
        assert [row.source_event_id for row in question.rows] == ["e2"]
        assert question.suggested_patient_id == "p1"

    def test_a_series_retitled_to_the_same_client_keeps_booking(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])

        clients.poll(mock_user, [_event("e2", _in(9), title="Session with Jane Smith")])

        booked = clients.followed("e2")
        assert booked is not None
        assert booked.patient_id == "p1"

    def test_a_series_said_to_be_no_client_is_never_booked_by_name(
        self, clients: _Harness, mock_user: User
    ) -> None:
        remember_not_a_client(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(SERIES, "", 0, "00:00"),
            clients.outside.context(USER_ID),
            scope=SCOPE,
        )

        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])

        assert clients.followed("e1") is None
        assert clients.open_ids() == []

    def test_an_event_that_has_started_is_not_booked(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(-1), title="Jane Smith")])

        assert clients.followed("e1") is None
        assert self._series_answer(clients) is None

    def test_it_never_books_over_a_session_already_booked(
        self, clients: _Harness, mock_user: User
    ) -> None:
        start = _in(2)
        clients.appointments.create(
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

        result = clients.outside.ingest_google(
            USER_ID, [_event("e1", start, title="Jane Smith")], calendar_id=MAIN
        )

        assert result.booked == []
        assert clients.followed("e1") is None
        [row] = clients.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
        assert row.answer == ANSWER_CLIENT

    def test_a_booking_from_its_title_is_reported_as_one(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="Weekly 1:1")])
        clients.answer("p1")

        result = clients.outside.ingest_google(
            USER_ID,
            [
                _event("e2", _in(9), title="Weekly 1:1"),
                _event("x", _in(3), title="Robert Jones", series=None),
            ],
            calendar_id=MAIN,
        )

        by_patient = {a.patient_id: a.id for a in result.booked}
        assert set(by_patient) == {"p1", "p2"}
        assert result.by_name == {by_patient["p2"]}

    def test_a_session_with_a_note_does_not_move(self, clients: _Harness, mock_user: User) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])
        started = clients.followed("e1")
        assert started is not None
        started.session_id = "session-1"
        clients.appointments.update(started)

        clients.poll(mock_user, [_event("e1", _in(4), title="Jane Smith")])

        still = clients.followed("e1")
        assert still is not None
        assert still.start_at == started.start_at

    def test_cancelling_it_in_pablo_is_not_undone_by_the_next_read(
        self, clients: _Harness, mock_user: User
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])
        booked = clients.followed("e1")
        assert booked is not None
        booked.status = AppointmentStatus.CANCELLED
        clients.appointments.update(booked)

        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])

        live = [
            a
            for a in clients.appointments.list_by_range(USER_ID, _in(0), _in(30))
            if a.status != AppointmentStatus.CANCELLED
        ]
        assert live == []

    def test_a_removal_in_google_can_be_undone(self, clients: _Harness, mock_user: User) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])
        clients.poll(mock_user, [_event("e1", cancelled=True)])
        gone = clients.followed("e1")
        assert gone is not None
        assert gone.status == AppointmentStatus.CANCELLED

        restored = clients.follower.resolve(USER_ID, gone.id, Resolution.KEEP_PABLO)

        assert restored.status == AppointmentStatus.CONFIRMED


class TestWhichPartOfATitleBooks:
    """Only the session's own name books: the whole title, a whole piece of it,
    or the name after a session word and "with"."""

    @pytest.fixture
    def clients(self, h: _Harness) -> _Harness:
        h.patient("p1", "Jane", "Smith")
        return h

    @pytest.mark.parametrize(
        "title",
        [
            "Jane Smith",
            "Smith, Jane",
            "Jane Smith - Therapy",
            "Session with Jane Smith",
            "Therapy session with Jane Smith",
            "Med management with Jane Smith",
            "INTAKE with Jane Smith",
            "Follow-up with Jane Smith",
        ],
    )
    def test_the_sessions_own_name_books(
        self, clients: _Harness, mock_user: User, title: str
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title=title, series=None)])

        booked = clients.followed("e1")
        assert booked is not None
        assert booked.patient_id == "p1"

    @pytest.mark.parametrize(
        "title", ["Lunch with Jane Smith", "Call with Jane Smith", "Coffee with Jane Smith"]
    )
    def test_a_name_the_title_only_mentions_is_asked_with_the_client_filled_in(
        self, clients: _Harness, mock_user: User, title: str
    ) -> None:
        clients.poll(mock_user, [_event("e1", _in(2), title=title)])

        assert clients.followed("e1") is None
        [question] = clients.outside.questions(USER_ID)
        assert question.match.patient_id == "p1"


class TestWhenANameDoesNotBook:
    """The clinician's choice: a title naming one client is asked, pre-filled."""

    @pytest.fixture
    def asking(self, h: _Harness) -> _Harness:
        h.users.save_preferences(USER_ID, UserPreferences(book_sessions_named_in_title=False))
        h.patient("p1", "Jane", "Smith")
        return h

    @pytest.mark.parametrize("series", [SERIES, None])
    def test_a_title_naming_one_client_is_asked_with_them_filled_in(
        self, asking: _Harness, mock_user: User, series: str | None
    ) -> None:
        asking.poll(mock_user, [_event("e1", _in(2), title="Jane Smith", series=series)])

        assert asking.followed("e1") is None
        [question] = asking.outside.questions(USER_ID)
        assert question.match.patient_id == "p1"

    def test_a_series_already_answered_still_books(self, asking: _Harness, mock_user: User) -> None:
        asking.poll(mock_user, [_event("e1", _in(2), title="Jane Smith")])
        asking.answer("p1")

        asking.poll(mock_user, [_event("e2", _in(9), title="Jane Smith")])

        booked = asking.followed("e2")
        assert booked is not None
        assert booked.patient_id == "p1"

    def test_a_clinician_who_has_not_chosen_gets_the_default(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.users.save_preferences(USER_ID, UserPreferences())
        h.patient("p1", "Jane", "Smith")

        h.poll(mock_user, [_event("e1", _in(2), title="Jane Smith", series=None)])

        assert (h.followed("e1") is not None) is BOOK_SESSIONS_NAMED_IN_TITLE_BY_DEFAULT

    def test_turning_it_on_books_the_next_read(self, asking: _Harness, mock_user: User) -> None:
        asking.poll(mock_user, [_event("e1", _in(2), title="Jane Smith", series=None)])
        assert asking.followed("e1") is None

        asking.users.save_preferences(USER_ID, UserPreferences(book_sessions_named_in_title=True))
        asking.poll(mock_user, [_event("e1", _in(2), title="Jane Smith", series=None)])

        booked = asking.followed("e1")
        assert booked is not None
        assert booked.patient_id == "p1"


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


class TestAfterADisconnect:
    """A disconnect forgets the events and the answers; the appointments keep their links.

    So connecting again and answering the series once more picks the same
    appointments back up: none is booked twice, and each follows Google again.
    """

    def test_answering_again_relinks_the_sessions_and_they_follow_google(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.patient("p1", "Jane", "Smith")
        events = [_event("e1", _in(2)), _event("e2", _in(9))]
        h.poll(mock_user, events)
        h.answer("p1")
        booked = {e: h.followed(e).id for e in ("e1", "e2")}  # type: ignore[union-attr]

        forget_google_calendar(USER_ID, events=h.events, mappings=h.mappings)
        h.poll(mock_user, events)
        assert len(h.outside.questions(USER_ID)) == 1

        h.answer("p1")

        assert h.outside.questions(USER_ID) == []
        appointments = h.appointments.list_by_range(USER_ID, _in(0), _in(30))
        assert sorted(a.id for a in appointments) == sorted(booked.values())
        rows = h.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
        assert {r.source_event_id: r.appointment_id for r in rows} == booked
        # And it follows: a move in Google moves the same appointment.
        moved_to = _in(3, hour=10)
        h.poll(mock_user, [_event("e1", moved_to)])
        followed = h.followed("e1")
        assert followed is not None
        assert (followed.id, followed.start_at) == (booked["e1"], moved_to)


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
        h.patient("p2", "Jane", "Smith")
        h.poll(mock_user, [_event("x", _in(2), title="Jane Smith", series=None)])
        assert h.open_ids() == ["x"]
        h.patients.delete("p1", USER_ID)
        h.patients.delete("p2", USER_ID)

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
