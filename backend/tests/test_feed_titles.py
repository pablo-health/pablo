# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A followed session books without asking only when its title names one client.

Run against two captured reads of one SimplePractice feed (see
``fixtures/simplepractice_feed/README.md``): the same appointments with the
calendar sync showing initials, then full names. Initials never identify
anyone; a full name identifies a client when exactly one chart bears it. A
provider series holds its answer only while it keeps its title, and an
inactive chart is never booked without asking.
"""

from __future__ import annotations

import base64
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    answered_title_digest,
    calendar_source_identifier,
    ical_source,
)
from app.main import app
from app.models.patient import Patient
from app.patients.identifiers import PRACTICE_SCOPE, calendar_scope
from app.patients.matching import remember_match, remember_not_a_client
from app.repositories.external_calendar_event import (
    ANSWER_CLIENT,
    ANSWER_NOT_A_CLIENT,
    ExternalCalendarEvent,
    InMemoryExternalCalendarEventRepository,
)
from app.repositories.ical_sync_config import ICalSyncConfig
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.patient_source_mapping import InMemoryPatientSourceMappingRepository
from app.routes.ical_sync import _get_service
from app.routes.outside_sessions import get_external_calendar_events
from app.routes.scheduling import (
    get_appointment_repository,
    get_google_calendar_service,
    get_owner_timezone,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.ical_sync_service import ICalSyncService, ParsedEvent, feed_identity
from app.services.outside_sessions import ONE_SESSION_AT_A_TIME, OutsideSessions
from app.services.token_encryption import encrypt_tokens
from app.settings import get_settings
from app.utcnow import utc_now

from tests.test_ical_sync import SH_ICAL_DATA, InMemoryICalSyncConfigRepo

if TYPE_CHECKING:
    from collections.abc import Generator

    from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures" / "simplepractice_feed"
INITIALS = (FIXTURES / "initials.ics").read_text()
FULL_NAMES = (FIXTURES / "full_names.ics").read_text()

USER = "user1"
SP = "simplepractice"
FEED_SOURCE = ical_source(SP)
#: The followed main calendar's real id.
MAIN = "clinician@example.test"


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patient(patient_id: str, first: str, last: str, *, status: str = "active") -> Patient:
    now = utc_now()
    return Patient(
        id=patient_id,
        first_name=first,
        last_name=last,
        status=status,
        created_at=now,
        updated_at=now,
    )


class _Feed:
    """A SimplePractice feed followed by one clinician, over in-memory storage."""

    def __init__(self) -> None:
        self.configs = InMemoryICalSyncConfigRepo()
        self.appointments = InMemoryAppointmentRepository()
        self.patients = InMemoryPatientRepository()
        self.mappings = InMemoryPatientSourceMappingRepository()
        self.events = InMemoryExternalCalendarEventRepository()
        self.configs.save(
            ICalSyncConfig(
                user_id=USER,
                ehr_system=SP,
                encrypted_feed_url=encrypt_tokens(
                    {"feed_url": "https://secure.simplepractice.com/ical/test/feed.ics"}
                ),
                connected_at=utc_now(),
            )
        )
        self.service = ICalSyncService(
            config_repo=self.configs,  # type: ignore[arg-type]
            appointment_repo=self.appointments,
            patient_repo=self.patients,
            mapping_repo=self.mappings,
            external_events=self.events,
        )
        self.outside = self.service._outside

    def chart(self, patient_id: str, first: str, last: str, *, status: str = "active") -> Patient:
        patient = self.patients.create(_patient(patient_id, first, last, status=status), USER)
        self.appointments.grant_access(patient_id, USER)
        return patient

    def sync(self, ical: str) -> Any:
        with patch.object(ICalSyncService, "_fetch_feed", return_value=ical):
            [result] = self.service.sync(USER, SP)
        return result

    def booked(self) -> dict[str, str]:
        """uid -> patient id, for every appointment the feed made."""
        return {
            a.ical_uid or "": a.patient_id for a in self.appointments.list_by_ical_source(USER, SP)
        }

    def questions(self) -> list[Any]:
        return self.outside.questions(USER)

    def questions_titled(self, title: str) -> list[Any]:
        return [q for q in self.questions() if q.title == title]

    def soonest(self, title: str) -> Any:
        return min(self.questions_titled(title), key=lambda q: q.next_start_at)

    def stored_title_style(self) -> str | None:
        config = self.configs.get(USER, SP)
        assert config is not None
        return config.title_style


@pytest.fixture
def feed() -> _Feed:
    return _Feed()


# --- The captured feeds ----------------------------------------------------


class TestCapturedFeeds:
    """The parser reads what the feed really carries, under both settings."""

    def test_both_settings_carry_the_same_appointments(self, feed: _Feed) -> None:
        initials = {e.uid: e for e in feed.service._parse_events(INITIALS)}
        names = {e.uid: e for e in feed.service._parse_events(FULL_NAMES)}

        assert len(initials) == 44
        # One appointment was booked between the two reads.
        assert len(names) == 45
        assert set(initials) < set(names)
        for uid, event in initials.items():
            assert (event.start_at, event.end_at, event.url) == (
                names[uid].start_at,
                names[uid].end_at,
                names[uid].url,
            )

    def test_the_setting_changes_only_the_title(self, feed: _Feed) -> None:
        initials = {e.uid: e.summary for e in feed.service._parse_events(INITIALS)}
        names = {e.uid: e.summary for e in feed.service._parse_events(FULL_NAMES)}

        assert Counter(initials.values()) == {"J.A. Appointment": 38, "P.B. Appointment": 6}
        # Four different clients arrive as "J.A.", one name typed in lower case.
        assert {names[uid] for uid, title in initials.items() if title == "J.A. Appointment"} == {
            "James Anderson Appointment",
            "Jamie Appleseed Appointment",
            "Jane Abbott Appointment",
            "John Adams Appointment",
        }
        assert names[next(uid for uid in names if uid not in initials)] == "jane smith Appointment"

    def test_a_weekly_series_arrives_as_one_event_per_occurrence(self, feed: _Feed) -> None:
        assert "RRULE" not in FULL_NAMES.split("END:VTIMEZONE")[1]
        tuesdays = [
            e
            for e in feed.service._parse_events(FULL_NAMES)
            if e.summary == "James Anderson Appointment"
        ]
        assert len(tuesdays) == 30
        # The UID is the appointment number: allocated when the appointment
        # was booked, so it says nothing about the client.
        assert all(e.uid.isdigit() for e in tuesdays)

    def test_every_title_is_read_as_initials_or_a_name(self, feed: _Feed) -> None:
        kinds = {feed_identity(SP, e.summary).kind for e in feed.service._parse_events(INITIALS)}
        assert kinds == {"initials"}
        kinds = {feed_identity(SP, e.summary).kind for e in feed.service._parse_events(FULL_NAMES)}
        assert kinds == {"name"}
        assert feed_identity(SP, "J.A. Appointment").identifier == "J.A."
        assert feed_identity(SP, "J.Q.A. Appointment").kind == "initials"
        assert feed_identity(SP, "jane smith Appointment").identifier == "jane smith"

    def test_a_read_records_how_the_feed_names_clients(self, feed: _Feed) -> None:
        assert feed.sync(INITIALS).title_style == "initials"
        assert feed.stored_title_style() == "initials"
        assert feed.service.get_status(USER)[0].title_style == "initials"

        assert feed.sync(FULL_NAMES).title_style == "names"
        assert feed.stored_title_style() == "names"


# --- Initials -----------------------------------------------------------------


class TestInitialsFeed:
    """Four clients titled "J.A.": nothing books on its own, every event is asked."""

    @pytest.fixture
    def four(self, feed: _Feed) -> _Feed:
        feed.chart("john", "John", "Adams")
        feed.chart("james", "James", "Anderson")
        feed.chart("jane", "Jane", "Abbott")
        feed.chart("jamie", "Jamie", "Appleseed")
        feed.chart("pablo", "Pablo", "Bear")
        return feed

    def test_nothing_books_and_every_event_is_its_own_question(self, four: _Feed) -> None:
        result = four.sync(INITIALS)

        assert result.created == 0
        assert four.booked() == {}
        questions = four.questions()
        assert len(questions) == 44
        assert all(q.outside_session_id is not None and len(q.rows) == 1 for q in questions)
        ja = four.questions_titled("J.A. Appointment")
        assert len(ja) == 38
        assert all(sorted(q.match.possible_ids) == ["james", "jamie", "jane", "john"] for q in ja)
        assert all(q.suggested_patient_id is None for q in ja)

    def test_even_a_unique_match_is_offered_not_booked(self, feed: _Feed) -> None:
        feed.chart("pablo", "Pablo", "Bear")

        feed.sync(INITIALS)

        assert feed.booked() == {}
        pb = feed.questions_titled("P.B. Appointment")
        assert len(pb) == 6
        assert all(q.match.patient_id == "pablo" for q in pb)

    def test_an_answer_settles_one_event_and_pre_fills_the_rest(self, four: _Feed) -> None:
        four.sync(INITIALS)
        first = four.soonest("J.A. Appointment")

        rows = four.outside.answer(
            USER, FEED_SOURCE, "J.A.", patient_id="john", row_id=first.outside_session_id
        )

        assert [r.id for r in rows] == [first.outside_session_id]
        assert four.booked() == {first.rows[0].source_event_id: "john"}
        # Remembered as the pre-fill for the next "J.A.", nothing more.
        again = four.questions_titled("J.A. Appointment")
        assert len(again) == 37
        assert all(q.suggested_patient_id == "john" for q in again)
        assert all(q.match.possible_ids[0] == "john" for q in again)
        assert all(
            sorted(q.match.possible_ids) == ["james", "jamie", "jane", "john"] for q in again
        )

        # The next read books nothing more.
        assert four.sync(INITIALS).created == 0
        assert len(four.booked()) == 1

    def test_an_answer_for_every_event_at_once_is_refused(self, four: _Feed) -> None:
        """The legacy resolve-client path can't settle 38 events with one answer."""
        four.sync(INITIALS)

        with pytest.raises(ValueError, match=ONE_SESSION_AT_A_TIME):
            four.service.resolve_client(USER, SP, "J.A.", "john")

        assert four.booked() == {}
        assert len(four.questions_titled("J.A. Appointment")) == 38

    def test_not_a_client_is_one_events_answer(self, four: _Feed) -> None:
        four.sync(INITIALS)
        first = four.soonest("J.A. Appointment")

        four.outside.answer(
            USER, FEED_SOURCE, "J.A.", patient_id=None, row_id=first.outside_session_id
        )

        assert len(four.questions_titled("J.A. Appointment")) == 37
        row = four.events.get_by_id(USER, first.outside_session_id)
        assert row is not None
        assert row.answer == ANSWER_NOT_A_CLIENT
        # Nothing was remembered for the title, and the next read asks the
        # rest again without bringing this one back.
        assert four.mappings.list_by_source(PRACTICE_SCOPE, SP) == []
        four.sync(INITIALS)
        assert len(four.questions_titled("J.A. Appointment")) == 37


