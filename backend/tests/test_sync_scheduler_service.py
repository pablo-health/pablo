# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the SyncSchedulerService (periodic calendar sync orchestrator)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.models import User
from app.models.user import UserPreferences
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.repositories.ical_sync_config import ICalSyncConfig
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.models.availability import AvailabilityRule, RuleType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.audit_service import AuditService
from app.services.google_calendar_follow import GoogleChangeFollower
from app.services.google_calendar_service import CalendarGoneError, PushedEvent
from app.services.sync_scheduler_service import (
    SyncSchedulerService,
    _is_within_working_hours,
    _read_window,
)
from app.settings import get_settings
from app.utcnow import utc_now
from google.auth.exceptions import RefreshError

if TYPE_CHECKING:
    import pytest

# Fixtures


def _make_service(
    ical_configs: list[ICalSyncConfig] | None = None,
    google_tokens: list[GoogleCalendarTokenDoc] | None = None,
    user_prefs: UserPreferences | None = None,
) -> SyncSchedulerService:
    """Build a SyncSchedulerService with mocked dependencies."""
    ical_config_repo = MagicMock()
    ical_config_repo.list_all.return_value = ical_configs or []

    google_token_repo = MagicMock()
    google_token_repo.list_all.return_value = google_tokens or []
    google_token_repo.get.return_value = None

    user_repo = MagicMock()
    user_repo.get_preferences.return_value = user_prefs or UserPreferences()

    ical_sync_service = MagicMock()
    ical_sync_service.sync.return_value = []

    google_calendar_service = MagicMock()
    google_calendar_service.sync_from_google.return_value = []

    reminder_service = MagicMock()
    reminder_service.check_and_send_reminders.return_value = {"24h_sent": 0, "1h_sent": 0}

    appointment_repo = MagicMock()
    appointment_repo.get_by_google_event_id.return_value = None

    availability_rule_repo = MagicMock()
    availability_rule_repo.list_by_user.return_value = []

    return SyncSchedulerService(
        ical_config_repo=ical_config_repo,
        google_token_repo=google_token_repo,
        user_repo=user_repo,
        ical_sync_service=ical_sync_service,
        google_calendar_service=google_calendar_service,
        reminder_service=reminder_service,
        appointment_repo=appointment_repo,
        availability_rule_repo=availability_rule_repo,
    )


def _make_ical_config(
    user_id: str = "user1",
    ehr_system: str = "simplepractice",
    consecutive_error_count: int = 0,
) -> ICalSyncConfig:
    return ICalSyncConfig(
        user_id=user_id,
        ehr_system=ehr_system,
        encrypted_feed_url="encrypted_url",
        connected_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        consecutive_error_count=consecutive_error_count,
    )


def _make_google_token(
    user_id: str = "user1",
    consecutive_error_count: int = 0,
) -> GoogleCalendarTokenDoc:
    return GoogleCalendarTokenDoc(
        user_id=user_id,
        encrypted_tokens="encrypted_tokens",
        calendar_id="primary",
        connected_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        consecutive_error_count=consecutive_error_count,
    )


# _is_within_working_hours tests


_EASTERN = ZoneInfo("America/New_York")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 5, hour, minute, tzinfo=_EASTERN)


def _hours(day: int, start: str, end: str) -> AvailabilityRule:
    return AvailabilityRule(
        id=f"wh-{day}-{start}",
        user_id="user1",
        rule_type=RuleType.WORKING_HOURS.value,
        enforcement="hard",
        params={"day_of_week": day, "start": start, "end": end},
    )


def _within(now: datetime, rules: list[AvailabilityRule] | None = None) -> bool:
    prefs = UserPreferences(timezone="America/New_York")
    with patch("app.services.sync_scheduler_service.datetime") as mock_dt:
        mock_dt.now.return_value = now
        return _is_within_working_hours(prefs, rules or [])


