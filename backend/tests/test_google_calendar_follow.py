# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pablo's own sessions follow the moves and deletions made to them in Google."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.repositories.audit import InMemoryAuditRepository
from app.scheduling_engine.exceptions import AppointmentConflictError
from app.scheduling_engine.models.appointment import (
    Appointment,
    AppointmentStatus,
    CancellationActor,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.audit_service import AuditService
from app.services.google_calendar_follow import (
    GoogleChangeFollower,
    GoogleChangeUnavailableError,
    GoogleSyncStatus,
    Resolution,
)
from app.services.google_calendar_service import PushedEvent
from app.utcnow import utc_now

if TYPE_CHECKING:
    from app.models import User

USER_ID = "test-user-123"


def _in(days: float, hour: int = 14) -> datetime:
    """A whole-hour instant ``days`` from now, so tests never sit on the clock."""
    base = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return base.replace(hour=hour)


def _change(event_id: str, start: datetime | None = None, *, cancelled: bool = False) -> dict:
    if cancelled:
        return {"google_event_id": event_id, "status": "cancelled", "start": {}, "end": {}}
    assert start is not None
    return {
        "google_event_id": event_id,
        "status": "confirmed",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
    }


class _Harness:
    def __init__(self) -> None:
        self.repo = InMemoryAppointmentRepository()
        self.calendar = MagicMock()
        self._pushed = 0

        def push(_user_id: str, appt: Appointment) -> PushedEvent:
            # Like Google: an update keeps its event id, an insert gets a new one.
            if appt.google_event_id:
                return PushedEvent(event_id=appt.google_event_id, conference_url=None)
            self._pushed += 1
            return PushedEvent(event_id=f"new-evt-{self._pushed}", conference_url=None)

        self.calendar.push_appointment_event.side_effect = push
        self.audit_repo = InMemoryAuditRepository()
        self.audit = AuditService(self.audit_repo)
        self.follower = GoogleChangeFollower(self.repo, self.calendar)

    def add(
        self,
        appt_id: str,
        start: datetime,
        *,
        event_id: str | None = None,
        session_id: str | None = None,
        reminders_sent: bool = False,
    ) -> Appointment:
        appt = Appointment(
            id=appt_id,
            user_id=USER_ID,
            patient_id=f"patient-{appt_id}",
            title="Session",
            start_at=start,
            end_at=start + timedelta(minutes=50),
            duration_minutes=50,
            status=AppointmentStatus.CONFIRMED,
            session_type="individual",
            google_event_id=event_id or f"evt-{appt_id}",
            google_sync_status="synced",
            session_id=session_id,
            reminder_24h_sent=reminders_sent,
            reminder_1h_sent=reminders_sent,
        )
        self.repo.grant_access(appt.patient_id, USER_ID)
        return self.repo.create(appt)

    def get(self, appt_id: str) -> Appointment:
        appt = self.repo.get(appt_id, USER_ID)
        assert appt is not None
        return appt

    def follow(self, user: User, changes: list[dict[str, Any]]) -> Any:
        return self.follower.follow(user, self.audit, changes)

    def history(self, appt_id: str) -> list[tuple[str, Any, str | None, str | None]]:
        return [
            (e.action, (e.changes or {}).get("reason"), e.actor_type, e.actor_component)
            for e in self.audit_repo.list_for_user(USER_ID)
            if e.resource_id == appt_id
        ]


@pytest.fixture
def h() -> _Harness:
    return _Harness()


class TestMoves:
    def test_a_session_moved_in_google_moves_here(self, h: _Harness, mock_user: User) -> None:
        h.add("a", _in(3), reminders_sent=True)
        new_start = _in(4, hour=10)

        summary = h.follow(mock_user, [_change("evt-a", new_start)])

        moved = h.get("a")
        assert summary.moved == 1
        assert moved.start_at == new_start
        assert moved.end_at == new_start + timedelta(minutes=50)
        assert moved.google_sync_status == GoogleSyncStatus.SYNCED
        # The reminder schedule restarts from the new time.
        assert moved.reminder_24h_sent is False
        assert moved.reminder_1h_sent is False
        h.calendar.push_appointment_event.assert_not_called()

    def test_the_move_is_written_to_the_history(self, h: _Harness, mock_user: User) -> None:
        h.add("a", _in(3))

        h.follow(mock_user, [_change("evt-a", _in(4))])

        assert h.history("a") == [
            ("appointment_updated", "moved_in_google_calendar", "system", "google_calendar_sync")
        ]

    def test_a_move_onto_another_session_changes_nothing_and_flags_it(
        self, h: _Harness, mock_user: User
    ) -> None:
        a_start, b_start = _in(3, hour=9), _in(3, hour=15)
        h.add("a", a_start)
        h.add("b", b_start)

        summary = h.follow(mock_user, [_change("evt-a", b_start + timedelta(minutes=20))])

        assert summary.moved == 0
        assert summary.flagged == 1
        assert h.get("a").start_at == a_start
        assert h.get("b").start_at == b_start
        assert h.get("a").google_sync_status == GoogleSyncStatus.EXTERNAL_CHANGE
        assert h.get("b").google_sync_status == GoogleSyncStatus.SYNCED
        assert h.history("a") == []

    def test_a_chain_rearranged_in_one_go_is_followed(self, h: _Harness, mock_user: User) -> None:
        """B takes A's slot and A moves on: whichever Google reports first."""
        h.add("a", _in(3, hour=9))
        h.add("b", _in(3, hour=15))

        summary = h.follow(
            mock_user,
            [_change("evt-b", _in(3, hour=9)), _change("evt-a", _in(3, hour=11))],
        )

        assert summary.moved == 2
        assert h.get("a").start_at == _in(3, hour=11)
        assert h.get("b").start_at == _in(3, hour=9)

    def test_pablos_own_push_coming_back_is_not_a_change(
        self, h: _Harness, mock_user: User
    ) -> None:
        start = _in(3)
        h.add("a", start)

        summary = h.follow(mock_user, [_change("evt-a", start)])

        assert summary.changed == 0
        assert h.history("a") == []

    def test_moving_back_to_pablos_time_settles_the_flag(
        self, h: _Harness, mock_user: User
    ) -> None:
        start = _in(3)
        h.add("a", start).google_sync_status = GoogleSyncStatus.EXTERNAL_CHANGE

        h.follow(mock_user, [_change("evt-a", start)])

        assert h.get("a").google_sync_status == GoogleSyncStatus.SYNCED

    def test_an_all_day_event_is_flagged_rather_than_followed(
        self, h: _Harness, mock_user: User
    ) -> None:
        start = _in(3)
        h.add("a", start)
        change = {
            "google_event_id": "evt-a",
            "status": "confirmed",
            "start": {"date": "2030-01-01"},
            "end": {"date": "2030-01-02"},
        }

        h.follow(mock_user, [change])

        assert h.get("a").start_at == start
        assert h.get("a").google_sync_status == GoogleSyncStatus.EXTERNAL_CHANGE


class TestDeletions:
    def test_a_session_deleted_in_google_is_quietly_cancelled(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.add("a", _in(3))

        summary = h.follow(mock_user, [_change("evt-a", cancelled=True)])

        cancelled = h.get("a")
        assert summary.cancelled == 1
        assert cancelled.status == AppointmentStatus.CANCELLED
        assert cancelled.google_sync_status == GoogleSyncStatus.REMOVED_IN_GOOGLE
        # No fee: nobody chargeable cancelled it, and it is not late.
        assert cancelled.cancelled_by == CancellationActor.SYSTEM
        assert cancelled.late_cancellation is False
        assert h.history("a") == [
            (
                "appointment_cancelled",
                "removed_from_google_calendar",
                "system",
                "google_calendar_sync",
            )
        ]

    def test_undo_restores_it_and_puts_the_event_back(self, h: _Harness, mock_user: User) -> None:
        h.add("a", _in(3))
        h.follow(mock_user, [_change("evt-a", cancelled=True)])

        restored = h.follower.resolve(USER_ID, "a", Resolution.KEEP_PABLO)

        assert restored.status == AppointmentStatus.CONFIRMED
        assert restored.cancelled_at is None
        assert restored.cancelled_by is None
        assert restored.late_cancellation is None
        assert restored.google_sync_status == GoogleSyncStatus.SYNCED
        # A new event: the deleted one's id is not reused.
        assert restored.google_event_id == "new-evt-1"
        pushed = h.calendar.push_appointment_event.call_args.args[1]
        assert pushed.id == "a"

    def test_undo_is_refused_when_the_slot_was_taken_since(
        self, h: _Harness, mock_user: User
    ) -> None:
        start = _in(3)
        h.add("a", start)
        h.follow(mock_user, [_change("evt-a", cancelled=True)])
        h.add("b", start)

        with pytest.raises(AppointmentConflictError):
            h.follower.resolve(USER_ID, "a", Resolution.KEEP_PABLO)
        assert h.get("a").status == AppointmentStatus.CANCELLED

    def test_accepting_the_removal_stops_it_asking(self, h: _Harness, mock_user: User) -> None:
        h.add("a", _in(3))
        h.follow(mock_user, [_change("evt-a", cancelled=True)])

        dismissed = h.follower.resolve(USER_ID, "a", Resolution.ACCEPT_GOOGLE)

        assert dismissed.status == AppointmentStatus.CANCELLED
        assert dismissed.google_sync_status is None
        h.calendar.push_appointment_event.assert_not_called()

    def test_pablos_own_cancellation_coming_back_is_ignored(
        self, h: _Harness, mock_user: User
    ) -> None:
        h.add("a", _in(3)).status = AppointmentStatus.CANCELLED

        summary = h.follow(mock_user, [_change("evt-a", cancelled=True)])

        assert summary.changed == 0
        assert h.get("a").google_sync_status == GoogleSyncStatus.SYNCED


class TestBulkGuard:
    def test_every_session_vanishing_at_once_cancels_none(
        self, h: _Harness, mock_user: User
    ) -> None:
        for i in range(20):
            h.add(f"s{i}", _in(1 + i))

        summary = h.follow(mock_user, [_change(f"evt-s{i}", cancelled=True) for i in range(20)])

        assert summary.cancelled == 0
        assert summary.held == 20
        for i in range(20):
            held = h.get(f"s{i}")
            assert held.status == AppointmentStatus.CONFIRMED
            assert held.google_sync_status == GoogleSyncStatus.MISSING_IN_GOOGLE
            assert h.history(f"s{i}") == []
        # One prompt, asked once for all of them.
        assert len(h.follower.held_removals(USER_ID)) == 20

    def test_a_few_deletions_among_many_sessions_go_ahead(
        self, h: _Harness, mock_user: User
    ) -> None:
        """Four of twenty is more than three but not more than a quarter."""
        for i in range(20):
            h.add(f"s{i}", _in(1 + i))

        summary = h.follow(mock_user, [_change(f"evt-s{i}", cancelled=True) for i in range(4)])

        assert summary.cancelled == 4
        assert summary.held == 0

    def test_three_deletions_go_ahead_however_small_the_calendar(
        self, h: _Harness, mock_user: User
    ) -> None:
        for i in range(3):
            h.add(f"s{i}", _in(1 + i))

        summary = h.follow(mock_user, [_change(f"evt-s{i}", cancelled=True) for i in range(3)])

        assert summary.cancelled == 3

    def test_putting_held_sessions_back_pushes_each_to_google(
        self, h: _Harness, mock_user: User
    ) -> None:
        for i in range(5):
            h.add(f"s{i}", _in(1 + i))
        h.follow(mock_user, [_change(f"evt-s{i}", cancelled=True) for i in range(5)])

        restored = h.follower.resolve_held(USER_ID, Resolution.KEEP_PABLO)

        assert len(restored) == 5
        assert h.calendar.push_appointment_event.call_count == 5
        assert h.follower.held_removals(USER_ID) == []
        assert all(h.get(f"s{i}").status == AppointmentStatus.CONFIRMED for i in range(5))

    def test_confirming_held_deletions_cancels_them(self, h: _Harness, mock_user: User) -> None:
        for i in range(5):
            h.add(f"s{i}", _in(1 + i))
        h.follow(mock_user, [_change(f"evt-s{i}", cancelled=True) for i in range(5)])

        cancelled = h.follower.resolve_held(USER_ID, Resolution.ACCEPT_GOOGLE)

        assert len(cancelled) == 5
        for i in range(5):
            row = h.get(f"s{i}")
            assert row.status == AppointmentStatus.CANCELLED
            assert row.late_cancellation is False
        assert h.follower.held_removals(USER_ID) == []


class TestNeverTouched:
    def test_a_session_in_the_past_is_never_moved_or_cancelled(
        self, h: _Harness, mock_user: User
    ) -> None:
        past = _in(-2)
        h.add("moved", past)
        h.add("deleted", past + timedelta(hours=2))

        summary = h.follow(
            mock_user,
            [_change("evt-moved", _in(3)), _change("evt-deleted", cancelled=True)],
        )

        assert summary.changed == 0
        assert h.get("moved").start_at == past
        assert h.get("deleted").status == AppointmentStatus.CONFIRMED

    def test_a_session_with_a_note_is_never_moved_or_cancelled(
        self, h: _Harness, mock_user: User
    ) -> None:
        start = _in(3)
        h.add("moved", start, session_id="session-1")
        h.add("deleted", start + timedelta(hours=2), session_id="session-2")

        summary = h.follow(
            mock_user,
            [_change("evt-moved", _in(5)), _change("evt-deleted", cancelled=True)],
        )

        assert summary.changed == 0
        assert h.get("moved").start_at == start
        assert h.get("deleted").status == AppointmentStatus.CONFIRMED

    def test_an_event_pablo_did_not_write_is_ignored(self, h: _Harness, mock_user: User) -> None:
        h.add("a", _in(3))

        summary = h.follow(mock_user, [_change("someone-elses-event", cancelled=True)])

        assert summary.changed == 0


class TestSettlingAFlaggedMove:
    def _flagged(self, h: _Harness, mock_user: User) -> tuple[datetime, datetime]:
        a_start, b_start = _in(3, hour=9), _in(3, hour=15)
        h.add("a", a_start)
        h.add("b", b_start)
        h.follow(mock_user, [_change("evt-a", b_start)])
        return a_start, b_start

    def test_keeping_pablos_time_writes_it_back_to_google(
        self, h: _Harness, mock_user: User
    ) -> None:
        a_start, _ = self._flagged(h, mock_user)

        kept = h.follower.resolve(USER_ID, "a", Resolution.KEEP_PABLO)

        assert kept.start_at == a_start
        assert kept.google_sync_status == GoogleSyncStatus.SYNCED
        # The same event, updated in place.
        h.calendar.push_appointment_event.assert_called_once()
        assert kept.google_event_id == "evt-a"

    def test_googles_time_is_refused_while_it_still_collides(
        self, h: _Harness, mock_user: User
    ) -> None:
        _, b_start = self._flagged(h, mock_user)
        h.calendar.read_event_times.return_value = (b_start, b_start + timedelta(minutes=50))

        with pytest.raises(AppointmentConflictError):
            h.follower.resolve(USER_ID, "a", Resolution.ACCEPT_GOOGLE)

    def test_googles_time_is_taken_once_the_collision_is_gone(
        self, h: _Harness, mock_user: User
    ) -> None:
        _, b_start = self._flagged(h, mock_user)
        h.get("b").status = AppointmentStatus.CANCELLED
        h.calendar.read_event_times.return_value = (b_start, b_start + timedelta(minutes=50))

        taken = h.follower.resolve(USER_ID, "a", Resolution.ACCEPT_GOOGLE)

        assert taken.start_at == b_start
        assert taken.google_sync_status == GoogleSyncStatus.SYNCED

    def test_nothing_to_settle_on_an_ordinary_session(self, h: _Harness) -> None:
        h.add("a", _in(3))

        with pytest.raises(GoogleChangeUnavailableError):
            h.follower.resolve(USER_ID, "a", Resolution.KEEP_PABLO)


class TestRecreatedCalendar:
    def test_upcoming_sessions_are_pushed_as_new_events(self, h: _Harness) -> None:
        h.add("past", _in(-2))
        h.add("a", _in(3))
        h.add("b", _in(5))
        h.add("gone", _in(6)).status = AppointmentStatus.CANCELLED

        pushed = h.follower.repush_upcoming(USER_ID)

        assert pushed == 2
        assert {h.get("a").google_event_id, h.get("b").google_event_id} == {
            "new-evt-1",
            "new-evt-2",
        }
        assert h.get("past").google_event_id == "evt-past"
        assert all(h.get(i).status == AppointmentStatus.CONFIRMED for i in ("past", "a", "b"))