# --- Full names ---------------------------------------------------------------


class TestFullNameFeed:
    def test_a_slot_handed_from_one_client_to_another(self, feed: _Feed) -> None:
        """John Adams, then James Anderson, on Tuesdays at ten."""
        feed.chart("john", "John", "Adams")

        result = feed.sync(FULL_NAMES)

        booked = feed.booked()
        assert set(booked.values()) == {"john"}
        assert len(booked) == 3
        # James has no chart: one question for the whole run, a new client.
        [james] = feed.questions_titled("James Anderson Appointment")
        assert len(james.rows) == 30
        assert james.outside_session_id is None
        assert (james.match.patient_id, james.match.possible_ids) == (None, [])
        assert result.created == 3

        # With a chart of his own, his run books to it.
        feed.chart("james", "James", "Anderson")
        assert feed.sync(FULL_NAMES).created == 30
        assert Counter(feed.booked().values()) == {"john": 3, "james": 30}

    def test_a_name_two_charts_share_is_asked_every_time(self, feed: _Feed) -> None:
        feed.chart("bear1", "Pablo", "Bear")
        feed.chart("bear2", "Pablo A", "Bear")

        feed.sync(FULL_NAMES)

        assert "bear1" not in feed.booked().values()
        assert "bear2" not in feed.booked().values()
        pablo = feed.questions_titled("Pablo Bear Appointment")
        assert len(pablo) == 6
        assert all(q.outside_session_id is not None for q in pablo)
        assert all(sorted(q.match.possible_ids) == ["bear1", "bear2"] for q in pablo)

        # An answer is the next pre-fill, and books only its own event.
        first = feed.soonest("Pablo Bear Appointment")
        feed.outside.answer(
            USER, FEED_SOURCE, "Pablo Bear", patient_id="bear1", row_id=first.outside_session_id
        )
        again = feed.questions_titled("Pablo Bear Appointment")
        assert len(again) == 5
        assert all(q.suggested_patient_id == "bear1" for q in again)
        assert feed.sync(FULL_NAMES).created == 0
        assert list(feed.booked().values()) == ["bear1"]

    def test_exactly_one_chart_with_the_name_books(self, feed: _Feed) -> None:
        feed.chart("jane", "Jane", "Smith")
        feed.chart("abbott", "Jane", "Abbott")

        feed.sync(FULL_NAMES)

        booked = feed.booked()
        assert Counter(booked.values()) == {"jane": 1, "abbott": 1}
        # Typed in lower case on the feed; the chart is the chart.
        assert feed.questions_titled("jane smith Appointment") == []

    def test_a_middle_initial_on_the_chart_does_not_hide_the_client(self, feed: _Feed) -> None:
        feed.chart("bear", "Pablo A", "Bear")

        feed.sync(FULL_NAMES)

        assert Counter(feed.booked().values()) == {"bear": 6}