class TestIsWithinWorkingHours:
    """When in the day the scheduled pass reads a clinician's calendars."""

    def test_no_rules_reads_seven_to_seven(self) -> None:
        assert _read_window([]) == (7 * 60, 19 * 60)
        assert _within(_at(7)) is True
        assert _within(_at(18, 59)) is True
        assert _within(_at(6, 59)) is False
        assert _within(_at(19)) is False

    def test_reads_from_an_hour_before_the_earliest_to_two_after_the_latest(self) -> None:
        rules = [_hours(0, "09:00", "17:00"), _hours(2, "10:00", "19:30")]
        assert _read_window(rules) == (8 * 60, 21 * 60 + 30)
        assert _within(_at(8), rules) is True
        assert _within(_at(21, 29), rules) is True
        assert _within(_at(7, 59), rules) is False
        assert _within(_at(21, 30), rules) is False

    def test_a_late_evening_schedule_is_read_into_the_night(self) -> None:
        rules = [_hours(1, "14:00", "21:00"), _hours(3, "16:00", "22:30")]
        assert _read_window(rules) == (13 * 60, 24 * 60)
        assert _within(_at(23, 45), rules) is True
        # The default window would have stopped at seven.
        assert _within(_at(20), []) is False
        assert _within(_at(20), rules) is True
        assert _within(_at(12, 30), rules) is False

    def test_an_early_start_does_not_wrap_past_midnight(self) -> None:
        assert _read_window([_hours(0, "00:30", "08:00")]) == (0, 10 * 60)

    def test_other_rules_and_malformed_hours_are_ignored(self) -> None:
        block = AvailabilityRule(
            id="b",
            user_id="user1",
            rule_type=RuleType.BLOCK_TIME_RANGE.value,
            enforcement="hard",
            params={"start": "03:00", "end": "04:00"},
        )
        broken = _hours(4, "late", "17:00")
        assert _read_window([block, broken]) == (7 * 60, 19 * 60)

    def test_invalid_timezone_defaults_to_sync(self) -> None:
        """Invalid timezone should default to syncing (don't skip)."""
        prefs = UserPreferences(timezone="Invalid/Timezone")
        assert _is_within_working_hours(prefs) is True


# dispatch() tests


