# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for reading a calendar once and proposing the practice it describes."""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Generator

    from app.repositories.patient import InMemoryPatientRepository
    from fastapi.testclient import TestClient

import pytest
from app.calendar_providers.capabilities import CalendarCapability
from app.calendar_providers.oauth_state import mint_state
from app.calendar_providers.practice_import import (
    ACTIVE_WITHIN_DAYS,
    Cadence,
    SeriesStatus,
    build_proposal,
)
from app.calendar_providers.provider import BusyWindow, ImportCandidate
from app.main import app
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.routes.patients import get_patient_repository
from app.routes.scheduling import get_google_calendar_service, get_scheduling_service
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services import get_audit_service
from app.services.google_calendar_service import (
    CalendarBusyNotAuthorizedError,
    CalendarImportNotAuthorizedError,
    GoogleCalendarService,
)
from app.services.token_encryption import derive_subkey
from app.settings import get_settings

# A fixed instant for the SCAN side, where every occurrence is deliberately in
# the past and is only ever compared against an explicitly passed `now=`.
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _ahead(days: int) -> datetime:
    """A start time genuinely in the future, on the real clock.

    The confirm route refuses an occurrence that has already passed, and it
    reads the wall clock to decide — there is no `now=` to pass it. Anchoring
    confirm fixtures to NOW instead made them expire: NOW + 3 days went by on
    2026-09-02 at 12:00 UTC and every confirm test failed from then on, on
    every branch. Offsets here are from the real clock, so they stay ahead of
    it.
    """
    return datetime.now(UTC) + timedelta(days=days)


# A distinctive stand-in for the kind of wording a therapist actually uses.
# Every "did this leak" assertion looks for exactly this string.
CLIENT_TITLE = "Zorbulax Quintwhistle weekly"


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _occurrence(
    start: datetime,
    *,
    summary: str = CLIENT_TITLE,
    minutes: int = 50,
    series_id: str | None = None,
    attendees: int = 0,
    event_id: str | None = None,
) -> ImportCandidate:
    return ImportCandidate(
        provider_event_id=event_id or f"evt-{start:%Y%m%d%H%M}",
        start=start,
        end=start + timedelta(minutes=minutes),
        summary=summary,
        attendee_count=attendees,
        series_id=series_id,
    )


def _weekly(
    count: int,
    *,
    first: datetime,
    step_days: int = 7,
    **kwargs: Any,
) -> list[ImportCandidate]:
    return [_occurrence(first + timedelta(days=step_days * i), **kwargs) for i in range(count)]


# Grouping and cadence


class TestProposalShape:
    """What a window of occurrences says about a practice."""

    def test_a_weekly_series_is_proposed_with_its_cadence_and_counts(self) -> None:
        occurrences = _weekly(8, first=NOW - timedelta(days=28))

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert len(proposal.series) == 1
        series = proposal.series[0]
        assert series.summary == CLIENT_TITLE
        assert series.cadence is Cadence.WEEKLY
        assert series.occurrences_in_window == 8
        # -28 through +21 by weeks: the one landing exactly on now is not ahead.
        assert series.occurrences_ahead == 3
        assert series.duration_minutes == 50
        assert series.recurrence_rule.startswith("RRULE:FREQ=WEEKLY")
        assert series.first_future_start is not None
        assert series.first_future_start > NOW

    def test_a_biweekly_series_is_recognised_as_biweekly(self) -> None:
        occurrences = _weekly(4, first=NOW - timedelta(days=28), step_days=14)

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert proposal.series[0].cadence is Cadence.BIWEEKLY
        assert "INTERVAL=2" in proposal.series[0].recurrence_rule

    def test_two_occurrences_and_one_offs_are_left_alone(self) -> None:
        """Twice is a coincidence. Three times is a pattern."""
        occurrences = [
            *_weekly(2, first=NOW - timedelta(days=7)),
            _occurrence(NOW - timedelta(days=3), summary="Dentist"),
            _occurrence(NOW - timedelta(days=1), summary="Lunch"),
        ]

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert proposal.series == ()
        assert proposal.left_alone == 4

    def test_a_provider_series_id_groups_across_a_changed_title(self) -> None:
        """Renaming an event mid-series must not split it in two."""
        occurrences = [
            *_weekly(3, first=NOW - timedelta(days=21), series_id="series-1"),
            *_weekly(3, first=NOW, series_id="series-1", summary="Renamed later"),
        ]

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert len(proposal.series) == 1
        assert proposal.series[0].occurrences_in_window == 6

    def test_a_missed_week_does_not_disqualify_a_series(self) -> None:
        starts = [NOW - timedelta(days=days) for days in (28, 21, 7, 0)]
        occurrences = [_occurrence(start) for start in starts]

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert proposal.series[0].cadence is Cadence.WEEKLY