# --- What still books on its own ---------------------------------------------


class TestWhatStillBooks:
    def test_a_feeds_own_client_code(self, feed: _Feed) -> None:
        sh = "sessions_health"
        feed.configs.save(
            ICalSyncConfig(
                user_id=USER,
                ehr_system=sh,
                encrypted_feed_url=encrypt_tokens(
                    {"feed_url": "https://app.sessionshealth.com/calendars/test/calendar.ics"}
                ),
                connected_at=utc_now(),
            )
        )
        feed.chart("p1", "Pablo", "Bear")
        remember_match(sh, "SH00001", "p1", feed.outside.context(USER), scope=PRACTICE_SCOPE)

        with patch.object(ICalSyncService, "_fetch_feed", return_value=SH_ICAL_DATA):
            [result] = feed.service.sync(USER, sh)

        assert result.created == 1
        assert result.title_style == "codes"
        [appointment] = feed.appointments.list_by_ical_source(USER, sh)
        assert appointment.patient_id == "p1"


class _Google:
    """A followed main calendar, for what a provider series does."""

    def __init__(self) -> None:
        self.events = InMemoryExternalCalendarEventRepository()
        self.appointments = InMemoryAppointmentRepository()
        self.patients = InMemoryPatientRepository()
        self.mappings = InMemoryPatientSourceMappingRepository()
        self.outside = OutsideSessions(
            self.events, self.appointments, self.patients, self.mappings, main_calendar_id=MAIN
        )

    def chart(self, patient_id: str, first: str, last: str, *, status: str = "active") -> None:
        self.patients.create(_patient(patient_id, first, last, status=status), USER)
        self.appointments.grant_access(patient_id, USER)

    def remember(self, series: str, patient_id: str, *, title: str | None) -> None:
        remember_match(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(series, "", 0, "00:00"),
            patient_id,
            self.outside.context(USER),
            scope=calendar_scope(MAIN),
            answered_title=answered_title_digest(title) if title is not None else None,
        )

    def poll(self, event_id: str, title: str, *, series: str = "wk", days: int = 3) -> list[Any]:
        start = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
        return self.outside.ingest_google(
            USER,
            [
                {
                    "google_event_id": event_id,
                    "status": "confirmed",
                    "summary": title,
                    "series_id": series,
                    "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
                }
            ],
        ).booked


