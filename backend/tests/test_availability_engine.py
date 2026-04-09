# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for AvailabilityEngine using InMemory repositories."""

from __future__ import annotations

import pytest
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import InMemoryAvailabilityRuleRepository
from app.scheduling_engine.services.availability import AvailabilityEngine

USER_ID = "user-1"


def _rule(
    rule_type: str,
    params: dict[str, object],
    *,
    rule_id: str = "rule-1",
    enforcement: str = EnforcementLevel.HARD,
) -> AvailabilityRule:
    return AvailabilityRule(
        id=rule_id,
        user_id=USER_ID,
        rule_type=rule_type,
        enforcement=enforcement,
        params=params,
        created_at="2026-01-01T00:00:00Z",
    )


def _appt(
    start_at: str,
    end_at: str,
    *,
    appt_id: str = "appt-1",
    status: str = AppointmentStatus.CONFIRMED,
) -> Appointment:
    return Appointment(
        id=appt_id,
        user_id=USER_ID,
        patient_id="patient-1",
        title="Session",
        start_at=start_at,
        end_at=end_at,
        duration_minutes=50,
        status=status,
        session_type="individual",
        created_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def rule_repo() -> InMemoryAvailabilityRuleRepository:
    return InMemoryAvailabilityRuleRepository()


@pytest.fixture
def appt_repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def engine(
    rule_repo: InMemoryAvailabilityRuleRepository,
    appt_repo: InMemoryAppointmentRepository,
) -> AvailabilityEngine:
    return AvailabilityEngine(rule_repo, appt_repo)


class TestWorkingHoursConflict:
    def test_no_conflict_within_hours(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Wednesday = weekday 2, working hours 09:00-17:00
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z")
        assert len(conflicts) == 0

    def test_conflict_outside_hours(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T08:00:00Z", "2026-03-18T08:50:00Z")
        assert len(conflicts) == 1
        assert "working hours" in conflicts[0].message.lower()

    def test_conflict_ends_after_hours(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T16:30:00Z", "2026-03-18T17:20:00Z")
        assert len(conflicts) == 1

    def test_no_conflict_different_day(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Working hours rule only applies to Wednesday (2)
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        # Thursday = weekday 3, no rule defined
        conflicts = engine.check_conflicts(USER_ID, "2026-03-19T10:00:00Z", "2026-03-19T10:50:00Z")
        assert len(conflicts) == 0


class TestBlockDayOfWeek:
    def test_blocked_day(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Block Sunday (6)
        rule_repo.create(_rule(RuleType.BLOCK_DAY_OF_WEEK, {"day_of_week": 6}))
        conflicts = engine.check_conflicts(USER_ID, "2026-03-22T10:00:00Z", "2026-03-22T10:50:00Z")
        assert len(conflicts) == 1
        assert "blocked" in conflicts[0].message.lower()

    def test_unblocked_day(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_rule(RuleType.BLOCK_DAY_OF_WEEK, {"day_of_week": 6}))
        # Wednesday = 2, not blocked
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z")
        assert len(conflicts) == 0


class TestBlockTimeRange:
    def test_overlapping_time_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Block 12:00-13:00 (lunch)
        rule_repo.create(_rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}))
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T12:30:00Z", "2026-03-18T13:20:00Z")
        assert len(conflicts) == 1
        assert "blocked time range" in conflicts[0].message.lower()

    def test_non_overlapping_time_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}))
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z")
        assert len(conflicts) == 0

    def test_adjacent_not_overlapping(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}))
        # Starts exactly when block ends — should NOT conflict
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T13:00:00Z", "2026-03-18T13:50:00Z")
        assert len(conflicts) == 0


