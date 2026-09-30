# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The calendar's outside blocks, the banner's questions, and answering them."""

from __future__ import annotations

import base64
import os
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    calendar_source_identifier,
    ical_source,
)
from app.main import app
from app.models.patient import Patient
from app.patients.identifiers import calendar_scope
from app.patients.matching import MatchContext, remember_match
from app.repositories.external_calendar_event import (
    ExternalCalendarEvent,
    InMemoryExternalCalendarEventRepository,
)
from app.repositories.ical_sync_config import ICalSyncConfig
from app.routes.outside_sessions import get_external_calendar_events
from app.routes.scheduling import (
    get_appointment_repository,
    get_google_calendar_service,
    get_owner_timezone,
)
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.google_calendar_service import ReadableCalendar
from app.services.ical_sync_service import ICalSyncService
from app.services.outside_sessions import CALENDAR_NOT_KNOWN
from app.services.token_encryption import encrypt_tokens
from app.settings import get_settings
from app.utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Generator

    from app.repositories.patient import InMemoryPatientRepository
    from app.repositories.patient_source_mapping import (
        InMemoryPatientSourceMappingRepository,
    )
    from fastapi.testclient import TestClient

USER_ID = "test-user-123"
#: The main calendar's real id.
MAIN = "clinician@example.test"


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """The secret the answered-title digest is keyed under; every answer needs it."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


SERIES_KEY = calendar_source_identifier("wk", "", 0, "00:00")


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
        self.status: dict[str, Any] = {
            "connected": True,
            "import_granted": True,
            "follow_calendar_id": MAIN,
        }
        self.calendar.get_sync_status.side_effect = lambda _user_id: self.status
        self.calendar.known_main_calendar_id.return_value = MAIN
        self.calendar.list_readable_calendars.return_value = [
            ReadableCalendar(MAIN, MAIN, primary=True),
            ReadableCalendar("team@group.calendar.google.test", "Team", primary=False),
        ]
        self.calendar.set_followed_calendar.return_value = True

    def hold(self, event_id: str, days: int, *, series: str | None = "wk") -> None:
        start = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
        self.events.save(
            ExternalCalendarEvent(
                id=f"row-{event_id}",
                user_id=USER_ID,
                source=GOOGLE_CALENDAR_SOURCE,
                source_event_id=event_id,
                source_series_id=series,
                start_at=start,
                end_at=start + timedelta(minutes=50),
                title="Jane Smith",
            )
        )

    def client_named(self, patient_id: str) -> None:
        now = utc_now()
        self.patients.create(
            Patient(
                id=patient_id, first_name="Jane", last_name="Smith", created_at=now, updated_at=now
            ),
            USER_ID,
        )
        self.appointments.grant_access(patient_id, USER_ID)


@pytest.fixture
def wired(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_mapping_repo: InMemoryPatientSourceMappingRepository,
) -> _Wired:
    """Overrides on top of ``client``, whose teardown clears them."""
    w = _Wired(mock_repo, mock_mapping_repo)
    app.dependency_overrides[get_owner_timezone] = lambda: UTC
    app.dependency_overrides[get_external_calendar_events] = lambda: w.events
    app.dependency_overrides[get_appointment_repository] = lambda: w.appointments
    app.dependency_overrides[get_google_calendar_service] = lambda: w.calendar
    return w


def _window() -> dict[str, str]:
    now = utc_now()
    return {"start": now.isoformat(), "end": (now + timedelta(days=30)).isoformat()}


def test_open_sessions_are_listed_for_the_calendar(client: TestClient, wired: _Wired) -> None:
    wired.hold("e1", 2)
    wired.hold("far", 90)

    response = client.get("/api/calendar/outside-sessions", params=_window())

    assert response.status_code == 200, response.text
    [event] = response.json()["events"]
    assert event["id"] == "row-e1"
    assert event["source_identifier"] == SERIES_KEY


def test_questions_are_one_per_client_with_the_match_offered(
    client: TestClient, wired: _Wired
) -> None:
    wired.client_named("p1")
    wired.hold("e1", 2)
    wired.hold("e2", 9)

    body = client.get("/api/calendar/outside-sessions/questions").json()

    assert body["count"] == 1
    [question] = body["questions"]
    assert question["sessions"] == 2
    assert question["recurring"] is True
    # A name alone is offered preselected, never shown as settled.
    assert question["match"]["patient"] is None
    assert question["match"]["suggested_patient_id"] == "p1"
    assert [c["patient_id"] for c in question["match"]["possible"]] == ["p1"]


def test_confirming_a_client_books_each_session(client: TestClient, wired: _Wired) -> None:
    wired.client_named("p1")
    wired.hold("e1", 2)
    wired.hold("e2", 9)

    response = client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {
                    "source": GOOGLE_CALENDAR_SOURCE,
                    "source_identifier": SERIES_KEY,
                    "patient_id": "p1",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["answered"], body["appointments_created"]) == (1, 2)
    booked = {a["outside_session_id"]: a["appointment_id"] for a in body["appointments"]}
    appointment = wired.appointments.get(booked["row-e1"], USER_ID)
    assert appointment is not None
    assert appointment.outside_event_id == "e1"
    assert client.get("/api/calendar/outside-sessions/questions").json()["count"] == 0


def test_a_new_client_gets_a_chart_named_as_the_calendar_names_them(
    client: TestClient, wired: _Wired
) -> None:
    wired.hold("e1", 2)

    response = client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {
                    "source": GOOGLE_CALENDAR_SOURCE,
                    "source_identifier": SERIES_KEY,
                    "new_client_name": "Jane Smith",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    [booked] = response.json()["appointments"]
    appointment = wired.appointments._appointments[booked["appointment_id"]]
    patient = wired.patients.get(appointment.patient_id, USER_ID)
    assert patient is not None
    assert (patient.first_name, patient.origin) == ("Jane Smith", "calendar_follow")


def test_not_a_client_clears_the_question(client: TestClient, wired: _Wired) -> None:
    wired.hold("e1", 2)

    response = client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {
                    "source": GOOGLE_CALENDAR_SOURCE,
                    "source_identifier": SERIES_KEY,
                    "not_a_client": True,
                }
            ]
        },
    )

    assert response.json() == {
        "answered": 1,
        "appointments_created": 0,
        "appointments": [],
        "not_added": [],
    }
    assert wired.events.list_open(USER_ID) == []


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"patient_id": "p1", "not_a_client": True},
        {"patient_id": "p1", "new_client_name": "Jane"},
    ],
    ids=["none", "client-and-not", "two-clients"],
)
def test_an_answer_must_say_exactly_one_thing(
    client: TestClient, wired: _Wired, answer: dict[str, Any]
) -> None:
    response = client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {"source": GOOGLE_CALENDAR_SOURCE, "source_identifier": SERIES_KEY, **answer}
            ]
        },
    )

    assert response.status_code == 422


def test_an_unknown_client_is_refused_before_anything_is_written(
    client: TestClient, wired: _Wired
) -> None:
    wired.hold("e1", 2)

    response = client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {
                    "source": GOOGLE_CALENDAR_SOURCE,
                    "source_identifier": SERIES_KEY,
                    "patient_id": "nobody",
                }
            ]
        },
    )

    assert response.status_code == 404
    assert len(wired.events.list_open(USER_ID)) == 1


def _follow(client: TestClient, calendar_id: str | None) -> Any:
    return client.put("/api/google-calendar/followed-calendar", json={"calendar_id": calendar_id})


def _followed_appointment(wired: _Wired) -> Appointment:
    wired.client_named("p1")
    start = (utc_now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    return wired.appointments.create(
        Appointment(
            id="followed",
            user_id=USER_ID,
            patient_id="p1",
            title="Session",
            start_at=start,
            end_at=start + timedelta(minutes=50),
            duration_minutes=50,
            status=AppointmentStatus.CONFIRMED,
            session_type="individual",
            outside_source=GOOGLE_CALENDAR_SOURCE,
            outside_event_id="e1",
        )
    )


def test_a_followed_session_is_not_moved_in_pablo(client: TestClient, wired: _Wired) -> None:
    appointment = _followed_appointment(wired)
    later = appointment.start_at + timedelta(hours=3)

    response = client.patch(
        "/api/appointments/followed",
        json={"start_at": later.isoformat(), "end_at": (later + timedelta(minutes=50)).isoformat()},
    )

    assert response.status_code == 409
    assert wired.appointments.get("followed", USER_ID) == appointment


def test_editing_a_followed_session_puts_nothing_on_google(
    client: TestClient, wired: _Wired
) -> None:
    _followed_appointment(wired)

    response = client.patch("/api/appointments/followed", json={"notes": "Bring the worksheet"})

    assert response.status_code == 200, response.text
    assert response.json()["outside_event_id"] == "e1"
    wired.calendar.push_appointment.assert_not_called()
    wired.calendar.push_appointment_event.assert_not_called()


def _answer_series(client: TestClient, **answer: Any) -> Any:
    return client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {"source": GOOGLE_CALENDAR_SOURCE, "source_identifier": SERIES_KEY, **answer}
            ]
        },
    )


def test_a_session_already_booked_over_is_reported_not_added(
    client: TestClient, wired: _Wired
) -> None:
    wired.client_named("p1")
    wired.hold("e1", 2)
    wired.hold("e2", 9)
    [row] = [r for r in wired.events.list_open(USER_ID) if r.source_event_id == "e1"]
    wired.appointments.create(
        Appointment(
            id="booked-here",
            user_id=USER_ID,
            patient_id="p1",
            title="Session",
            start_at=row.start_at,
            end_at=row.end_at,
            duration_minutes=50,
            status=AppointmentStatus.CONFIRMED,
            session_type="individual",
        )
    )

    body = _answer_series(client, patient_id="p1").json()

    assert body["appointments_created"] == 1
    [skipped] = body["not_added"]
    assert skipped["outside_session_id"] == "row-e1"
    assert skipped["client_name"] == "Jane Smith"


def test_turning_following_off_drops_its_questions_and_hides_the_rest(
    client: TestClient, wired: _Wired
) -> None:
    wired.hold("e1", 2)

    _follow(client, None)
    wired.status["follow_calendar_id"] = None

    assert wired.events.list_open(USER_ID) == []
    # A question held while following was off (a read already under way)
    # stays out of sight.
    wired.hold("e2", 3)
    assert client.get("/api/calendar/outside-sessions", params=_window()).json() == {"events": []}
    assert client.get("/api/calendar/outside-sessions/questions").json()["count"] == 0


def test_turning_following_off_leaves_answered_sessions_alone(
    client: TestClient, wired: _Wired
) -> None:
    wired.client_named("p1")
    wired.hold("e1", 2)
    _answer_series(client, patient_id="p1")

    _follow(client, None)

    assert wired.appointments.get_by_outside_event(USER_ID, GOOGLE_CALENDAR_SOURCE, "e1")
    [row] = wired.events.list_by_source(USER_ID, GOOGLE_CALENDAR_SOURCE)
    assert row.answer == "client"


def test_a_remembered_slot_is_offered_preselected(client: TestClient, wired: _Wired) -> None:
    wired.client_named("p1")
    wired.hold("e1", 2, series=None)
    [row] = wired.events.list_open(USER_ID)
    local = row.start_at.astimezone(UTC)
    remember_match(
        GOOGLE_CALENDAR_SOURCE,
        calendar_source_identifier(None, row.title, local.weekday(), local.strftime("%H:%M")),
        "p1",
        MatchContext.for_practice(USER_ID, wired.patients, wired.mappings),
        scope=calendar_scope(MAIN),
    )

    [question] = client.get("/api/calendar/outside-sessions/questions").json()["questions"]

    assert question["match"]["patient"] is None
    assert question["match"]["suggested_patient_id"] == "p1"


TEAM = "team@group.calendar.google.test"


def test_a_refused_choice_writes_nothing(client: TestClient, wired: _Wired) -> None:
    kept = _followed_appointment(wired)
    kept.outside_calendar_id = None
    wired.appointments.update(kept)

    response = _follow(client, "someone-else@group.calendar.google.test")

    assert response.status_code == 400
    unchanged = wired.appointments.get(kept.id, USER_ID)
    assert unchanged is not None
    assert unchanged.outside_calendar_id is None


# --- Whose answer it is -----------------------------------------------------------

COLLEAGUE = "colleague-456"
SH = "sessions_health"
SH_ICAL = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:sh-1
DTSTART:20990105T150000Z
DTEND:20990105T155000Z
SUMMARY:SH00001
END:VEVENT
END:VCALENDAR"""