class TestARetitledSeries:
    def test_a_series_keeping_its_title_books_and_a_retitled_one_asks(self) -> None:
        g = _Google()
        g.chart("mine", "Jane", "Smith")
        g.remember("wk", "mine", title="Jane weekly")

        assert [a.patient_id for a in g.poll("e1", "Jane weekly")] == ["mine"]
        # Every event of the series edited to another name: same id, new title.
        assert g.poll("e2", "Rowan weekly", days=10) == []

        [question] = g.outside.questions(USER)
        assert question.suggested_patient_id == "mine"
        assert question.match.patient_id is None
        assert question.outside_session_id is None

    def test_an_answer_from_before_titles_were_kept_is_asked_once(self) -> None:
        g = _Google()
        g.chart("mine", "Jane", "Smith")
        g.remember("wk", "mine", title=None)

        assert g.poll("e1", "Jane weekly") == []
        [question] = g.outside.questions(USER)
        assert question.suggested_patient_id == "mine"

        # Confirming records the title; the next event books.
        g.outside.answer(
            USER, GOOGLE_CALENDAR_SOURCE, question.source_identifier, patient_id="mine"
        )
        assert [a.patient_id for a in g.poll("e2", "Jane weekly", days=10)] == ["mine"]

    def test_a_title_is_matched_ignoring_case_and_spacing(self) -> None:
        g = _Google()
        g.chart("mine", "Jane", "Smith")
        g.remember("wk", "mine", title="Jane  Weekly")

        assert [a.patient_id for a in g.poll("e1", "jane weekly")] == ["mine"]