class TestDispatch:
    """Test the dispatch phase — filtering and enqueuing."""

    @patch("app.services.sync_scheduler_service._enqueue_sync_task")
    @patch("app.services.sync_scheduler_service._is_within_working_hours", return_value=True)
    def test_enqueues_eligible_users(self, mock_hours: MagicMock, mock_enqueue: MagicMock) -> None:
        """Users within working hours should be enqueued."""
        service = _make_service(
            ical_configs=[_make_ical_config("user1"), _make_ical_config("user2")],
        )
        summary = service.dispatch()
        assert summary.enqueued == 2
        assert mock_enqueue.call_count == 2

    @patch("app.services.sync_scheduler_service._enqueue_sync_task")
    @patch("app.services.sync_scheduler_service._is_within_working_hours", return_value=False)
    def test_skips_outside_working_hours(
        self, mock_hours: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        """Users outside working hours should be skipped."""
        service = _make_service(
            ical_configs=[_make_ical_config("user1")],
        )
        summary = service.dispatch()
        assert summary.enqueued == 0
        assert summary.skipped_outside_hours == 1
        mock_enqueue.assert_not_called()

    @patch("app.services.sync_scheduler_service._enqueue_sync_task")
    @patch("app.services.sync_scheduler_service._is_within_working_hours", return_value=True)
    def test_skips_circuit_breaker(self, mock_hours: MagicMock, mock_enqueue: MagicMock) -> None:
        """Users with too many consecutive errors should be skipped."""
        service = _make_service(
            ical_configs=[_make_ical_config("user1", consecutive_error_count=10)],
        )
        summary = service.dispatch()
        assert summary.enqueued == 0
        assert summary.skipped_circuit_breaker == 1
        mock_enqueue.assert_not_called()

    @patch("app.services.sync_scheduler_service._enqueue_sync_task")
    @patch("app.services.sync_scheduler_service._is_within_working_hours", return_value=True)
    def test_a_broken_feed_does_not_stop_the_google_calendar(
        self, mock_hours: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        """Only a user with every source past the limit is skipped."""
        service = _make_service(
            ical_configs=[_make_ical_config("user1", consecutive_error_count=10)],
            google_tokens=[_make_google_token("user1")],
        )
        summary = service.dispatch()
        assert summary.enqueued == 1
        assert summary.skipped_circuit_breaker == 0

    @patch("app.services.sync_scheduler_service._enqueue_sync_task")
    @patch("app.services.sync_scheduler_service._is_within_working_hours", return_value=True)
    def test_a_paused_google_calendar_alone_is_skipped(
        self, mock_hours: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        service = _make_service(google_tokens=[_make_google_token("user1", 5)])
        summary = service.dispatch()
        assert summary.enqueued == 0
        assert summary.skipped_circuit_breaker == 1

    @patch("app.services.sync_scheduler_service._enqueue_sync_task")
    def test_the_window_comes_from_the_users_working_hours(self, mock_enqueue: MagicMock) -> None:
        service = _make_service(google_tokens=[_make_google_token("user1")])
        service._user_repo.get_preferences_many.return_value = {  # type: ignore[attr-defined]
            "user1": UserPreferences(timezone="America/New_York")
        }
        service._availability_rule_repo.list_by_user.return_value = [  # type: ignore[union-attr]
            _hours(1, "13:00", "21:00")
        ]
        with patch("app.services.sync_scheduler_service.datetime") as mock_dt:
            mock_dt.now.return_value = _at(22)
            summary = service.dispatch()
        assert summary.enqueued == 1
        service._availability_rule_repo.list_by_user.assert_called_with("user1")  # type: ignore[union-attr]

    @patch("app.services.sync_scheduler_service._enqueue_sync_task")
    @patch("app.services.sync_scheduler_service._is_within_working_hours", return_value=True)
    def test_deduplicates_users_across_sources(
        self, mock_hours: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        """A user with both iCal and Google should only be enqueued once."""
        service = _make_service(
            ical_configs=[_make_ical_config("user1")],
            google_tokens=[_make_google_token("user1")],
        )
        summary = service.dispatch()
        assert summary.enqueued == 1
        assert mock_enqueue.call_count == 1

    @patch("app.services.sync_scheduler_service._enqueue_sync_task", side_effect=Exception("boom"))
    @patch("app.services.sync_scheduler_service._is_within_working_hours", return_value=True)
    def test_handles_enqueue_errors(self, mock_hours: MagicMock, mock_enqueue: MagicMock) -> None:
        """Enqueue failures should be counted, not raised."""
        service = _make_service(
            ical_configs=[_make_ical_config("user1")],
        )
        summary = service.dispatch()
        assert summary.enqueued == 0
        assert summary.errors == 1


# execute() tests


class TestExecute:
    """Test the per-user execute phase."""

    def test_syncs_ical_and_google(self) -> None:
        """Execute should call iCal sync, Google sync, and reminders."""
        service = _make_service()
        # Set up Google token to be found
        service._google_token_repo.get.return_value = _make_google_token()  # type: ignore[attr-defined]

        service.execute("user1")
        service._ical_sync_service.sync.assert_called_once_with("user1")  # type: ignore[attr-defined]
        service._google_calendar_service.sync_from_google.assert_called_once_with("user1")  # type: ignore[attr-defined]
        service._reminder_service.check_and_send_reminders.assert_called_once_with("user1")  # type: ignore[attr-defined]

    def test_ical_error_does_not_block_google(self) -> None:
        """iCal failure should not prevent Google sync or reminders."""
        service = _make_service()
        service._ical_sync_service.sync.side_effect = Exception("feed down")  # type: ignore[attr-defined]
        service._google_token_repo.get.return_value = _make_google_token()  # type: ignore[attr-defined]

        summary = service.execute("user1")
        assert summary.ical_errors >= 1
        service._google_calendar_service.sync_from_google.assert_called_once()  # type: ignore[attr-defined]
        service._reminder_service.check_and_send_reminders.assert_called_once()  # type: ignore[attr-defined]

    def test_google_error_does_not_block_reminders(self) -> None:
        """Google Calendar failure should not prevent reminder check."""
        service = _make_service()
        service._google_token_repo.get.return_value = _make_google_token()  # type: ignore[attr-defined]
        service._google_calendar_service.sync_from_google.side_effect = Exception("auth failed")  # type: ignore[attr-defined]

        summary = service.execute("user1")
        assert summary.google_error is True
        service._reminder_service.check_and_send_reminders.assert_called_once()  # type: ignore[attr-defined]

    def test_skips_google_when_not_connected(self) -> None:
        """If no Google token exists, skip Google sync."""
        service = _make_service()
        service._google_token_repo.get.return_value = None  # type: ignore[attr-defined]

        summary = service.execute("user1")
        assert summary.google_synced is False
        service._google_calendar_service.sync_from_google.assert_not_called()  # type: ignore[attr-defined]

    def test_aggregates_reminder_counts(self) -> None:
        """Reminder counts should be summed from the service result."""
        service = _make_service()
        service._reminder_service.check_and_send_reminders.return_value = {  # type: ignore[attr-defined]
            "24h_sent": 2,
            "1h_sent": 1,
        }
        summary = service.execute("user1")
        assert summary.reminders_sent == 3

    def test_ignores_change_with_no_matching_appointment(self) -> None:
        """A Google change for an event Pablo never pushed is not counted."""
        service = _make_service()
        service._google_token_repo.get.return_value = _make_google_token()  # type: ignore[attr-defined]
        service._google_calendar_service.sync_from_google.return_value = [  # type: ignore[attr-defined]
            {"google_event_id": "gcal-evt-unknown", "summary": "", "status": "confirmed"},
        ]
        service._appointment_repo.get_by_google_event_id.return_value = None  # type: ignore[attr-defined]

        summary = service.execute("user1")

        assert summary.google_changes_processed == 0
        service._appointment_repo.update.assert_not_called()  # type: ignore[attr-defined]


# Following Google changes end to end


def _future_appointment(appt_id: str, days: int) -> Appointment:
    start = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return Appointment(
        id=appt_id,
        user_id="user1",
        patient_id=f"patient-{appt_id}",
        title="Session",
        start_at=start,
        end_at=start + timedelta(minutes=50),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
        google_event_id=f"evt-{appt_id}",
        google_sync_status="synced",
    )


def _service_over(repo: InMemoryAppointmentRepository) -> SyncSchedulerService:
    """A scheduler whose appointments are real rows rather than mocks."""
    service = _make_service()
    calendar = service._google_calendar_service
    service._appointment_repo = repo
    service._follower = GoogleChangeFollower(repo, calendar)
    service._audit_service = AuditService(InMemoryAuditRepository())
    service._google_token_repo.get.return_value = _make_google_token()  # type: ignore[attr-defined]
    service._user_repo.get.return_value = User(  # type: ignore[attr-defined]
        id="user1",
        email="therapist@example.com",
        name="Therapist",
        created_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
    )
    return service


class TestFollowsGoogle:
    def test_a_poll_moves_a_session_moved_in_google(self) -> None:
        repo = InMemoryAppointmentRepository()
        appt = repo.create(_future_appointment("a", 3))
        repo.grant_access(appt.patient_id, "user1")
        service = _service_over(repo)
        new_start = appt.start_at + timedelta(days=1)
        service._google_calendar_service.sync_from_google.return_value = [  # type: ignore[attr-defined]
            {
                "google_event_id": "evt-a",
                "status": "confirmed",
                "start": {"dateTime": new_start.isoformat()},
                "end": {"dateTime": (new_start + timedelta(minutes=50)).isoformat()},
            }
        ]

        summary = service.execute("user1")

        assert summary.google_changes_processed == 1
        moved = repo.get("a", "user1")
        assert moved is not None
        assert moved.start_at == new_start

    def test_a_deleted_calendar_is_recreated_and_nothing_is_cancelled(self) -> None:
        repo = InMemoryAppointmentRepository()
        for i in range(20):
            repo.create(_future_appointment(f"s{i}", 1 + i))
        service = _service_over(repo)
        calendar = service._google_calendar_service
        calendar.sync_from_google.side_effect = CalendarGoneError()  # type: ignore[attr-defined]
        calendar.recreate_app_calendar.return_value = True  # type: ignore[attr-defined]
        calendar.push_appointment_event.side_effect = [  # type: ignore[attr-defined]
            PushedEvent(event_id=f"fresh-{i}", conference_url=None) for i in range(20)
        ]

        summary = service.execute("user1")

        assert summary.google_synced is True
        calendar.recreate_app_calendar.assert_called_once_with("user1")  # type: ignore[attr-defined]
        rows = repo.list_by_range("user1", utc_now(), utc_now() + timedelta(days=60))
        assert len(rows) == 20
        assert all(r.status == AppointmentStatus.CONFIRMED for r in rows)
        assert {r.google_event_id for r in rows} == {f"fresh-{i}" for i in range(20)}

    def test_a_failed_recreate_does_not_stop_reminders(self) -> None:
        service = _service_over(InMemoryAppointmentRepository())
        calendar = service._google_calendar_service
        calendar.sync_from_google.side_effect = CalendarGoneError()  # type: ignore[attr-defined]
        calendar.recreate_app_calendar.side_effect = Exception("google down")  # type: ignore[attr-defined]

        summary = service.execute("user1")

        assert summary.google_error is True
        service._reminder_service.check_and_send_reminders.assert_called_once()  # type: ignore[attr-defined]


# How a Google read went, kept on the connection


def _connected(count: int = 0) -> SyncSchedulerService:
    service = _make_service()
    service._google_token_repo.get.return_value = _make_google_token(  # type: ignore[attr-defined]
        consecutive_error_count=count
    )
    return service


class TestRecordsGoogleReads:
    def test_a_read_that_works_is_recorded(self) -> None:
        service = _connected()

        service.execute("user1")

        tokens = service._google_token_repo
        tokens.record_read_success.assert_called_once()  # type: ignore[attr-defined]
        assert tokens.record_read_success.call_args[0][0] == "user1"  # type: ignore[attr-defined]
        tokens.record_read_failure.assert_not_called()  # type: ignore[attr-defined]

    def test_a_revoked_grant_is_recorded_by_kind(self) -> None:
        service = _connected()
        error = RefreshError("invalid_grant: Token has been expired or revoked.")
        service._google_calendar_service.sync_from_google.side_effect = error  # type: ignore[attr-defined]

        summary = service.execute("user1")

        assert summary.google_error is True
        service._google_token_repo.record_read_failure.assert_called_once_with(  # type: ignore[attr-defined]
            "user1", "access_revoked"
        )
        service._google_token_repo.record_read_success.assert_not_called()  # type: ignore[attr-defined]

    def test_a_failed_follow_read_is_recorded_too(self) -> None:
        service = _connected()
        token = _make_google_token()
        token.follow_calendar_id = "primary"
        service._google_token_repo.get.return_value = token  # type: ignore[attr-defined]
        calendar = service._google_calendar_service
        calendar.read_main_calendar_changes.side_effect = TimeoutError()  # type: ignore[attr-defined]

        summary = service.execute("user1")

        assert summary.google_error is True
        service._google_token_repo.record_read_failure.assert_called_once_with(  # type: ignore[attr-defined]
            "user1", "read_failed"
        )

    def test_the_failure_is_not_logged_with_its_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        service = _connected()
        service._google_calendar_service.sync_from_google.side_effect = RuntimeError(  # type: ignore[attr-defined]
            "Session with Jane Doe"
        )

        service.execute("user1")

        assert "Jane Doe" not in caplog.text
        assert "read_failed" in caplog.text

    def test_a_scheduled_pass_leaves_a_paused_calendar_alone(self) -> None:
        limit = get_settings().calendar_sync_max_consecutive_failures
        service = _connected(count=limit)

        summary = service.execute("user1")

        assert summary.google_paused is True
        service._google_calendar_service.sync_from_google.assert_not_called()  # type: ignore[attr-defined]
        service._google_token_repo.record_read_success.assert_not_called()  # type: ignore[attr-defined]
        service._reminder_service.check_and_send_reminders.assert_called_once()  # type: ignore[attr-defined]

    def test_a_read_on_request_reads_a_paused_calendar_and_clears_it(self) -> None:
        limit = get_settings().calendar_sync_max_consecutive_failures
        service = _connected(count=limit)

        summary = service.execute("user1", on_request=True)

        assert summary.google_paused is False
        assert summary.google_synced is True
        service._google_calendar_service.sync_from_google.assert_called_once_with("user1")  # type: ignore[attr-defined]
        service._google_token_repo.record_read_success.assert_called_once()  # type: ignore[attr-defined]

    def test_a_scheduled_pass_leaves_out_a_broken_feed_and_reads_the_rest(self) -> None:
        limit = get_settings().calendar_sync_max_consecutive_failures
        service = _make_service()
        service._ical_config_repo.list_by_user.return_value = [  # type: ignore[attr-defined]
            _make_ical_config(ehr_system="simplepractice", consecutive_error_count=limit),
            _make_ical_config(ehr_system="therapynotes"),
        ]

        service.execute("user1")

        service._ical_sync_service.sync.assert_called_once_with("user1", "therapynotes")  # type: ignore[attr-defined]

    def test_a_read_on_request_reads_every_feed(self) -> None:
        limit = get_settings().calendar_sync_max_consecutive_failures
        service = _make_service()
        service._ical_config_repo.list_by_user.return_value = [  # type: ignore[attr-defined]
            _make_ical_config(consecutive_error_count=limit),
        ]

        service.execute("user1", on_request=True)

        service._ical_sync_service.sync.assert_called_once_with("user1")  # type: ignore[attr-defined]