# Staleness


class TestStaleness:
    """A former client proposed as a current one is the worst thing here."""

    def test_a_series_that_stopped_inside_the_window_is_not_active(self) -> None:
        """Three occurrences 90 days back is a finished client, not a live one."""
        occurrences = _weekly(6, first=NOW - timedelta(days=85))
        occurrences = [c for c in occurrences if c.start < NOW - timedelta(days=ACTIVE_WITHIN_DAYS)]

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert proposal.series[0].status is SeriesStatus.LOOKS_FINISHED
        assert proposal.series[0].preselected is False

    def test_a_rule_that_has_run_out_marks_a_series_finished(self) -> None:
        """The therapist's own rule beats counting occurrences."""
        occurrences = _weekly(5, first=NOW - timedelta(days=14), series_id="series-1")

        proposal = build_proposal(
            occurrences,
            now=NOW,
            timezone="UTC",
            series_recurrence={"series-1": ["RRULE:FREQ=WEEKLY;UNTIL=20260701T000000Z"]},
        )

        assert proposal.series[0].status is SeriesStatus.LOOKS_FINISHED
        assert proposal.series[0].preselected is False

    def test_recent_activity_keeps_a_series_active(self) -> None:
        occurrences = _weekly(4, first=NOW - timedelta(days=21))

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert proposal.series[0].status is SeriesStatus.ACTIVE


# Structural scoring


class TestConfidence:
    """Shape, not content: nothing here reads what the event says."""

    def test_a_session_shaped_series_outscores_a_meeting_shaped_one(self) -> None:
        occurrences = [
            *_weekly(6, first=NOW - timedelta(days=21), summary="Client hour", minutes=50),
            *_weekly(
                6,
                first=NOW - timedelta(days=21, hours=3),
                summary="Team standup",
                minutes=30,
                attendees=7,
            ),
        ]

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        by_summary = {series.summary: series for series in proposal.series}
        assert by_summary["Client hour"].confidence > by_summary["Team standup"].confidence

    def test_the_list_is_ordered_most_confident_first(self) -> None:
        occurrences = [
            *_weekly(6, first=NOW - timedelta(days=21), summary="Client hour", minutes=50),
            *_weekly(
                6,
                first=NOW - timedelta(days=21, hours=3),
                summary="Team standup",
                minutes=30,
                attendees=7,
            ),
        ]

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        scores = [series.confidence for series in proposal.series]
        assert scores == sorted(scores, reverse=True)

    def test_a_low_scoring_series_is_still_proposed(self) -> None:
        """The score ranks. It never hides a cadence-qualifying candidate."""
        occurrences = _weekly(
            6,
            first=NOW.replace(hour=22) - timedelta(days=21),
            summary="Evening class",
            minutes=120,
            attendees=15,
        )

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC")

        assert len(proposal.series) == 1
        assert proposal.series[0].confidence < 0.6
        assert proposal.series[0].preselected is False