class TestAnInactiveChart:
    def test_is_asked_whatever_the_evidence(self, feed: _Feed) -> None:
        feed.chart("jane", "Jane", "Smith", status="inactive")
        feed.chart("john", "John", "Adams", status="on_hold")
        remember_match(
            "simplepractice", "John Adams", "john", feed.outside.context(USER), scope=PRACTICE_SCOPE
        )

        feed.sync(FULL_NAMES)

        assert feed.booked() == {}
        [jane] = feed.questions_titled("jane smith Appointment")
        assert (jane.match.patient_id, jane.client_inactive) == ("jane", True)
        [john] = feed.questions_titled("John Adams Appointment")
        assert (john.suggested_patient_id, john.client_inactive) == ("john", True)
        assert len(john.rows) == 3

    def test_a_remembered_series_on_an_inactive_chart_is_asked(self) -> None:
        g = _Google()
        g.chart("mine", "Jane", "Smith", status="inactive")
        g.remember("wk", "mine", title="Jane weekly")

        assert g.poll("e1", "Jane weekly") == []
        [question] = g.outside.questions(USER)
        assert (question.suggested_patient_id, question.client_inactive) == ("mine", True)


# --- Over the API ----------------------------------------------------------------


class _Wired:
    def __init__(
        self,
        patients: InMemoryPatientRepository,
        mappings: InMemoryPatientSourceMappingRepository,
    ) -> None:
        self.events = InMemoryExternalCalendarEventRepository()
        self.appointments = InMemoryAppointmentRepository()
        self.patients = patients
        self.mappings = mappings
        self.calendar = MagicMock()
        self.calendar.get_sync_status.return_value = {"connected": False}

    def hold(self, row_id: str, title: str, days: int) -> None:
        start = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
        self.events.save(
            ExternalCalendarEvent(
                id=row_id,
                user_id="test-user-123",
                source=FEED_SOURCE,
                source_event_id=f"uid-{row_id}",
                start_at=start,
                end_at=start + timedelta(minutes=50),
                title=title,
            )
        )

    def chart(self, patient_id: str, first: str, last: str, *, status: str = "active") -> None:
        self.patients.create(_patient(patient_id, first, last, status=status), "test-user-123")
        self.appointments.grant_access(patient_id, "test-user-123")

    def status(self, patient_id: str) -> str:
        patient = self.patients.get(patient_id, "test-user-123")
        assert patient is not None
        return patient.status


@pytest.fixture
def wired(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_mapping_repo: InMemoryPatientSourceMappingRepository,
) -> _Wired:
    w = _Wired(mock_repo, mock_mapping_repo)
    app.dependency_overrides[get_owner_timezone] = lambda: utc_now().tzinfo
    app.dependency_overrides[get_external_calendar_events] = lambda: w.events
    app.dependency_overrides[get_appointment_repository] = lambda: w.appointments
    app.dependency_overrides[get_google_calendar_service] = lambda: w.calendar
    return w


