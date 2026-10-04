# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Importing a calendar's series, then following the same calendar.

The import makes Pablo's own recurring appointments for a series it read.
They are not linked to the calendar's events. Following that calendar then
reads the same events again. These tests record what that does, through the
real import routes and the real follower, so the wizard can be built on what
happens rather than on what was assumed.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.calendar_providers.practice_import import build_proposal
from app.calendar_providers.provider import ImportCandidate
from app.calendar_providers.source_identity import GOOGLE_CALENDAR_SOURCE
from app.main import app
from app.models.patient import Patient
from app.repositories.external_calendar_event import InMemoryExternalCalendarEventRepository
from app.routes.outside_sessions import get_external_calendar_events
from app.routes.scheduling import (
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

    from app.repositories.patient import InMemoryPatientRepository
    from app.repositories.patient_source_mapping import InMemoryPatientSourceMappingRepository
    from fastapi.testclient import TestClient

ME = "test-user-123"
MAIN = "clinician@example.test"
SERIES = "rec-1"
REDIRECT = "http://localhost:3000/dashboard/settings/calendar"


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patient(patient_id: str, first: str, last: str) -> Patient:
    now = utc_now()
    return Patient(id=patient_id, first_name=first, last_name=last, created_at=now, updated_at=now)


def _next_slot() -> datetime:
    """A weekday afternoon three to nine days out, on the hour."""
    start = (datetime.now(UTC) + timedelta(days=3)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    while start.weekday() >= 5:
        start += timedelta(days=1)
    return start


class _Stack:
    """The pieces one clinician's import and follow share."""

    def __init__(
        self,
        client: TestClient,
        patients: InMemoryPatientRepository,
        mappings: InMemoryPatientSourceMappingRepository,
    ) -> None:
        self.client = client
        self.patients = patients
        self.mappings = mappings
        self.appointments = InMemoryAppointmentRepository()
        self.events = InMemoryExternalCalendarEventRepository()
        self.gcal = MagicMock()
        self.gcal.known_main_calendar_id.return_value = MAIN
        self.gcal.main_calendar_id.return_value = MAIN
        self.gcal.get_sync_status.return_value = {
            "connected": True,
            "import_granted": True,
            "follow_calendar_id": MAIN,
        }
        app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(
            self.appointments
        )
        app.dependency_overrides[get_appointment_repository] = lambda: self.appointments
        app.dependency_overrides[get_external_calendar_events] = lambda: self.events
        app.dependency_overrides[get_owner_timezone] = lambda: UTC
        app.dependency_overrides[get_google_calendar_service] = lambda: self.gcal

    def occurrences(self, first_ahead: datetime, title: str) -> list[ImportCandidate]:
        """Three weeks behind and two ahead of one weekly series."""
        return [
            ImportCandidate(
                provider_event_id=f"evt-{week}",
                start=first_ahead + timedelta(days=7 * week),
                end=first_ahead + timedelta(days=7 * week, minutes=50),
                summary=title,
                attendee_count=0,
                series_id=SERIES,
            )
            for week in range(-3, 2)
        ]

    def scan_and_confirm(self, first_ahead: datetime, title: str, patient_id: str) -> None:
        self.gcal.scan_for_practice_import.return_value = build_proposal(
            self.occurrences(first_ahead, title), now=datetime.now(UTC), timezone="UTC"
        )
        scanned = self.client.post(
            "/api/calendar/import/scan", params={"redirect_uri": REDIRECT, "timezone": "UTC"}
        )
        assert scanned.status_code == 200, scanned.text
        [series] = scanned.json()["series"]
        confirmed = self.client.post(
            "/api/calendar/import/confirm",
            json={
                "series": [
                    {
                        "candidate_key": series["candidate_key"],
                        "display_name": series["summary"],
                        "patient_id": patient_id,
                        "source_identifier": series["source_identifier"],
                        "start_at": series["first_future_start"],
                        "duration_minutes": series["duration_minutes"],
                        "cadence": series["cadence"],
                        "occurrences": max(series["occurrences_ahead"], 1),
                        "timezone": "UTC",
                    }
                ]
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["appointments_created"] == 2, confirmed.text

    def follow(self, first_ahead: datetime, title: str) -> Any:
        """What the follower does with one read of the followed calendar."""
        outside = OutsideSessions(
            self.events, self.appointments, self.patients, self.mappings, main_calendar_id=MAIN
        )
        return outside.ingest_google(
            ME,
            [
                {
                    "google_event_id": c.provider_event_id,
                    "status": "confirmed",
                    "summary": c.summary,
                    "series_id": c.series_id,
                    "start": {"dateTime": c.start.isoformat()},
                    "end": {"dateTime": c.end.isoformat()},
                }
                for c in self.occurrences(first_ahead, title)
                if c.start > datetime.now(UTC)
            ],
            calendar_id=MAIN,
        )

    def upcoming(self) -> list[Any]:
        now = utc_now()
        return [
            a
            for a in self.appointments.list_by_range(ME, now, now + timedelta(days=60))
            if a.status != "cancelled"
        ]


@pytest.fixture
def stack(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_mapping_repo: InMemoryPatientSourceMappingRepository,
) -> _Stack:
    return _Stack(client, mock_repo, mock_mapping_repo)


class TestImportThenFollowTheSameCalendar:
    def test_a_generic_title_asks_again_and_the_answer_adds_nothing(self, stack: _Stack) -> None:
        """The clash: the imported series comes back as a question, and
        answering it reports every session as overlapping."""
        stack.patients.create(_patient("jane", "Jane", "Adams"), ME)
        first = _next_slot()
        stack.scan_and_confirm(first, "Weekly 1:1", "jane")
        imported = stack.upcoming()
        assert len(imported) == 2
        assert all(a.outside_event_id is None for a in imported)

        result = stack.follow(first, "Weekly 1:1")

        # Not booked on its own: the import remembered the series without
        # the title it was answered under, so the follower asks.
        assert result.booked == []
        questions = stack.client.get("/api/calendar/outside-sessions/questions")
        assert questions.status_code == 200, questions.text
        [question] = questions.json()["questions"]
        assert question["sessions"] == 2

        answered = stack.client.post(
            "/api/calendar/outside-sessions/answer",
            json={
                "answers": [
                    {
                        "source": GOOGLE_CALENDAR_SOURCE,
                        "source_identifier": question["source_identifier"],
                        "patient_id": "jane",
                    }
                ]
            },
        )
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["appointments_created"] == 0
        assert len(body["not_added"]) == 2
        # The import's appointments stand, still not linked to the events.
        after = stack.upcoming()
        assert {a.id for a in after} == {a.id for a in imported}
        assert all(a.outside_event_id is None for a in after)

    def test_a_named_title_is_skipped_silently_and_never_linked(self, stack: _Stack) -> None:
        """No question this time, but the events still never reach the
        appointments the import made: a moved event would not move them."""
        stack.patients.create(_patient("jane", "Jane", "Adams"), ME)
        first = _next_slot()
        stack.scan_and_confirm(first, "Jane Adams", "jane")
        imported = stack.upcoming()

        result = stack.follow(first, "Jane Adams")

        assert result.booked == []
        assert (
            stack.client.get("/api/calendar/outside-sessions/questions").json()["questions"] == []
        )
        rows = stack.events.list_by_source(ME, GOOGLE_CALENDAR_SOURCE)
        assert len(rows) == 2
        assert all(row.appointment_id is None for row in rows)
        assert {a.id for a in stack.upcoming()} == {a.id for a in imported}

    @pytest.mark.parametrize("write_target", ["app_calendar", "primary"])
    def test_the_import_writes_nothing_to_google(self, stack: _Stack, write_target: str) -> None:
        """Whichever calendar Pablo writes its sessions to, the import adds no
        copy there: Google still holds only the originals."""
        stack.gcal.get_sync_status.return_value = {
            "connected": True,
            "import_granted": True,
            "write_target": write_target,
            "follow_calendar_id": None,
        }
        stack.patients.create(_patient("jane", "Jane", "Adams"), ME)
        stack.scan_and_confirm(_next_slot(), "Weekly 1:1", "jane")

        stack.gcal.push_appointment.assert_not_called()
        stack.gcal.push_appointment_event.assert_not_called()
        assert all(a.google_event_id is None for a in stack.upcoming())

    def test_editing_an_imported_session_writes_a_second_copy(self, stack: _Stack) -> None:
        """The import leaves Pablo's session unlinked from the original, so
        the first edit pushes it to Google as a new event beside it."""
        stack.patients.create(_patient("jane", "Jane", "Adams"), ME)
        stack.scan_and_confirm(_next_slot(), "Weekly 1:1", "jane")
        stack.gcal.push_appointment.return_value = "pablo-copy-1"
        first = min(stack.upcoming(), key=lambda a: a.start_at)

        edited = stack.client.patch(f"/api/appointments/{first.id}", json={"notes": "Room 2"})

        assert edited.status_code == 200, edited.text
        stack.gcal.push_appointment.assert_called_once()
        assert stack.appointments.get(first.id, ME).google_event_id == "pablo-copy-1"

    def test_following_alone_books_and_links_every_session(self, stack: _Stack) -> None:
        """Without the import, the same calendar's series is followed: each
        session is an appointment linked to its event."""
        stack.patients.create(_patient("jane", "Jane", "Adams"), ME)
        first = _next_slot()

        result = stack.follow(first, "Jane Adams")

        assert len(result.booked) == 2
        assert {a.outside_event_id for a in stack.upcoming()} == {"evt-0", "evt-1"}