class TestCaps:
    def test_more_series_than_the_cap_is_flagged_partial(self) -> None:
        occurrences: list[ImportCandidate] = []
        for index in range(5):
            occurrences += _weekly(
                3,
                first=NOW - timedelta(days=14, hours=index),
                summary=f"Client {index}",
            )

        proposal = build_proposal(occurrences, now=NOW, timezone="UTC", max_series=2)

        assert len(proposal.series) == 2
        assert proposal.partial is True

    def test_a_truncated_read_is_flagged_partial(self) -> None:
        proposal = build_proposal(
            _weekly(3, first=NOW - timedelta(days=14)),
            now=NOW,
            timezone="UTC",
            truncated=True,
        )

        assert proposal.partial is True

    def test_a_complete_read_is_not_flagged_partial(self) -> None:
        proposal = build_proposal(
            _weekly(3, first=NOW - timedelta(days=14)),
            now=NOW,
            timezone="UTC",
        )

        assert proposal.partial is False


# Reading the calendar


class _FakeEvents:
    """Replays pages of an events().list, and answers events().get."""

    def __init__(self, pages: list[dict[str, Any]], masters: dict[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.masters = masters
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    def list(self, **kwargs: Any) -> Any:
        self.list_calls.append(dict(kwargs))
        return _FakeRequest(self.pages[len(self.list_calls) - 1])

    def get(self, **kwargs: Any) -> Any:
        self.get_calls.append(kwargs["eventId"])
        return _FakeRequest(self.masters.get(kwargs["eventId"], {}))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def execute(self) -> dict[str, Any]:
        return self._payload


class _FakeCalendarService:
    def __init__(self, pages: list[dict[str, Any]], masters: dict[str, dict[str, Any]]) -> None:
        self.events_resource = _FakeEvents(pages, masters)

    def events(self) -> _FakeEvents:
        return self.events_resource


def _google_event(start: datetime, *, summary: str, series_id: str | None = None) -> dict[str, Any]:
    return {
        "id": f"evt-{start:%Y%m%d%H%M}",
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
        **({"recurringEventId": series_id} if series_id else {}),
    }


@pytest.fixture
def token_repo() -> MagicMock:
    repo = MagicMock()
    repo.get.return_value = GoogleCalendarTokenDoc(
        user_id="user-001",
        encrypted_tokens="encrypted",
        calendar_id="primary@gmail.com",
        granted_capabilities="push,import",
    )
    return repo


@pytest.fixture
def calendar_service(token_repo: MagicMock) -> GoogleCalendarService:
    return GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
    )


def _run_scan(
    calendar_service: GoogleCalendarService,
    pages: list[dict[str, Any]],
    masters: dict[str, dict[str, Any]] | None = None,
) -> tuple[Any, _FakeCalendarService]:
    fake = _FakeCalendarService(pages, masters or {})
    creds = MagicMock(expired=False)
    with (
        patch("app.services.google_calendar_service.decrypt_tokens", return_value={}),
        patch("app.services.google_calendar_service._make_credentials", return_value=creds),
        patch("app.services.google_calendar_service._build_calendar_service", return_value=fake),
        patch("app.services.google_calendar_service._now", return_value=NOW),
    ):
        proposal = calendar_service.scan_for_practice_import("user-001")
    return proposal, fake


class TestScan:
    def test_a_series_split_across_pages_is_read_whole(
        self,
        calendar_service: GoogleCalendarService,
    ) -> None:
        """Stopping at page one would report a weekly client as a one-off."""
        early = [
            _google_event(NOW - timedelta(days=days), summary=CLIENT_TITLE) for days in (28, 21)
        ]
        late = [_google_event(NOW - timedelta(days=days), summary=CLIENT_TITLE) for days in (14, 7)]
        pages = [
            {"items": early, "nextPageToken": "page-2"},
            {"items": late},
        ]

        proposal, fake = _run_scan(calendar_service, pages)

        assert len(fake.events_resource.list_calls) == 2
        assert fake.events_resource.list_calls[1]["pageToken"] == "page-2"
        assert len(proposal.series) == 1
        assert proposal.series[0].occurrences_in_window == 4

    def test_the_window_looks_back_and_ahead_by_default(
        self,
        calendar_service: GoogleCalendarService,
    ) -> None:
        proposal, fake = _run_scan(calendar_service, [{"items": []}])

        call = fake.events_resource.list_calls[0]
        assert call["singleEvents"] is True
        window_start = datetime.fromisoformat(call["timeMin"])
        window_end = datetime.fromisoformat(call["timeMax"])
        assert (NOW - window_start).days == 90
        assert (window_end - NOW).days == 90
        assert proposal.lookback_days == 90
        assert proposal.horizon_days == 90

    def test_a_series_rule_is_read_from_its_own_event(
        self,
        calendar_service: GoogleCalendarService,
    ) -> None:
        events = [
            _google_event(NOW - timedelta(days=days), summary=CLIENT_TITLE, series_id="series-1")
            for days in (21, 14, 7)
        ]
        masters = {"series-1": {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"]}}

        proposal, fake = _run_scan(calendar_service, [{"items": events}], masters)

        assert fake.events_resource.get_calls == ["series-1"]
        assert proposal.series[0].recurrence_rule == "RRULE:FREQ=WEEKLY;BYDAY=MO"

    def test_all_day_events_are_not_sessions(
        self,
        calendar_service: GoogleCalendarService,
    ) -> None:
        pages = [
            {
                "items": [
                    {"id": "holiday", "summary": "Vacation", "start": {"date": "2026-08-20"}},
                ]
            }
        ]

        proposal, _ = _run_scan(calendar_service, pages)

        assert proposal.series == ()
        assert proposal.events_read == 0

    def test_scanning_without_the_grant_is_refused_before_any_read(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """The first scan on a fresh connection lands here by design."""
        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="primary@gmail.com",
            granted_capabilities="push,busy",
        )

        with patch("app.services.google_calendar_service._build_calendar_service") as build:
            with pytest.raises(CalendarImportNotAuthorizedError):
                calendar_service.scan_for_practice_import("user-001")
            build.assert_not_called()

    def test_reading_the_calendar_never_logs_what_it_says(
        self,
        calendar_service: GoogleCalendarService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """HIPAA: a scan reports counts. Titles go to the therapist alone."""
        events = [
            _google_event(NOW - timedelta(days=days), summary=CLIENT_TITLE) for days in (21, 14, 7)
        ]

        with caplog.at_level(logging.DEBUG, logger="app.services.google_calendar_service"):
            proposal, _ = _run_scan(calendar_service, [{"items": events}])

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert CLIENT_TITLE not in logged
        assert "Zorbulax" not in logged
        # The proposal still carries it, for the one person entitled to read it.
        assert proposal.series[0].summary == CLIENT_TITLE

    def test_an_incremental_grant_is_recorded_alongside_what_was_held(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """Granting event read later must not drop the write grant."""
        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="primary@gmail.com",
            granted_capabilities="push,busy",
        )
        fake = _FakeCalendarService([], {})
        fake.events_resource.masters = {}
        with (
            patch("app.services.google_calendar_service._build_flow") as build_flow,
            patch(
                "app.services.google_calendar_service._build_calendar_service",
                return_value=MagicMock(),
            ),
        ):
            build_flow.return_value.credentials = MagicMock(
                token=None, refresh_token=None, token_uri=None, client_id=None, client_secret=None
            )
            calendar_service.handle_callback(
                "user-001",
                "auth-code",
                "http://localhost/callback",
                state=mint_state(derive_subkey("google-calendar-oauth-state"), "user-001"),
                capabilities=[CalendarCapability.IMPORT],
            )

        saved = token_repo.save.call_args[0][0]
        assert set(saved.granted_capabilities.split(",")) == {"busy", "import", "push"}

    def test_asking_for_event_read_alone_asks_google_to_keep_earlier_grants(
        self,
        calendar_service: GoogleCalendarService,
    ) -> None:
        with patch("app.services.google_calendar_service._build_flow") as build_flow:
            build_flow.return_value.authorization_url.return_value = ("https://url", "state")
            calendar_service.get_auth_url(
                "user-001",
                "http://localhost/callback",
                capabilities=[CalendarCapability.IMPORT],
            )

        assert (
            build_flow.return_value.authorization_url.call_args.kwargs["include_granted_scopes"]
            == "true"
        )

    def test_connecting_replaces_the_grant_rather_than_adding_to_it(
        self,
        calendar_service: GoogleCalendarService,
    ) -> None:
        """A therapist narrowing their choice at connect must not be
        silently kept on the wider grant they had before."""
        with patch("app.services.google_calendar_service._build_flow") as build_flow:
            build_flow.return_value.authorization_url.return_value = ("https://url", "state")
            calendar_service.get_auth_url("user-001", "http://localhost/callback")

        assert (
            build_flow.return_value.authorization_url.call_args.kwargs["include_granted_scopes"]
            == "false"
        )


# Free/busy — the anonymous week grid's data source


class _FakeFreeBusy:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def query(self, body: dict[str, Any]) -> _FakeRequest:
        self.calls.append(body)
        return _FakeRequest(self.payload)


class _FakeFreeBusyCalendarService:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.freebusy_resource = _FakeFreeBusy(payload)

    def freebusy(self) -> _FakeFreeBusy:
        return self.freebusy_resource


def _run_busy(
    calendar_service: GoogleCalendarService,
    payload: dict[str, Any],
    *,
    start: datetime = NOW,
    end: datetime = NOW + timedelta(days=7),
) -> tuple[list[BusyWindow], _FakeFreeBusyCalendarService]:
    fake = _FakeFreeBusyCalendarService(payload)
    creds = MagicMock(expired=False)
    with (
        patch("app.services.google_calendar_service.decrypt_tokens", return_value={}),
        patch("app.services.google_calendar_service._make_credentials", return_value=creds),
        patch("app.services.google_calendar_service._build_calendar_service", return_value=fake),
    ):
        windows = calendar_service.list_busy_windows("user-001", start, end)
    return windows, fake


class TestBusyWindows:
    def test_busy_blocks_come_back_as_start_and_end_only(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="primary@gmail.com",
            granted_capabilities="push,busy",
        )
        payload = {
            "calendars": {
                "primary": {
                    "busy": [
                        {"start": "2026-09-01T14:00:00Z", "end": "2026-09-01T15:00:00Z"},
                        {"start": "2026-09-02T09:00:00Z", "end": "2026-09-02T09:30:00Z"},
                    ]
                }
            }
        }

        windows, fake = _run_busy(calendar_service, payload)

        assert len(windows) == 2
        assert all(isinstance(w, BusyWindow) for w in windows)
        assert {f.name for f in fields(BusyWindow)} == {"start", "end"}
        assert windows[0].start == datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
        assert windows[0].end == datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
        # Queried the therapist's own calendar, not wherever PUSH writes to.
        assert fake.freebusy_resource.calls[0]["items"] == [{"id": "primary"}]

    def test_an_empty_calendar_reports_no_busy_time(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="primary@gmail.com",
            granted_capabilities="push,busy",
        )

        windows, _ = _run_busy(calendar_service, {"calendars": {"primary": {"busy": []}}})

        assert windows == []

    def test_reading_busy_time_without_the_grant_is_refused_before_any_read(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """Declining "Also check when I'm busy" at connect lands here — not
        a failure, the caller falls back to the scan-only grid."""
        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="primary@gmail.com",
            granted_capabilities="push,import",
        )

        with patch("app.services.google_calendar_service._build_calendar_service") as build:
            with pytest.raises(CalendarBusyNotAuthorizedError):
                calendar_service.list_busy_windows("user-001", NOW, NOW + timedelta(days=7))
            build.assert_not_called()

    def test_a_connection_predating_the_busy_choice_is_also_refused(
        self,
        calendar_service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        """The pre-capability default (`push,import`) never included busy."""
        token_repo.get.return_value = GoogleCalendarTokenDoc(
            user_id="user-001",
            encrypted_tokens="encrypted",
            calendar_id="primary@gmail.com",
        )

        with pytest.raises(CalendarBusyNotAuthorizedError):
            calendar_service.list_busy_windows("user-001", NOW, NOW + timedelta(days=7))


# Routes

_REDIRECT = "http://localhost:3000/dashboard/settings/calendar"
_USER = "test-user-123"


@pytest.fixture
def appt_repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def audit_spy() -> MagicMock:
    spy = MagicMock()
    app.dependency_overrides[get_audit_service] = lambda: spy
    return spy


@pytest.fixture
def import_client(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    appt_repo: InMemoryAppointmentRepository,
) -> TestClient:
    """The shared client, wired to the real scheduling service over
    in-memory repos so a confirmation writes something a test can read back."""
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo)
    app.dependency_overrides[get_patient_repository] = lambda: mock_repo
    return client


def _patients(repo: InMemoryPatientRepository) -> list[Any]:
    return repo.list_by_user(_USER, page=1, page_size=100)[0]


def _appointments(repo: InMemoryAppointmentRepository) -> list[Any]:
    return repo.list_by_range(_USER, NOW - timedelta(days=400), NOW + timedelta(days=400))


class TestScanRoute:
    """The scan surface: propose, and write nothing."""

    def test_a_scan_returns_the_proposal_and_persists_nothing(
        self,
        import_client: TestClient,
        mock_repo: InMemoryPatientRepository,
        appt_repo: InMemoryAppointmentRepository,
    ) -> None:
        """Proposing is not ingesting. A therapist who walks away leaves
        nothing behind — no patient, no appointment, no staged row."""
        gcal = MagicMock()
        gcal.scan_for_practice_import.return_value = build_proposal(
            _weekly(4, first=NOW - timedelta(days=21)),
            now=NOW,
            timezone="UTC",
        )
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        response = import_client.post(
            "/api/calendar/import/scan", params={"redirect_uri": _REDIRECT}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["series"]) == 1
        assert body["series"][0]["summary"] == CLIENT_TITLE
        assert body["partial"] is False
        assert _patients(mock_repo) == []
        assert _appointments(appt_repo) == []

    def test_a_scan_without_the_grant_asks_for_it(self, client: TestClient) -> None:
        """The consent prompt is the expected first answer, not a failure."""
        gcal = MagicMock()
        gcal.scan_for_practice_import.side_effect = CalendarImportNotAuthorizedError("google")
        gcal.get_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth?scope=x"
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        response = client.post("/api/calendar/import/scan", params={"redirect_uri": _REDIRECT})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["needs_consent"] is True
        assert body["capability"] == "import"
        assert body["auth_url"].startswith("https://accounts.google.com/")
        assert gcal.get_auth_url.call_args.kwargs["capabilities"] == [CalendarCapability.IMPORT]

    def test_a_scan_audits_counts_and_never_the_titles(
        self,
        client: TestClient,
        audit_spy: MagicMock,
    ) -> None:
        gcal = MagicMock()
        gcal.scan_for_practice_import.return_value = build_proposal(
            _weekly(4, first=NOW - timedelta(days=21)),
            now=NOW,
            timezone="UTC",
        )
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        client.post("/api/calendar/import/scan", params={"redirect_uri": _REDIRECT})

        recorded = str(audit_spy.log.call_args.kwargs["changes"])
        assert CLIENT_TITLE not in recorded
        assert "events_read" in recorded

    def test_an_unknown_redirect_is_refused(self, client: TestClient) -> None:
        gcal = MagicMock()
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        response = client.post(
            "/api/calendar/import/scan",
            params={"redirect_uri": "https://not-ours.example.com/callback"},
        )

        assert response.status_code == 400, response.text


class TestBusyWindowsRoute:
    """The pre-scan week grid's data source: intervals only, never a title."""

    def test_busy_windows_carry_only_start_and_end(self, client: TestClient) -> None:
        gcal = MagicMock()
        gcal.list_busy_windows.return_value = [
            BusyWindow(
                start=datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
                end=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
            )
        ]
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        response = client.get(
            "/api/calendar/import/busy",
            params={
                "start": NOW.isoformat(),
                "end": (NOW + timedelta(days=7)).isoformat(),
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["windows"] == [{"start": "2026-09-01T14:00:00Z", "end": "2026-09-01T15:00:00Z"}]
        # The response shape has no field a summary could ever land in.
        assert set(body["windows"][0].keys()) == {"start", "end"}
        assert "summary" not in body["windows"][0]

    def test_busy_windows_without_the_grant_return_a_typed_not_granted_response(
        self, client: TestClient
    ) -> None:
        """Declining the busy checkbox at connect is not a 500."""
        gcal = MagicMock()
        gcal.list_busy_windows.side_effect = CalendarBusyNotAuthorizedError("google")
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        response = client.get(
            "/api/calendar/import/busy",
            params={
                "start": NOW.isoformat(),
                "end": (NOW + timedelta(days=7)).isoformat(),
            },
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"granted": False}

    def test_an_inverted_window_is_refused(self, client: TestClient) -> None:
        gcal = MagicMock()
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        response = client.get(
            "/api/calendar/import/busy",
            params={
                "start": NOW.isoformat(),
                "end": (NOW - timedelta(days=1)).isoformat(),
            },
        )

        assert response.status_code == 400, response.text


def _confirm_item(**overrides: Any) -> dict[str, Any]:
    item = {
        "candidate_key": "key-1",
        "display_name": CLIENT_TITLE,
        "start_at": _ahead(3).isoformat(),
        "duration_minutes": 50,
        "cadence": "weekly",
        "occurrences": 4,
        "timezone": "UTC",
    }
    item.update(overrides)
    return item


class TestConfirmRoute:
    def test_confirming_a_subset_creates_only_that_subset(
        self,
        import_client: TestClient,
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        response = import_client.post(
            "/api/calendar/import/confirm",
            json={
                "series": [
                    _confirm_item(candidate_key="key-1", display_name="First client"),
                    _confirm_item(
                        candidate_key="key-2",
                        display_name="Second client",
                        start_at=_ahead(4).isoformat(),
                    ),
                ]
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["patients_created"] == 2
        assert body["appointments_created"] == 8
        assert {row["candidate_key"] for row in body["confirmed"]} == {"key-1", "key-2"}

        patients = _patients(mock_repo)
        assert len(patients) == 2
        # The calendar's own wording becomes the initial name, unparsed.
        assert {p.first_name for p in patients} == {"First client", "Second client"}
        assert all(p.origin == "calendar_import" for p in patients)

    def test_a_series_omitted_from_the_confirmation_creates_nothing(
        self,
        import_client: TestClient,
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        import_client.post(
            "/api/calendar/import/confirm",
            json={"series": [_confirm_item(display_name="Only this one")]},
        )

        patients = _patients(mock_repo)
        assert len(patients) == 1
        assert patients[0].first_name == "Only this one"

    def test_a_past_occurrence_is_refused(
        self,
        import_client: TestClient,
        mock_repo: InMemoryPatientRepository,
    ) -> None:
        """The past supplies the pattern. Only what is ahead becomes a record."""
        response = import_client.post(
            "/api/calendar/import/confirm",
            json={"series": [_confirm_item(start_at=_ahead(-7).isoformat())]},
        )

        assert response.status_code == 400, response.text
        assert _patients(mock_repo) == []

    def test_created_appointments_all_land_in_the_future(
        self,
        import_client: TestClient,
        appt_repo: InMemoryAppointmentRepository,
    ) -> None:
        import_client.post(
            "/api/calendar/import/confirm",
            json={"series": [_confirm_item(occurrences=6)]},
        )

        created = _appointments(appt_repo)
        assert len(created) == 6
        assert all(appt.start_at > datetime.now(UTC) for appt in created)


def test_nothing_on_the_import_path_can_send_an_event_title_anywhere() -> None:
    """Structural scoring exists so no title has to leave the process.

    A model client, an HTTP client or a queue publish appearing on this
    path would undo that in one line, so the path is checked rather than
    trusted.
    """
    app_root = Path(__file__).resolve().parents[1] / "app"
    import_path = (
        app_root / "calendar_providers" / "practice_import.py",
        app_root / "routes" / "calendar_import.py",
    )
    forbidden = (
        "anthropic",
        "openai",
        "google.genai",
        "structured_llm_gateway",
        "chat_llm_gateway",
        "httpx",
        "requests.post",
        "publish",
        "PublisherClient",
    )

    for path in import_path:
        source = path.read_text()
        found = [name for name in forbidden if name in source]
        assert found == [], f"{path.name} reaches for {found}"
