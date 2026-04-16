# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the SyncSchedulerService (periodic calendar sync orchestrator)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.models.user import UserPreferences
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.repositories.ical_sync_config import ICalSyncConfig
from app.services.sync_scheduler_service import (
    SyncSchedulerService,
    _is_within_working_hours,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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

    return SyncSchedulerService(
        ical_config_repo=ical_config_repo,
        google_token_repo=google_token_repo,
        user_repo=user_repo,
        ical_sync_service=ical_sync_service,
        google_calendar_service=google_calendar_service,
        reminder_service=reminder_service,
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


# ---------------------------------------------------------------------------
# _is_within_working_hours tests
# ---------------------------------------------------------------------------


class TestIsWithinWorkingHours:
    """Test timezone-aware working hours filter."""

    def test_within_working_hours(self) -> None:
        """User at 10 AM in their timezone should be within 8-18 window."""
        prefs = UserPreferences(
            working_hours_start=8,
            working_hours_end=18,
            timezone="America/New_York",
        )
        # Mock 10:00 AM Eastern
        with patch("app.services.sync_scheduler_service.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 10
            mock_dt.now.return_value = mock_now
            assert _is_within_working_hours(prefs) is True

    def test_outside_working_hours(self) -> None:
        """User at 11 PM should be outside 8-18 + 1hr buffer."""
        prefs = UserPreferences(
            working_hours_start=8,
            working_hours_end=18,
            timezone="America/New_York",
        )
        with patch("app.services.sync_scheduler_service.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.return_value = mock_now
            assert _is_within_working_hours(prefs) is False

    def test_within_buffer_before(self) -> None:
        """User at 7 AM should be within (8-1=7) to 19 window."""
        prefs = UserPreferences(
            working_hours_start=8,
            working_hours_end=18,
            timezone="America/New_York",
        )
        with patch("app.services.sync_scheduler_service.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 7
            mock_dt.now.return_value = mock_now
            assert _is_within_working_hours(prefs) is True

    def test_within_buffer_after(self) -> None:
        """User at 6:30 PM (hour=18) should be within window (end+1=19)."""
        prefs = UserPreferences(
            working_hours_start=8,
            working_hours_end=18,
            timezone="America/New_York",
        )
        with patch("app.services.sync_scheduler_service.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 18
            mock_dt.now.return_value = mock_now
            assert _is_within_working_hours(prefs) is True

    def test_invalid_timezone_defaults_to_sync(self) -> None:
        """Invalid timezone should default to syncing (don't skip)."""
        prefs = UserPreferences(timezone="Invalid/Timezone")
        assert _is_within_working_hours(prefs) is True

    def test_early_morning_start_clamps_to_zero(self) -> None:
        """Working hours starting at 0 should clamp window_start to 0."""
        prefs = UserPreferences(
            working_hours_start=0,
            working_hours_end=8,
            timezone="America/New_York",
        )
        with patch("app.services.sync_scheduler_service.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 0
            mock_dt.now.return_value = mock_now
            assert _is_within_working_hours(prefs) is True


# ---------------------------------------------------------------------------
# dispatch() tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# execute() tests
# ---------------------------------------------------------------------------


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
