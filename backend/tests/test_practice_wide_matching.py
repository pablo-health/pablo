# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client belongs to the practice: matching sees every chart, and never
charts or books a colleague's client for someone who doesn't see them.

Two clinicians in one practice. ``ME`` imports and answers; ``COLLEAGUE``
sees a client ``ME`` does not. The same promises are proven against real
Postgres in ``tests_integration/database/test_practice_client_directory.py``.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.calendar_providers.practice_import import build_proposal
from app.calendar_providers.provider import ImportCandidate
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    answered_title_digest,
    calendar_source_identifier,
)
from app.main import app
from app.models import User
from app.models.patient import Patient
from app.models.user import UserPreferences
from app.patients.matching import MatchContext, PatientHint, match_patient, remember_match
from app.repositories.external_calendar_event import (
    ExternalCalendarEvent,
    InMemoryExternalCalendarEventRepository,
)
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.patient_source_mapping import (
    InMemoryPatientSourceMappingRepository,
    PatientSourceMapping,
)
from app.routes.outside_sessions import get_external_calendar_events
from app.routes.scheduling import (
    configured_timezone,
    get_appointment_repository,
    get_google_calendar_service,
    get_owner_timezone,
    get_scheduling_service,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.outside_sessions import OutsideSessions
from app.settings import get_settings
from app.utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Generator

    from app.repositories import InMemoryUserRepository
    from fastapi.testclient import TestClient

ME = "test-user-123"
COLLEAGUE = "colleague-456"
COLLEAGUE_NAME = "Dr. Rivera"
_REDIRECT = "http://localhost:3000/dashboard/settings/calendar"
_SERIES = "rec-1"


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """The secret the answered-title digest is keyed under; every answer needs it."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patient(patient_id: str, first: str, last: str, **fields: Any) -> Patient:
    now = utc_now()
    return Patient(
        id=patient_id, first_name=first, last_name=last, created_at=now, updated_at=now, **fields
    )


@pytest.fixture
def colleague(mock_user_repo: InMemoryUserRepository) -> None:
    mock_user_repo.update(
        User(
            id=COLLEAGUE,
            email="rivera@example.com",
            name="Rivera",
            title="Dr.",
            created_at=utc_now(),
        )
    )


def _practice(*charts: tuple[Patient, str]) -> MatchContext:
    patients = InMemoryPatientRepository()
    for chart, clinician in charts:
        patients.create(chart, clinician)
    return MatchContext.for_practice(ME, patients, InMemoryPatientSourceMappingRepository())


class TestStrongEvidenceSeesThePractice:
    def test_a_colleagues_client_by_name_and_birthday_is_matched_and_not_seen(self) -> None:
        ctx = _practice(
            (_patient("theirs", "Jane", "Adams", date_of_birth="1980-01-02"), COLLEAGUE)
        )

        result = match_patient(
            PatientHint(full_name="Jane Adams", date_of_birth=date(1980, 1, 2)), ctx
        )

        assert (result.patient_id, result.evidence) == ("theirs", "name_and_dob")
        assert not result.visible
        candidate = ctx.candidate("theirs")
        assert candidate is not None
        assert candidate.clinician_ids == (COLLEAGUE,)

    def test_a_remembered_colleagues_client_is_matched_and_not_seen(self) -> None:
        ctx = _practice((_patient("theirs", "Jane", "Adams"), COLLEAGUE))
        remember_match("simplepractice", "J.A.", "theirs", ctx)

        result = match_patient(
            PatientHint(initials="J.A.", source="simplepractice", source_identifier="J.A."), ctx
        )

        assert (result.patient_id, result.evidence) == ("theirs", "remembered")
        assert not result.visible


class TestWeakEvidenceLooksOnlyAtMyCharts:
    def test_a_colleagues_client_by_initials_alone_is_no_match(self) -> None:
        """So the clinician may answer "new client"."""
        ctx = _practice((_patient("theirs", "Jane", "Adams"), COLLEAGUE))

        result = match_patient(PatientHint(initials="J.A."), ctx)

        assert result.patient_id is None
        assert result.possible_ids == []

    def test_a_colleagues_client_by_name_alone_is_no_match(self) -> None:
        ctx = _practice((_patient("theirs", "Jane", "Adams"), COLLEAGUE))

        result = match_patient(PatientHint(full_name="Jane Adams"), ctx)

        assert result.patient_id is None
        assert result.possible_ids == []

    def test_my_initials_match_stays_certain_when_a_colleague_has_the_same_initials(
        self,
    ) -> None:
        ctx = _practice(
            (_patient("mine", "Jane", "Adams"), ME),
            (_patient("theirs", "Joe", "Amato"), COLLEAGUE),
        )

        result = match_patient(PatientHint(initials="J.A."), ctx)

        assert (result.patient_id, result.evidence) == ("mine", "initials")
        assert result.visible

    def test_two_of_my_clients_sharing_initials_stay_a_question(self) -> None:
        ctx = _practice(
            (_patient("john", "John", "Adams"), ME),
            (_patient("james", "James", "Andersson"), ME),
        )

        result = match_patient(PatientHint(initials="J.A."), ctx)

        assert result.patient_id is None
        assert sorted(result.possible_ids) == ["james", "john"]


class TestTheMatcherSeesThePractice:
    def test_my_own_client_still_matches_and_is_seen(self) -> None:
        patients = InMemoryPatientRepository()
        patients.create(_patient("mine", "Jane", "Adams"), ME)
        patients.create(_patient("theirs", "Bo", "Li"), COLLEAGUE)
        ctx = MatchContext.for_practice(ME, patients, InMemoryPatientSourceMappingRepository())

        result = match_patient(PatientHint(full_name="Jane Adams"), ctx)

        assert result.patient_id == "mine"
        assert result.visible
        assert result.hidden_ids == []

    def test_an_email_shared_across_the_practice_is_a_question_not_an_answer(self) -> None:
        """Unique among my charts is not unique in the practice."""
        patients = InMemoryPatientRepository()
        patients.create(_patient("mine", "Ana", "Ruiz", email="family@example.com"), ME)
        patients.create(_patient("theirs", "Leo", "Ruiz", email="family@example.com"), COLLEAGUE)
        ctx = MatchContext.for_practice(ME, patients, InMemoryPatientSourceMappingRepository())

        result = match_patient(PatientHint(email="family@example.com"), ctx)

        assert result.patient_id is None
        assert sorted(result.possible_ids) == ["mine", "theirs"]
        assert result.hidden_ids == ["theirs"]
        assert result.visible_possible_ids == ["mine"]


# --- The calendar import -------------------------------------------------------


def _scan(client: TestClient, summary: str, *, timezone: str = "UTC") -> dict[str, Any]:
    now = datetime.now(UTC)
    first = now - timedelta(days=21)
    gcal = MagicMock()
    gcal.scan_for_practice_import.return_value = build_proposal(
        [
            ImportCandidate(
                provider_event_id=f"evt-{i}",
                start=first + timedelta(days=7 * i),
                end=first + timedelta(days=7 * i, minutes=50),
                summary=summary,
                attendee_count=0,
                series_id=_SERIES,
            )
            for i in range(6)
        ],
        now=now,
        timezone=timezone,
    )
    app.dependency_overrides[get_google_calendar_service] = lambda: gcal
    response = client.post(
        "/api/calendar/import/scan", params={"redirect_uri": _REDIRECT, "timezone": timezone}
    )
    assert response.status_code == 200, response.text
    [series] = response.json()["series"]
    series["_gcal"] = gcal
    return series


def _confirm(client: TestClient, series: dict[str, Any], patient_id: str | None) -> Any:
    return client.post(
        "/api/calendar/import/confirm",
        json={
            "series": [
                {
                    "candidate_key": series["candidate_key"],
                    "display_name": series["summary"],
                    "patient_id": patient_id,
                    "source_identifier": series["source_identifier"],
                    "start_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
                    "duration_minutes": 50,
                    "cadence": "weekly",
                    "occurrences": 2,
                    "timezone": "UTC",
                }
            ]
        },
    )


@pytest.fixture
def import_client(
    client: TestClient, mock_repo: InMemoryPatientRepository
) -> tuple[TestClient, InMemoryAppointmentRepository]:
    appointments = InMemoryAppointmentRepository()
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appointments)
    return client, appointments


def _remembered_as_theirs(mappings: InMemoryPatientSourceMappingRepository) -> None:
    """This series was answered as the colleague's client (the strong evidence)."""
    mappings.save(
        PatientSourceMapping(
            ME,
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier(_SERIES, "", 0, "00:00"),
            "theirs",
        )
    )


@pytest.mark.usefixtures("colleague")
class TestImportOfAColleaguesClient:
    def test_the_scan_says_who_sees_them_and_offers_nothing_to_import(
        self,
        import_client: tuple[TestClient, Any],
        mock_repo: InMemoryPatientRepository,
        mock_mapping_repo: InMemoryPatientSourceMappingRepository,
    ) -> None:
        client, _ = import_client
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        _remembered_as_theirs(mock_mapping_repo)

        series = _scan(client, "Jane Adams")

        assert series["match"] == {
            "patient": None,
            "possible": [],
            "suggested_patient_id": None,
            "seen_by": [COLLEAGUE_NAME],
        }
        assert series["preselected"] is False

    def test_confirming_them_as_a_new_client_is_refused_and_creates_nothing(
        self,
        import_client: tuple[TestClient, InMemoryAppointmentRepository],
        mock_repo: InMemoryPatientRepository,
        mock_mapping_repo: InMemoryPatientSourceMappingRepository,
    ) -> None:
        client, appointments = import_client
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        _remembered_as_theirs(mock_mapping_repo)
        series = _scan(client, "Jane Adams")

        response = _confirm(client, series, None)

        assert response.status_code == 400
        assert "Already a client of the practice" in response.text
        assert [c.id for c in mock_repo.practice_directory()] == ["theirs"]
        assert appointments.list_by_range(ME, utc_now(), utc_now() + timedelta(days=60)) == []

    def test_confirming_onto_their_chart_is_refused(
        self,
        import_client: tuple[TestClient, InMemoryAppointmentRepository],
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        client, appointments = import_client
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        series = _scan(client, "Jane Adams")

        response = _confirm(client, series, "theirs")

        assert response.status_code == 400
        assert appointments.list_by_range(ME, utc_now(), utc_now() + timedelta(days=60)) == []

    def test_a_colleagues_client_sharing_only_a_name_does_not_stop_a_new_client(
        self,
        import_client: tuple[TestClient, InMemoryAppointmentRepository],
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        """A name alone is not that client: it may be someone else entirely."""
        client, _ = import_client
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        series = _scan(client, "Jane Adams")
        assert series["match"] == {
            "patient": None,
            "possible": [],
            "suggested_patient_id": None,
            "seen_by": None,
        }

        response = _confirm(client, series, None)

        assert response.status_code == 200, response.text
        assert response.json()["patients_created"] == 1

    def test_my_own_client_still_imports_onto_my_chart(
        self,
        import_client: tuple[TestClient, InMemoryAppointmentRepository],
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        client, _ = import_client
        mock_repo.create(_patient("mine", "Jane", "Adams"), ME)
        mock_repo.create(_patient("theirs", "Bo", "Li"), COLLEAGUE)
        series = _scan(client, "Jane Adams")
        assert series["match"]["seen_by"] is None
        assert series["match"]["suggested_patient_id"] == "mine"

        response = _confirm(client, series, "mine")

        assert response.status_code == 200, response.text
        assert response.json()["confirmed"][0]["patient_id"] == "mine"


class TestImportZone:
    def test_the_clinicians_own_zone_is_read_instead_of_the_browsers(
        self,
        import_client: tuple[TestClient, Any],
        mock_user_repo: InMemoryUserRepository,
    ) -> None:
        client, _ = import_client
        mock_user_repo.save_preferences(ME, UserPreferences(timezone="America/Chicago"))

        series = _scan(client, "Jane Adams", timezone="Europe/Lisbon")

        call = series["_gcal"].scan_for_practice_import.call_args
        assert call.kwargs["timezone"] == "America/Chicago"

    def test_the_browsers_zone_is_used_when_none_was_chosen(
        self, import_client: tuple[TestClient, Any]
    ) -> None:
        client, _ = import_client

        series = _scan(client, "Jane Adams", timezone="Europe/Lisbon")

        call = series["_gcal"].scan_for_practice_import.call_args
        assert call.kwargs["timezone"] == "Europe/Lisbon"

    def test_a_stored_default_is_not_mistaken_for_a_choice(
        self, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.save_preferences(ME, UserPreferences(theme="warm-paper"))
        assert configured_timezone(mock_user_repo, ME) is None
        mock_user_repo.save_preferences(ME, UserPreferences(timezone="America/Denver"))
        assert configured_timezone(mock_user_repo, ME) == "America/Denver"


# --- Sessions followed from a calendar -----------------------------------------


def _hold(events: InMemoryExternalCalendarEventRepository, title: str) -> str:
    start = (utc_now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    events.save(
        ExternalCalendarEvent(
            id="row-e1",
            user_id=ME,
            source=GOOGLE_CALENDAR_SOURCE,
            source_event_id="e1",
            source_series_id="wk",
            start_at=start,
            end_at=start + timedelta(minutes=50),
            title=title,
        )
    )
    return calendar_source_identifier("wk", "", 0, "00:00")


@pytest.fixture
def outside(client: TestClient) -> tuple[InMemoryExternalCalendarEventRepository, Any]:
    events = InMemoryExternalCalendarEventRepository()
    appointments = InMemoryAppointmentRepository()
    calendar = MagicMock()
    calendar.get_sync_status.return_value = {
        "connected": True,
        "import_granted": True,
        "follow_calendar_id": "primary",
    }
    app.dependency_overrides[get_owner_timezone] = lambda: UTC
    app.dependency_overrides[get_external_calendar_events] = lambda: events
    app.dependency_overrides[get_appointment_repository] = lambda: appointments
    app.dependency_overrides[get_google_calendar_service] = lambda: calendar
    return events, appointments


def _answer(client: TestClient, identifier: str, **answer: Any) -> Any:
    return client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {"source": GOOGLE_CALENDAR_SOURCE, "source_identifier": identifier, **answer}
            ]
        },
    )


@pytest.mark.usefixtures("colleague")
class TestOutsideSessionsForAColleaguesClient:
    def test_the_question_says_who_sees_them(
        self,
        client: TestClient,
        outside: Any,
        mock_repo: InMemoryPatientRepository,
        mock_mapping_repo: InMemoryPatientSourceMappingRepository,
    ) -> None:
        events, _ = outside
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        identifier = _hold(events, "Jane Adams")
        mock_mapping_repo.save(
            PatientSourceMapping(ME, GOOGLE_CALENDAR_SOURCE, identifier, "theirs")
        )

        [question] = client.get("/api/calendar/outside-sessions/questions").json()["questions"]

        assert question["match"]["seen_by"] == [COLLEAGUE_NAME]
        assert question["match"]["possible"] == []

    def test_answering_with_a_new_client_is_refused(
        self,
        client: TestClient,
        outside: Any,
        mock_repo: InMemoryPatientRepository,
        mock_mapping_repo: InMemoryPatientSourceMappingRepository,
    ) -> None:
        events, _ = outside
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        identifier = _hold(events, "Jane Adams")
        mock_mapping_repo.save(
            PatientSourceMapping(ME, GOOGLE_CALENDAR_SOURCE, identifier, "theirs")
        )

        response = _answer(client, identifier, new_client_name="Jane Adams")

        assert response.status_code == 400
        assert len(events.list_open(ME)) == 1
        assert [c.id for c in mock_repo.practice_directory()] == ["theirs"]

    def test_a_colleagues_client_sharing_only_a_name_can_be_answered_as_new(
        self, client: TestClient, outside: Any, mock_repo: InMemoryPatientRepository
    ) -> None:
        events, _ = outside
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        identifier = _hold(events, "Jane Adams")

        [question] = client.get("/api/calendar/outside-sessions/questions").json()["questions"]
        assert question["match"]["seen_by"] is None

        response = _answer(client, identifier, new_client_name="Jane Adams")

        assert response.status_code == 200, response.text
        assert events.list_open(ME) == []

    def test_answering_with_their_chart_is_refused(
        self, client: TestClient, outside: Any, mock_repo: InMemoryPatientRepository
    ) -> None:
        events, appointments = outside
        mock_repo.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        identifier = _hold(events, "Jane Adams")

        response = _answer(client, identifier, patient_id="theirs")

        assert response.status_code == 400
        assert len(events.list_open(ME)) == 1
        assert appointments.list_by_range(ME, utc_now(), utc_now() + timedelta(days=30)) == []


class TestUnattendedBooking:
    def test_a_remembered_series_is_never_booked_onto_a_chart_i_dont_see(self) -> None:
        """Remembered, then the grant went: the session stays a question."""
        events = InMemoryExternalCalendarEventRepository()
        appointments = InMemoryAppointmentRepository()
        patients = InMemoryPatientRepository()
        mappings = InMemoryPatientSourceMappingRepository()
        patients.create(_patient("theirs", "Jane", "Adams"), COLLEAGUE)
        identifier = calendar_source_identifier("wk", "", 0, "00:00")
        mappings.save(PatientSourceMapping(ME, GOOGLE_CALENDAR_SOURCE, identifier, "theirs"))
        outside = OutsideSessions(events, appointments, patients, mappings)
        start = (utc_now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)

        result = outside.ingest_google(
            ME,
            [
                {
                    "google_event_id": "e1",
                    "status": "confirmed",
                    "summary": "Weekly 1:1",
                    "series_id": "wk",
                    "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
                }
            ],
        )

        assert result.booked == []
        assert [row.source_event_id for row in events.list_open(ME)] == ["e1"]

    def test_a_remembered_series_on_my_own_chart_is_still_booked(self) -> None:
        events = InMemoryExternalCalendarEventRepository()
        appointments = InMemoryAppointmentRepository()
        patients = InMemoryPatientRepository()
        mappings = InMemoryPatientSourceMappingRepository()
        patients.create(_patient("mine", "Jane", "Adams"), ME)
        appointments.grant_access("mine", ME)
        outside = OutsideSessions(events, appointments, patients, mappings)
        remember_match(
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier("wk", "", 0, "00:00"),
            "mine",
            outside.context(ME),
            answered_title=answered_title_digest("Weekly 1:1"),
        )
        start = (utc_now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)

        result = outside.ingest_google(
            ME,
            [
                {
                    "google_event_id": "e1",
                    "status": "confirmed",
                    "summary": "Weekly 1:1",
                    "series_id": "wk",
                    "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
                }
            ],
        )

        assert [a.patient_id for a in result.booked] == ["mine"]


def _slot_event(event_id: str, start: datetime) -> dict[str, Any]:
    """A one-off event with no provider series: remembered by weekday and time."""
    return {
        "google_event_id": event_id,
        "status": "confirmed",
        "summary": "Jane Adams",
        "series_id": None,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
    }


def test_two_of_my_clients_sharing_a_name_are_picked_once_per_slot() -> None:
    """Asked once; the next session at the same weekday and time is remembered."""
    events = InMemoryExternalCalendarEventRepository()
    patients = InMemoryPatientRepository()
    patients.create(_patient("first", "Jane", "Adams"), ME)
    patients.create(_patient("second", "Jane", "Adams"), ME)
    outside = OutsideSessions(
        events, InMemoryAppointmentRepository(), patients, InMemoryPatientSourceMappingRepository()
    )
    start = (utc_now() + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
    outside.ingest_google(ME, [_slot_event("e1", start)])
    [question] = outside.questions(ME)
    assert question.match.patient_id is None
    assert sorted(question.match.possible_ids) == ["first", "second"]

    outside.answer(ME, GOOGLE_CALENDAR_SOURCE, question.source_identifier, patient_id="first")
    outside.ingest_google(ME, [_slot_event("e2", start + timedelta(days=7))])

    [row] = events.list_open(ME)
    assert row.source_event_id == "e2"
    [identifier] = {identifier for _, identifier in outside.open_sessions(ME)}
    assert identifier == question.source_identifier
    match = match_patient(
        PatientHint(
            full_name="Jane Adams", source=GOOGLE_CALENDAR_SOURCE, source_identifier=identifier
        ),
        outside.context(ME),
    )
    assert (match.patient_id, match.evidence) == ("first", "remembered")