class TestMaxPerDay:
    def test_under_max(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 3}))
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z", appt_id="a1"))
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T11:00:00Z", "2026-03-18T11:50:00Z")
        assert len(conflicts) == 0

    def test_at_max(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 2}))
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z", appt_id="a1"))
        appt_repo.create(_appt("2026-03-18T11:00:00Z", "2026-03-18T11:50:00Z", appt_id="a2"))
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T14:00:00Z", "2026-03-18T14:50:00Z")
        assert len(conflicts) == 1
        assert "maximum" in conflicts[0].message.lower()

    def test_cancelled_not_counted(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 1}))
        appt_repo.create(
            _appt(
                "2026-03-18T10:00:00Z",
                "2026-03-18T10:50:00Z",
                appt_id="a1",
                status=AppointmentStatus.CANCELLED,
            )
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T11:00:00Z", "2026-03-18T11:50:00Z")
        assert len(conflicts) == 0


class TestBufferBefore:
    def test_violates_buffer(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_rule(RuleType.BUFFER_BEFORE, {"minutes": 15}))
        # Existing appointment ends at 10:50
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"))
        # New starts at 10:55 — only 5min gap, needs 15min buffer
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T10:55:00Z", "2026-03-18T11:45:00Z")
        assert len(conflicts) == 1
        assert "buffer" in conflicts[0].message.lower()

    def test_respects_buffer(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_rule(RuleType.BUFFER_BEFORE, {"minutes": 10}))
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"))
        # New starts at 11:05 — 15min gap, needs 10min buffer
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T11:05:00Z", "2026-03-18T11:55:00Z")
        assert len(conflicts) == 0


class TestBufferAfter:
    def test_violates_buffer(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 15}))
        # Existing appointment starts at 11:00
        appt_repo.create(_appt("2026-03-18T11:00:00Z", "2026-03-18T11:50:00Z"))
        # New ends at 10:55 — only 5min gap before next, needs 15min buffer after
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:55:00Z")
        assert len(conflicts) == 1
        assert "buffer" in conflicts[0].message.lower()

    def test_respects_buffer(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 10}))
        appt_repo.create(_appt("2026-03-18T11:00:00Z", "2026-03-18T11:50:00Z"))
        # New ends at 10:45 — 15min gap, needs 10min buffer after
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T09:55:00Z", "2026-03-18T10:45:00Z")
        assert len(conflicts) == 0


class TestBlockDateRange:
    def test_within_blocked_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_DATE_RANGE, {"start_date": "2026-03-20", "end_date": "2026-03-25"})
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-22T10:00:00Z", "2026-03-22T10:50:00Z")
        assert len(conflicts) == 1
        assert "blocked range" in conflicts[0].message.lower()

    def test_outside_blocked_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_DATE_RANGE, {"start_date": "2026-03-20", "end_date": "2026-03-25"})
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z")
        assert len(conflicts) == 0


class TestBlockSpecificDates:
    def test_blocked_date(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_SPECIFIC_DATES, {"dates": ["2026-03-18", "2026-03-25"]})
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z")
        assert len(conflicts) == 1
        assert "specifically blocked" in conflicts[0].message.lower()

    def test_unblocked_date(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_SPECIFIC_DATES, {"dates": ["2026-03-18", "2026-03-25"]})
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-19T10:00:00Z", "2026-03-19T10:50:00Z")
        assert len(conflicts) == 0