def _questions(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/calendar/outside-sessions/questions")
    assert response.status_code == 200, response.text
    questions: list[dict[str, Any]] = response.json()["questions"]
    return questions


def _answer(client: TestClient, **answer: Any) -> Any:
    body = {"source": FEED_SOURCE, "source_identifier": "J.A.", **answer}
    return client.post("/api/calendar/outside-sessions/answer", json={"answers": [body]})


class TestInitialsOverTheApi:
    def test_each_event_is_its_own_question_and_answer(
        self, client: TestClient, wired: _Wired
    ) -> None:
        wired.chart("john", "John", "Adams")
        wired.chart("jane", "Jane", "Abbott")
        wired.hold("r1", "J.A. Appointment", 2)
        wired.hold("r2", "J.A. Appointment", 9)

        questions = _questions(client)

        assert [q["key"] for q in questions] == [
            f"{FEED_SOURCE}|J.A.|r1",
            f"{FEED_SOURCE}|J.A.|r2",
        ]
        assert [q["outside_session_id"] for q in questions] == ["r1", "r2"]
        assert all(q["sessions"] == 1 for q in questions)

        response = _answer(client, patient_id="john", outside_session_id="r1")

        assert response.status_code == 200, response.text
        assert response.json()["appointments_created"] == 1
        [left] = _questions(client)
        assert left["outside_session_id"] == "r2"
        # Pre-filled with the last answer, and still asked.
        assert left["match"]["suggested_patient_id"] == "john"
        assert left["match"]["patient"] is None

    def test_an_answer_for_every_event_at_once_is_refused(
        self, client: TestClient, wired: _Wired
    ) -> None:
        wired.chart("john", "John", "Adams")
        wired.hold("r1", "J.A. Appointment", 2)
        wired.hold("r2", "J.A. Appointment", 9)

        response = _answer(client, patient_id="john")

        assert response.status_code == 400
        assert response.json()["error"]["message"] == ONE_SESSION_AT_A_TIME
        assert wired.appointments.list_by_ical_source("test-user-123", SP) == []


class TestReactivatingOverTheApi:
    def _hold_janes(self, wired: _Wired, *, status: str) -> None:
        wired.chart("jane", "Jane", "Smith", status=status)
        wired.hold("r1", "Jane Smith Appointment", 2)
        wired.hold("r2", "Jane Smith Appointment", 9)

    def test_the_question_says_the_chart_is_inactive(
        self, client: TestClient, wired: _Wired
    ) -> None:
        self._hold_janes(wired, status="inactive")

        [question] = _questions(client)

        assert question["client_inactive"] is True
        assert question["match"]["suggested_patient_id"] == "jane"
        assert question["outside_session_id"] is None
        assert question["sessions"] == 2

    def test_confirming_with_reactivate_makes_the_chart_active_and_books(
        self, client: TestClient, wired: _Wired
    ) -> None:
        self._hold_janes(wired, status="inactive")

        response = _answer(
            client, source_identifier="Jane Smith", patient_id="jane", reactivate=True
        )

        assert response.status_code == 200, response.text
        assert response.json()["appointments_created"] == 2
        assert wired.status("jane") == "active"
        assert all(
            r.answer == ANSWER_CLIENT
            for r in wired.events.list_by_source("test-user-123", FEED_SOURCE)
        )

    def test_confirming_without_it_books_and_leaves_the_status(
        self, client: TestClient, wired: _Wired
    ) -> None:
        self._hold_janes(wired, status="on_hold")

        response = _answer(client, source_identifier="Jane Smith", patient_id="jane")

        assert response.status_code == 200, response.text
        assert response.json()["appointments_created"] == 2
        assert wired.status("jane") == "on_hold"


def test_the_status_route_says_how_a_feed_names_clients(client: TestClient) -> None:
    feed = _Feed()
    feed.configs.save(
        ICalSyncConfig(
            user_id="test-user-123",
            ehr_system=SP,
            encrypted_feed_url=encrypt_tokens(
                {"feed_url": "https://secure.simplepractice.com/ical/test/feed.ics"}
            ),
            connected_at=utc_now(),
            title_style="initials",
        )
    )
    app.dependency_overrides[_get_service] = lambda: feed.service
    try:
        response = client.get("/api/ical-sync/status")
    finally:
        app.dependency_overrides.pop(_get_service, None)

    assert response.status_code == 200, response.text
    [connection] = response.json()["connections"]
    assert connection["title_style"] == "initials"


# --- Review findings ----------------------------------------------------------


class TestWhatIsRememberedNeverStandsInForIdentity:
    def test_a_name_remembered_to_a_deleted_chart_is_not_handed_to_its_namesake(
        self, feed: _Feed
    ) -> None:
        feed.chart("first", "Jane", "Smith")
        feed.chart("second", "Jane", "Smith")
        remember_match(SP, "jane smith", "first", feed.outside.context(USER), scope=PRACTICE_SCOPE)
        feed.patients.delete("first", USER)

        feed.sync(FULL_NAMES)

        assert "second" not in feed.booked().values()
        [question] = feed.questions_titled("jane smith Appointment")
        assert "second" in question.match.possible_ids or question.match.patient_id == "second"

    def test_a_name_said_to_be_no_client_is_never_booked(self, feed: _Feed) -> None:
        feed.chart("jane", "Jane", "Smith")
        remember_not_a_client(SP, "jane smith", feed.outside.context(USER), scope=PRACTICE_SCOPE)
        start = utc_now()
        event = ParsedEvent(
            uid="u1",
            summary="jane smith Appointment",
            start_at=start,
            end_at=start,
            duration_minutes=60,
        )

        row = ICalSyncService._row(USER, SP, event)

        assert feed.outside.unattended(row, feed.outside.context(USER)) is None

    def test_initials_remembered_to_a_colleagues_chart_still_offer_my_own(
        self, feed: _Feed
    ) -> None:
        feed.patients.create(_patient("theirs", "Jack", "Ames"), "colleague")
        feed.chart("john", "John", "Adams")
        feed.chart("james", "James", "Anderson")
        remember_match(SP, "J.A.", "theirs", feed.outside.context(USER), scope=PRACTICE_SCOPE)

        feed.sync(INITIALS)

        ja = feed.questions_titled("J.A. Appointment")
        assert len(ja) == 38
        assert all(sorted(q.match.possible_ids) == ["james", "john"] for q in ja)
        assert all(q.suggested_patient_id is None for q in ja)
        first = min(ja, key=lambda q: q.next_start_at)
        # A new client is not refused: initials never say it is theirs.
        assert not feed.outside.seen_by_someone_else(
            USER, FEED_SOURCE, "J.A.", feed.outside.context(USER), row_id=first.outside_session_id
        )

    def test_initials_once_said_to_be_no_client_still_offer_every_candidate(
        self, feed: _Feed
    ) -> None:
        feed.chart("john", "John", "Adams")
        feed.chart("james", "James", "Anderson")
        remember_not_a_client(SP, "J.A.", feed.outside.context(USER), scope=PRACTICE_SCOPE)

        feed.sync(INITIALS)

        ja = feed.questions_titled("J.A. Appointment")
        assert len(ja) == 38
        assert all(sorted(q.match.possible_ids) == ["james", "john"] for q in ja)

    def test_an_event_answered_as_no_client_is_not_reported_again(self, feed: _Feed) -> None:
        feed.chart("john", "John", "Adams")
        feed.chart("james", "James", "Anderson")
        assert len(feed.sync(INITIALS).unmatched_events) == 44
        first = feed.soonest("J.A. Appointment")
        feed.outside.answer(
            USER, FEED_SOURCE, "J.A.", patient_id=None, row_id=first.outside_session_id
        )

        assert len(feed.sync(INITIALS).unmatched_events) == 43


class TestTheTitleAnAnswerRecords:
    def test_is_the_title_the_question_showed(self) -> None:
        g = _Google()
        g.chart("mine", "Jane", "Smith")
        # One later occurrence was retitled; it reaches Pablo first.
        g.poll("moved", "Jane (moved)", days=10)
        g.poll("usual", "Jane weekly", days=3)
        [question] = g.outside.questions(USER)
        assert question.title == "Jane weekly"

        g.outside.answer(
            USER, GOOGLE_CALENDAR_SOURCE, question.source_identifier, patient_id="mine"
        )

        assert [a.patient_id for a in g.poll("next", "Jane weekly", days=17)] == ["mine"]


class TestAFeedsTitleStyle:
    def test_a_stray_event_does_not_hide_an_initials_feed(self, feed: _Feed) -> None:
        lunch = (
            "BEGIN:VEVENT\r\nDTSTAMP:20260930T163202Z\r\nUID:lunch-1\r\n"
            "DTSTART;TZID=America/New_York:20261001T120000\r\n"
            "DTEND;TZID=America/New_York:20261001T130000\r\nSUMMARY:Lunch\r\n"
            "END:VEVENT\r\nEND:VCALENDAR"
        )

        result = feed.sync(INITIALS.replace("END:VCALENDAR", lunch))

        assert result.title_style == "initials"


class TestReactivationOverTheApi:
    def test_a_pending_chart_is_not_made_active(self, client: TestClient, wired: _Wired) -> None:
        wired.chart("jane", "Jane", "Smith", status="pending")
        wired.hold("r1", "Jane Smith Appointment", 2)

        response = _answer(
            client, source_identifier="Jane Smith", patient_id="jane", reactivate=True
        )

        assert response.status_code == 200, response.text
        assert wired.status("jane") == "pending"
