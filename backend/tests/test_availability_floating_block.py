# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Floating blocks: an hour kept free somewhere inside a window."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from app.models.availability_rule_params import AvailabilityRuleParamsError, validate_rule_params
from app.scheduling_engine.exceptions import RuleViolationError
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from app.scheduling_engine.models.busy import BusyInterval
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import InMemoryAvailabilityRuleRepository
from app.scheduling_engine.services import floating_block
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService

USER_ID = "user-1"
WEDNESDAY = "2026-03-18"  # weekday() == 2
THURSDAY = "2026-03-19"
LUNCH = {
    "duration_minutes": 60,
    "window_start": "11:30",
    "window_end": "14:00",
    "days_of_week": [0, 1, 2, 3, 4],
    "label": "Lunch",
}


def _rule(rule_type: str, params: dict[str, object], rule_id: str) -> AvailabilityRule:
    return AvailabilityRule(
        id=rule_id,
        user_id=USER_ID,
        rule_type=rule_type,
        enforcement=EnforcementLevel.HARD,
        params=params,
    )


def _appt(start: str, end: str, appt_id: str = "appt-1") -> Appointment:
    start_at = datetime.fromisoformat(start)
    end_at = datetime.fromisoformat(end)
    return Appointment(
        id=appt_id,
        user_id=USER_ID,
        patient_id="patient-1",
        title="Session",
        start_at=start_at,
        end_at=end_at,
        duration_minutes=int((end_at - start_at).total_seconds() // 60),
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
    )


class _Busy:
    def __init__(self, windows: list[BusyInterval]) -> None:
        self.windows = windows

    def busy_between(self, user_id: str, start: datetime, end: datetime) -> list[BusyInterval]:
        del user_id
        return [w for w in self.windows if w.start < end and start < w.end]


@pytest.fixture
def rule_repo() -> InMemoryAvailabilityRuleRepository:
    repo = InMemoryAvailabilityRuleRepository()
    repo.create(
        _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"}, "wh")
    )
    repo.create(
        _rule(RuleType.WORKING_HOURS, {"day_of_week": 3, "start": "09:00", "end": "17:00"}, "wh3")
    )
    return repo


@pytest.fixture
def appt_repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def engine(
    rule_repo: InMemoryAvailabilityRuleRepository, appt_repo: InMemoryAppointmentRepository
) -> AvailabilityEngine:
    return AvailabilityEngine(rule_repo, appt_repo)


def _lunch(rule_repo: InMemoryAvailabilityRuleRepository, **changes: object) -> AvailabilityRule:
    return rule_repo.create(_rule(RuleType.FLOATING_BLOCK, {**LUNCH, **changes}, "lunch"))


def _starts(engine: AvailabilityEngine, day: str = WEDNESDAY, duration: int = 60) -> list[str]:
    return [s.start[11:16] for s in engine.get_free_slots(USER_ID, day, duration).slots]


class TestListing:
    def test_a_slot_that_would_leave_no_free_hour_in_the_window_is_not_offered(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        _lunch(rule_repo)
        appt_repo.create(_appt(f"{WEDNESDAY}T11:30:00+00:00", f"{WEDNESDAY}T12:15:00+00:00"))

        starts = _starts(engine, duration=45)

        # 12:15-13:00 leaves 13:00-14:00 free, and 13:15-14:00 leaves
        # 12:15-13:15: both offered. 13:00-13:45 would leave 45 minutes
        # either side, no free hour: not offered.
        assert "12:15" in starts
        assert "13:15" in starts
        assert "13:00" not in starts
        # Time outside the window is untouched.
        assert "09:00" in starts
        assert "14:00" in starts

    def test_with_nothing_booked_the_window_still_hands_out_what_spares_an_hour(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        _lunch(rule_repo)

        starts = _starts(engine, duration=90)

        # 2.5 hours of window and a 60-minute block: a 90-minute session fits
        # at either end, never in the middle where it splits the hour.
        assert "12:00" not in starts
        assert "12:30" in starts

    def test_another_day_of_the_week_is_not_protected(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        _lunch(rule_repo, days_of_week=[0])

        assert "12:00" in _starts(engine)

    def test_a_skipped_date_is_not_protected(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        _lunch(rule_repo, exceptions={WEDNESDAY: {"skip": True}})

        assert "12:00" in _starts(engine, duration=90)
        assert "12:00" not in _starts(engine, THURSDAY, duration=90)

    def test_a_pinned_date_blocks_exactly_the_pinned_hour(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        _lunch(rule_repo, exceptions={WEDNESDAY: {"start": "13:00"}})

        starts = _starts(engine, duration=30)

        assert "12:30" in starts  # 12:30-13:00 leaves the pinned hour alone
        assert "13:00" not in starts
        assert "13:30" not in starts
        assert "14:00" in starts

    def test_calendar_busy_time_counts_as_taken(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
    ) -> None:
        _lunch(rule_repo)
        busy = _Busy(
            [
                BusyInterval(
                    datetime.fromisoformat(f"{WEDNESDAY}T12:00:00+00:00"),
                    datetime.fromisoformat(f"{WEDNESDAY}T13:00:00+00:00"),
                )
            ]
        )
        engine = AvailabilityEngine(rule_repo, appt_repo, busy)

        # The busy hour leaves 13:00-14:00 as the only free hour in the
        # window, so nothing goes in it; 11:30-12:00 does not touch it.
        starts = _starts(engine, duration=30)
        assert "11:30" in starts
        assert "13:00" not in starts
        assert "13:30" not in starts

    def test_the_block_is_read_in_the_clinicians_zone(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        _lunch(rule_repo, window_start="12:00", window_end="13:00")
        tz = ZoneInfo("America/New_York")

        slots = engine.get_free_slots(USER_ID, WEDNESDAY, 60, tz=tz).slots

        local = [
            datetime.fromisoformat(s.start.replace("Z", "+00:00")).astimezone(tz) for s in slots
        ]
        assert not any(12 <= t.hour < 13 for t in local)
        assert any(t.hour == 11 for t in local)

    def test_a_lunch_block_alone_does_not_count_as_availability_set_up(
        self, appt_repo: InMemoryAppointmentRepository
    ) -> None:
        rules = InMemoryAvailabilityRuleRepository()
        rules.create(_rule(RuleType.FLOATING_BLOCK, LUNCH, "lunch"))
        engine = AvailabilityEngine(rules, appt_repo)

        assert engine.get_free_slots(USER_ID, WEDNESDAY, 60).configured is False
        when = datetime.fromisoformat(f"{WEDNESDAY}T12:00:00+00:00")
        assert engine.check_conflicts(USER_ID, when, when).configured is False


class TestBookingGate:
    def test_a_booking_that_takes_the_last_free_stretch_is_a_hard_conflict_naming_the_block(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        _lunch(rule_repo)
        appt_repo.create(_appt(f"{WEDNESDAY}T11:30:00+00:00", f"{WEDNESDAY}T12:15:00+00:00"))

        result = engine.check_conflicts(
            USER_ID, f"{WEDNESDAY}T13:00:00+00:00", f"{WEDNESDAY}T13:45:00+00:00"
        )

        (conflict,) = result.conflicts
        assert conflict.rule_type == RuleType.FLOATING_BLOCK
        assert conflict.enforcement == EnforcementLevel.HARD
        assert "Lunch" in conflict.message

    def test_a_booking_that_leaves_room_is_clear(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        _lunch(rule_repo)
        appt_repo.create(_appt(f"{WEDNESDAY}T11:30:00+00:00", f"{WEDNESDAY}T12:15:00+00:00"))

        result = engine.check_conflicts(
            USER_ID, f"{WEDNESDAY}T12:15:00+00:00", f"{WEDNESDAY}T13:00:00+00:00"
        )

        assert result.conflicts == []

    def test_a_booking_without_override_is_refused_and_one_with_it_goes_through_warned(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
    ) -> None:
        """The self-booking, link and automated paths book without an override;
        only the clinician, shown the warning, books with one."""
        _lunch(rule_repo)
        service = SchedulingService(appt_repo, AvailabilityEngine(rule_repo, appt_repo))
        data: dict[str, str | int | datetime | None] = {
            "patient_id": "patient-1",
            "title": "Session",
            # 12:00-13:30 leaves half an hour either side: no free hour.
            "start_at": f"{WEDNESDAY}T12:00:00+00:00",
            "end_at": f"{WEDNESDAY}T13:30:00+00:00",
            "duration_minutes": 90,
        }

        with pytest.raises(RuleViolationError) as refused:
            service.create_appointment(USER_ID, data=data)
        assert "Lunch" in str(refused.value.violations)

        appt = service.create_appointment(USER_ID, data=data, rule_override=True)
        assert appt.id
        assert any("Lunch" in w for w in service.rule_warnings)

    def test_once_broken_by_a_direct_booking_only_the_window_stays_guarded(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        _lunch(rule_repo)
        appt_repo.create(_appt(f"{WEDNESDAY}T11:30:00+00:00", f"{WEDNESDAY}T14:00:00+00:00"))

        inside = engine.check_conflicts(
            USER_ID, f"{WEDNESDAY}T13:00:00+00:00", f"{WEDNESDAY}T13:30:00+00:00"
        )
        outside = engine.check_conflicts(
            USER_ID, f"{WEDNESDAY}T15:00:00+00:00", f"{WEDNESDAY}T16:00:00+00:00"
        )

        assert [c.rule_type for c in inside.conflicts] == [RuleType.FLOATING_BLOCK]
        assert outside.conflicts == []
        assert "15:00" in _starts(engine)


class TestResolution:
    def test_the_block_settles_into_the_earliest_free_stretch(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        _lunch(rule_repo)
        appt_repo.create(_appt(f"{WEDNESDAY}T11:30:00+00:00", f"{WEDNESDAY}T12:15:00+00:00"))

        (block,) = engine.floating_blocks(USER_ID, WEDNESDAY)

        assert block.state == "floating"
        assert block.label == "Lunch"
        assert block.start == datetime(2026, 3, 18, 12, 15, tzinfo=UTC)
        assert block.end == datetime(2026, 3, 18, 13, 15, tzinfo=UTC)

    def test_pinned_skipped_and_unplaced_days(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        _lunch(rule_repo, exceptions={WEDNESDAY: {"start": "13:00"}, THURSDAY: {"skip": True}})

        (pinned,) = engine.floating_blocks(USER_ID, WEDNESDAY)
        (skipped,) = engine.floating_blocks(USER_ID, THURSDAY)
        assert (pinned.state, pinned.start) == ("pinned", datetime(2026, 3, 18, 13, 0, tzinfo=UTC))
        assert (skipped.state, skipped.start) == ("skipped", None)

        appt_repo.create(_appt("2026-03-20T11:00:00+00:00", "2026-03-20T14:30:00+00:00"))
        (unplaced,) = engine.floating_blocks(USER_ID, "2026-03-20")
        assert (unplaced.state, unplaced.start) == ("unplaced", None)

    def test_a_day_the_block_does_not_cover_has_none(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        _lunch(rule_repo)

        assert engine.floating_blocks(USER_ID, "2026-03-21") == []  # a Saturday


class TestParams:
    def test_lunch_validates(self) -> None:
        assert validate_rule_params(RuleType.FLOATING_BLOCK, LUNCH) == LUNCH

    @pytest.mark.parametrize(
        "changes",
        [
            {"window_end": "11:00"},
            {"duration_minutes": 180},
            {"days_of_week": []},
            {"days_of_week": [2, 2]},
            {"label": ""},
            {"exceptions": {"2026-03-18": {}}},
            {"exceptions": {"2026-03-18": {"skip": True, "start": "12:00"}}},
            {"exceptions": {"2026-02-30": {"skip": True}}},
            {"exceptions": {"2026-03-18": {"start": "23:30"}}},
        ],
    )
    def test_a_bad_block_is_refused(self, changes: dict[str, object]) -> None:
        with pytest.raises(AvailabilityRuleParamsError):
            validate_rule_params(RuleType.FLOATING_BLOCK, {**LUNCH, **changes})

    def test_writing_an_exception_prunes_past_dates(self) -> None:
        params = {**LUNCH, "exceptions": {"2026-03-01": {"skip": True}}}

        updated = floating_block.with_exception(
            params, date(2026, 3, 20), {"start": "13:00"}, today=date(2026, 3, 18)
        )
        cleared = floating_block.with_exception(
            updated, date(2026, 3, 20), None, today=date(2026, 3, 18)
        )

        assert updated["exceptions"] == {"2026-03-20": {"start": "13:00"}}
        assert "exceptions" not in cleared
        validate_rule_params(RuleType.FLOATING_BLOCK, updated)