def _hold_feed_code(wired: _Wired, user_id: str) -> None:
    start = (utc_now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    wired.events.save(
        ExternalCalendarEvent(
            id=f"row-{user_id}",
            user_id=user_id,
            source=ical_source(SH),
            source_event_id="sh-1",
            start_at=start,
            end_at=start + timedelta(minutes=50),
            title="SH00001",
        )
    )


def _colleagues_feed(wired: _Wired) -> ICalSyncService:
    configs = MagicMock()
    configs.list_by_user.return_value = [
        ICalSyncConfig(
            user_id=COLLEAGUE,
            ehr_system=SH,
            encrypted_feed_url=encrypt_tokens({"feed_url": "https://x.test/sh"}),
            connected_at=utc_now(),
        )
    ]
    return ICalSyncService(
        config_repo=configs,
        appointment_repo=wired.appointments,
        patient_repo=wired.patients,
        mapping_repo=wired.mappings,
        external_events=wired.events,
    )


def test_a_feed_code_answered_here_books_a_colleagues_sessions(
    client: TestClient, wired: _Wired
) -> None:
    """The answer is the practice's: the colleague's next read books without asking."""
    wired.client_named("p1")
    wired.patients.grant_access("p1", COLLEAGUE)
    wired.appointments.grant_access("p1", COLLEAGUE)
    _hold_feed_code(wired, USER_ID)

    response = client.post(
        "/api/calendar/outside-sessions/answer",
        json={
            "answers": [
                {"source": ical_source(SH), "source_identifier": "SH00001", "patient_id": "p1"}
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["appointments_created"] == 1

    with patch.object(ICalSyncService, "_fetch_feed", return_value=SH_ICAL):
        [result] = _colleagues_feed(wired).sync(COLLEAGUE, SH)

    assert result.created == 1
    [booked] = wired.appointments.list_by_ical_source(COLLEAGUE, SH)
    assert (booked.patient_id, booked.user_id) == ("p1", COLLEAGUE)
    assert wired.events.list_open(COLLEAGUE) == []


def test_a_row_from_before_calendars_were_recorded_is_answered_under_the_main_one(
    client: TestClient, wired: _Wired
) -> None:
    """Nothing has learned the main calendar's id yet, so it is asked for once."""
    wired.calendar.known_main_calendar_id.return_value = None
    wired.client_named("p1")
    wired.hold("e1", 2)

    response = _answer_series(client, patient_id="p1")

    assert response.status_code == 200, response.text
    wired.calendar.list_readable_calendars.assert_called_once_with(USER_ID)
    [stored] = wired.mappings.list_by_source(calendar_scope(MAIN), GOOGLE_CALENDAR_SOURCE)
    assert stored.patient_id == "p1"
    row = wired.events.get(USER_ID, GOOGLE_CALENDAR_SOURCE, "e1")
    assert row is not None
    assert row.calendar_id == MAIN


def test_an_answer_is_refused_while_the_calendar_is_not_known(
    client: TestClient, wired: _Wired
) -> None:
    wired.calendar.known_main_calendar_id.return_value = None
    wired.calendar.list_readable_calendars.return_value = []
    wired.client_named("p1")
    wired.hold("e1", 2)

    response = _answer_series(client, patient_id="p1")

    assert response.status_code == 400
    assert response.json()["error"]["message"] == CALENDAR_NOT_KNOWN
    assert wired.mappings.list_by_source(calendar_scope(MAIN), GOOGLE_CALENDAR_SOURCE) == []
    assert len(wired.events.list_open(USER_ID)) == 1