class TestFreeSlots:
    def test_basic_free_slots(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Wednesday working hours 09:00-12:00 (3 hours)
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "12:00"})
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 60)
        assert len(slots) == 3
        assert slots[0].start == "2026-03-18T09:00:00Z"
        assert slots[0].end == "2026-03-18T10:00:00Z"
        assert slots[2].start == "2026-03-18T11:00:00Z"
        assert slots[2].end == "2026-03-18T12:00:00Z"

    def test_free_slots_with_existing_appointment(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "12:00"})
        )
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"))
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        # 09:00-09:50, gap during 10:00-10:50, then 10:50-11:40, 11:10 is too late for a 50
        starts = [s.start for s in slots]
        assert "2026-03-18T09:00:00Z" in starts
        # No slot should start inside the existing appointment
        for s in slots:
            assert not (s.start >= "2026-03-18T10:00:00Z" and s.start < "2026-03-18T10:50:00Z")

    def test_no_slots_on_blocked_day(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.BLOCK_DAY_OF_WEEK, {"day_of_week": 2}, rule_id="r2"))
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert len(slots) == 0

    def test_no_slots_on_blocked_date(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(
            _rule(
                RuleType.BLOCK_SPECIFIC_DATES,
                {"dates": ["2026-03-18"]},
                rule_id="r2",
            )
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert len(slots) == 0

    def test_free_slots_with_blocked_time_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "13:00"},
                rule_id="r1",
            )
        )
        # Block lunch 12:00-13:00
        rule_repo.create(
            _rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}, rule_id="r2")
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 60)
        # Should get 09:00-10:00, 10:00-11:00, 11:00-12:00 (not 12:00-13:00)
        assert len(slots) == 3
        starts = [s.start for s in slots]
        assert "2026-03-18T12:00:00Z" not in starts

    def test_max_per_day_limits_slots(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 2}, rule_id="r2"))
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z", appt_id="a1"))
        # 1 existing appointment, max 2 => only 1 more slot returned
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert len(slots) == 1

    def test_max_per_day_fully_booked(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 1}, rule_id="r2"))
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z", appt_id="a1"))
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert len(slots) == 0

    def test_no_working_hours_returns_empty(self, engine: AvailabilityEngine) -> None:
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert len(slots) == 0

    def test_free_slots_with_buffers(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "12:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.BUFFER_BEFORE, {"minutes": 10}, rule_id="r2"))
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 10}, rule_id="r3"))
        appt_repo.create(_appt("2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"))
        # With 10min buffer, blocked = 09:50-11:00
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        for s in slots:
            assert s.end <= "2026-03-18T09:50:00Z" or s.start >= "2026-03-18T11:00:00Z"

    def test_blocked_date_range_returns_empty(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(
            _rule(
                RuleType.BLOCK_DATE_RANGE,
                {"start_date": "2026-03-16", "end_date": "2026-03-20"},
                rule_id="r2",
            )
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert len(slots) == 0


class TestMultipleRulesInteraction:
    def test_working_hours_and_block_time(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(
            _rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}, rule_id="r2")
        )
        # 09:00 within working hours and outside blocked range — no conflict
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T09:00:00Z", "2026-03-18T09:50:00Z")
        assert len(conflicts) == 0

        # 12:30 within working hours but inside blocked range — 1 conflict
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T12:30:00Z", "2026-03-18T13:20:00Z")
        assert len(conflicts) == 1
        assert conflicts[0].rule.rule_type == RuleType.BLOCK_TIME_RANGE

    def test_multiple_conflicts_returned(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        engine: AvailabilityEngine,
    ) -> None:
        # Block Sunday
        rule_repo.create(_rule(RuleType.BLOCK_DAY_OF_WEEK, {"day_of_week": 6}, rule_id="r1"))
        # Block specific date
        rule_repo.create(
            _rule(RuleType.BLOCK_SPECIFIC_DATES, {"dates": ["2026-03-22"]}, rule_id="r2")
        )
        # Sunday 2026-03-22 hits both rules
        conflicts = engine.check_conflicts(USER_ID, "2026-03-22T10:00:00Z", "2026-03-22T10:50:00Z")
        assert len(conflicts) == 2

    def test_soft_enforcement_still_reported(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.BLOCK_TIME_RANGE,
                {"start": "12:00", "end": "13:00"},
                enforcement=EnforcementLevel.SOFT,
            )
        )
        conflicts = engine.check_conflicts(USER_ID, "2026-03-18T12:30:00Z", "2026-03-18T13:20:00Z")
        assert len(conflicts) == 1
        assert conflicts[0].enforcement == EnforcementLevel.SOFT

    def test_multiple_working_hour_ranges(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Split working hours: morning 09:00-12:00, afternoon 13:00-17:00
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "12:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "13:00", "end": "17:00"},
                rule_id="r2",
            )
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 60)
        starts = [s.start for s in slots]
        assert "2026-03-18T09:00:00Z" in starts
        assert "2026-03-18T13:00:00Z" in starts
        # 12:00-13:00 gap should not produce a slot
        assert "2026-03-18T12:00:00Z" not in starts
