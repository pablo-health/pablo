# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for AvailabilityEngine using InMemory repositories."""

from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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
        created_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
    )


def _parse_dt(val: str | datetime) -> datetime:
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(val.replace("Z", "+00:00"))


def _appt(
    start_at: str | datetime,
    end_at: str | datetime,
    *,
    appt_id: str = "appt-1",
    status: str = AppointmentStatus.CONFIRMED,
) -> Appointment:
    return Appointment(
        id=appt_id,
        user_id=USER_ID,
        patient_id="patient-1",
        title="Session",
        start_at=_parse_dt(start_at),
        end_at=_parse_dt(end_at),
        duration_minutes=50,
        status=status,
        session_type="individual",
        created_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 0

    def test_conflict_outside_hours(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T08:00:00Z", "2026-03-18T08:50:00Z"
        ).conflicts
        assert len(conflicts) == 1
        assert "working hours" in conflicts[0].message.lower()

    def test_conflict_ends_after_hours(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T16:30:00Z", "2026-03-18T17:20:00Z"
        ).conflicts
        assert len(conflicts) == 1

    def test_no_conflict_different_day(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Working hours rule only applies to Wednesday (2)
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        # Thursday = weekday 3, no rule defined
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-19T10:00:00Z", "2026-03-19T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 0


class TestBlockDayOfWeek:
    def test_blocked_day(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Block Sunday (6)
        rule_repo.create(_rule(RuleType.BLOCK_DAY_OF_WEEK, {"day_of_week": 6}))
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-22T10:00:00Z", "2026-03-22T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 1
        assert "blocked" in conflicts[0].message.lower()

    def test_unblocked_day(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_rule(RuleType.BLOCK_DAY_OF_WEEK, {"day_of_week": 6}))
        # Wednesday = 2, not blocked
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 0


class TestBlockTimeRange:
    def test_overlapping_time_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Block 12:00-13:00 (lunch)
        rule_repo.create(_rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}))
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T12:30:00Z", "2026-03-18T13:20:00Z"
        ).conflicts
        assert len(conflicts) == 1
        assert "blocked time range" in conflicts[0].message.lower()

    def test_non_overlapping_time_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}))
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 0

    def test_adjacent_not_overlapping(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}))
        # Starts exactly when block ends — should NOT conflict
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T13:00:00Z", "2026-03-18T13:50:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T11:00:00Z", "2026-03-18T11:50:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T14:00:00Z", "2026-03-18T14:50:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T11:00:00Z", "2026-03-18T11:50:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T10:55:00Z", "2026-03-18T11:45:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T11:05:00Z", "2026-03-18T11:55:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:55:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T09:55:00Z", "2026-03-18T10:45:00Z"
        ).conflicts
        assert len(conflicts) == 0


class TestBlockDateRange:
    def test_within_blocked_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_DATE_RANGE, {"start_date": "2026-03-20", "end_date": "2026-03-25"})
        )
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-22T10:00:00Z", "2026-03-22T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 1
        assert "blocked range" in conflicts[0].message.lower()

    def test_outside_blocked_range(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_DATE_RANGE, {"start_date": "2026-03-20", "end_date": "2026-03-25"})
        )
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 0


class TestBlockSpecificDates:
    def test_blocked_date(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_SPECIFIC_DATES, {"dates": ["2026-03-18", "2026-03-25"]})
        )
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 1
        assert "specifically blocked" in conflicts[0].message.lower()

    def test_unblocked_date(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.BLOCK_SPECIFIC_DATES, {"dates": ["2026-03-18", "2026-03-25"]})
        )
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-19T10:00:00Z", "2026-03-19T10:50:00Z"
        ).conflicts
        assert len(conflicts) == 0


class TestFreeSlots:
    def test_basic_free_slots(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        # Wednesday working hours 09:00-12:00 (3 hours)
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "12:00"})
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 60).slots
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 60).slots
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
        assert len(slots) == 0

    def test_no_rules_is_unconfigured_not_empty(self, engine: AvailabilityEngine) -> None:
        result = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert result.configured is False
        assert result.slots == []

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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
        assert len(slots) == 0

    def test_no_buffer_or_session_defaults_matches_back_to_back_enumeration(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
        starts = [s.start for s in slots]
        assert starts == [
            f"2026-03-18T{h:02d}:{m:02d}:00Z"
            for h, m in [
                (9, 0),
                (9, 50),
                (10, 40),
                (11, 30),
                (12, 20),
                (13, 10),
                (14, 0),
                (14, 50),
                (15, 40),
            ]
        ]

    def test_alignment_none_matches_back_to_back_enumeration(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.SESSION_DEFAULTS, {"alignment": "none"}, rule_id="r2"))
        without_rule = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        starts_with = [s.start for s in without_rule.slots]
        assert starts_with == [
            f"2026-03-18T{h:02d}:{m:02d}:00Z"
            for h, m in [
                (9, 0),
                (9, 50),
                (10, 40),
                (11, 30),
                (12, 20),
                (13, 10),
                (14, 0),
                (14, 50),
                (15, 40),
            ]
        ]


class TestSessionDefaults:
    def test_motivating_case_length_break_hour_alignment(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 10}, rule_id="r2"))
        rule_repo.create(
            _rule(
                RuleType.SESSION_DEFAULTS,
                {"duration_minutes": 50, "alignment": "hour"},
                rule_id="r3",
            )
        )
        slots = engine.get_free_slots(USER_ID, "2026-03-18", None).slots
        starts = [s.start for s in slots]
        assert starts == [f"2026-03-18T{h:02d}:00:00Z" for h in range(9, 17)]
        assert "2026-03-18T09:50:00Z" not in starts
        assert "2026-03-18T10:40:00Z" not in starts

    def test_break_after_booking_without_alignment(
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
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 10}, rule_id="r2"))
        appt_repo.create(_appt("2026-03-18T09:00:00Z", "2026-03-18T09:50:00Z"))
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
        starts = [s.start for s in slots]
        assert starts[0] == "2026-03-18T10:00:00Z"
        assert "2026-03-18T09:50:00Z" not in starts

    def test_break_after_booking_with_hour_alignment(
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
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 10}, rule_id="r2"))
        rule_repo.create(_rule(RuleType.SESSION_DEFAULTS, {"alignment": "hour"}, rule_id="r3"))
        appt_repo.create(_appt("2026-03-18T09:00:00Z", "2026-03-18T09:50:00Z"))
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
        starts = [s.start for s in slots]
        assert starts[0] == "2026-03-18T10:00:00Z"
        assert starts[1] == "2026-03-18T11:00:00Z"

    def test_alignment_edge_at_window_start_hour(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:30", "end": "12:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.SESSION_DEFAULTS, {"alignment": "hour"}, rule_id="r2"))
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
        assert slots[0].start == "2026-03-18T10:00:00Z"
        assert "2026-03-18T09:30:00Z" not in [s.start for s in slots]

    def test_alignment_edge_at_window_start_half_hour(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:30", "end": "12:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.SESSION_DEFAULTS, {"alignment": "half_hour"}, rule_id="r2"))
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 50).slots
        assert slots[0].start == "2026-03-18T09:30:00Z"

    def test_default_length_fallback(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "12:00"},
                rule_id="r1",
            )
        )
        rule_repo.create(_rule(RuleType.SESSION_DEFAULTS, {"duration_minutes": 60}, rule_id="r2"))
        result = engine.get_free_slots(USER_ID, "2026-03-18", None)
        assert result.duration_minutes == 60
        assert result.slots[0].end == "2026-03-18T10:00:00Z"

        explicit = engine.get_free_slots(USER_ID, "2026-03-18", 30)
        assert explicit.duration_minutes == 30
        assert explicit.slots[0].end == "2026-03-18T09:30:00Z"

    def test_default_length_falls_back_to_fifty_with_no_rule(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "12:00"},
                rule_id="r1",
            )
        )
        result = engine.get_free_slots(USER_ID, "2026-03-18", None)
        assert result.duration_minutes == 50

    def test_session_defaults_does_not_affect_conflict_checking(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 2, "start": "09:00", "end": "17:00"},
                rule_id="r1",
            )
        )
        without_result = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        )
        rule_repo.create(
            _rule(
                RuleType.SESSION_DEFAULTS,
                {"duration_minutes": 50, "alignment": "hour"},
                rule_id="r2",
            )
        )
        with_result = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        )
        assert len(without_result.conflicts) == len(with_result.conflicts) == 0


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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T09:00:00Z", "2026-03-18T09:50:00Z"
        ).conflicts
        assert len(conflicts) == 0

        # 12:30 within working hours but inside blocked range — 1 conflict
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T12:30:00Z", "2026-03-18T13:20:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-22T10:00:00Z", "2026-03-22T10:50:00Z"
        ).conflicts
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
        conflicts = engine.check_conflicts(
            USER_ID, "2026-03-18T12:30:00Z", "2026-03-18T13:20:00Z"
        ).conflicts
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
        slots = engine.get_free_slots(USER_ID, "2026-03-18", 60).slots
        starts = [s.start for s in slots]
        assert "2026-03-18T09:00:00Z" in starts
        assert "2026-03-18T13:00:00Z" in starts
        # 12:00-13:00 gap should not produce a slot
        assert "2026-03-18T12:00:00Z" not in starts


class TestConfiguredSentinel:
    """A practice's rule set is either not configured, or configured.

    Zero rules means nothing has been asserted about availability — not
    "closed" and not "open". ``get_free_slots`` and ``check_conflicts``
    must agree on that: neither one may render an unconfigured practice
    as unavailable.
    """

    def test_unconfigured_practice(self, engine: AvailabilityEngine) -> None:
        slots_result = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert slots_result.configured is False
        assert slots_result.slots == []

        conflicts_result = engine.check_conflicts(
            USER_ID, "2026-03-18T10:00:00Z", "2026-03-18T10:50:00Z"
        )
        assert conflicts_result.configured is False
        assert conflicts_result.conflicts == []

    def test_configured_practice_full_day(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        # Working hours are set, but the whole day is booked solid.
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "10:00"})
        )
        appt_repo.create(_appt("2026-03-18T09:00:00Z", "2026-03-18T10:00:00Z"))
        slots_result = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert slots_result.configured is True
        assert slots_result.slots == []

    def test_configured_practice_with_openings(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "10:00"})
        )
        slots_result = engine.get_free_slots(USER_ID, "2026-03-18", 50)
        assert slots_result.configured is True
        assert len(slots_result.slots) == 1


NY = ZoneInfo("America/New_York")


class TestTimezoneAwareRuleEvaluation:
    """A clinician's rules are configured in their own zone, not UTC.

    2026-08-26 is a Wednesday and falls in EDT (UTC-4); 2026-11-04 is a
    Wednesday after the fall DST change and falls in EST (UTC-5). Every
    case here is paired with the same call minus ``tz`` to pin down that
    the zone, and nothing else, is what flips the outcome.
    """

    def test_working_hours_conflict_flips_with_timezone(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )

        # 19:00 UTC = 15:00 EDT — inside working hours in NY, outside in UTC.
        ny_evening = engine.check_conflicts(
            USER_ID, "2026-08-26T19:00:00Z", "2026-08-26T19:50:00Z", tz=NY
        ).conflicts
        utc_evening = engine.check_conflicts(
            USER_ID, "2026-08-26T19:00:00Z", "2026-08-26T19:50:00Z"
        ).conflicts
        assert ny_evening == []
        assert len(utc_evening) == 1

        # 12:00 UTC = 08:00 EDT — outside working hours in NY, inside in UTC.
        ny_morning = engine.check_conflicts(
            USER_ID, "2026-08-26T12:00:00Z", "2026-08-26T12:50:00Z", tz=NY
        ).conflicts
        utc_morning = engine.check_conflicts(
            USER_ID, "2026-08-26T12:00:00Z", "2026-08-26T12:50:00Z"
        ).conflicts
        assert len(ny_morning) == 1
        assert utc_morning == []

    def test_free_slots_local_day_with_dst(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )

        # Before the fall-back: 09:00 EDT == 13:00Z.
        summer_slots = engine.get_free_slots(USER_ID, "2026-08-26", 50, tz=NY).slots
        assert summer_slots[0].start == "2026-08-26T13:00:00Z"
        assert all(s.end <= "2026-08-26T21:00:00Z" for s in summer_slots)

        # After the fall-back: 09:00 EST == 14:00Z.
        winter_slots = engine.get_free_slots(USER_ID, "2026-11-04", 50, tz=NY).slots
        assert winter_slots[0].start == "2026-11-04T14:00:00Z"

    def test_local_day_window_for_max_per_day(
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
        rule_repo.create(
            _rule(
                RuleType.WORKING_HOURS,
                {"day_of_week": 3, "start": "09:00", "end": "17:00"},
                rule_id="r2",
            )
        )
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 1}, rule_id="r3"))
        # 2026-08-27T02:00:00Z is 22:00 EDT on the 26th, not the 27th.
        appt_repo.create(_appt("2026-08-27T02:00:00Z", "2026-08-27T02:50:00Z"))

        # tz=NY: the appointment counts against the 26th local day, so a
        # second booking that day is refused by max_per_day.
        conflicts = engine.check_conflicts(
            USER_ID, "2026-08-26T13:00:00Z", "2026-08-26T13:50:00Z", tz=NY
        ).conflicts
        assert len(conflicts) == 1
        assert "maximum" in conflicts[0].message.lower()

        # The 27th local day is untouched by it.
        slots_27_ny = engine.get_free_slots(USER_ID, "2026-08-27", 50, tz=NY).slots
        assert len(slots_27_ny) > 0

        # With the UTC default, the same appointment lands on the 27th
        # instead, so it fully consumes that day's single slot.
        slots_27_utc = engine.get_free_slots(USER_ID, "2026-08-27", 50).slots
        assert slots_27_utc == []

    def test_block_day_of_week_is_local(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_rule(RuleType.BLOCK_DAY_OF_WEEK, {"day_of_week": 5}))  # Saturday

        # 2026-08-30T02:00:00Z is Saturday 22:00 EDT, but Sunday in UTC.
        ny_conflicts = engine.check_conflicts(
            USER_ID, "2026-08-30T02:00:00Z", "2026-08-30T02:50:00Z", tz=NY
        ).conflicts
        utc_conflicts = engine.check_conflicts(
            USER_ID, "2026-08-30T02:00:00Z", "2026-08-30T02:50:00Z"
        ).conflicts
        assert len(ny_conflicts) == 1
        assert utc_conflicts == []


class TestNaiveInputIsHostIndependent:
    """A datetime with no offset is read as wall-clock in ``tz``.

    ``datetime.astimezone()`` resolves a naive value against the *host's*
    timezone, so evaluating one that way would give a different answer on a
    UTC container than on a laptop in Chicago — a bug that passes CI and
    fails in front of a clinician. These cases pin the reading to ``tz``.
    """

    def _conflicts_under_host_tz(self, engine: AvailabilityEngine, host_tz: str) -> list[object]:
        """Run a naive-input rule check with the process pinned to ``host_tz``."""
        previous = os.environ.get("TZ")
        os.environ["TZ"] = host_tz
        time.tzset()
        try:
            # No offset: "15:00 on Wednesday the 26th", the practice's clock.
            return list(
                engine.check_conflicts(
                    USER_ID, "2026-08-26T15:00:00", "2026-08-26T15:50:00", tz=NY
                ).conflicts
            )
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

    def test_naive_reading_does_not_move_with_the_host_timezone(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )

        # 15:00 is inside 09:00-17:00 whatever the host clock says.
        assert self._conflicts_under_host_tz(engine, "UTC") == []
        assert self._conflicts_under_host_tz(engine, "America/Chicago") == []
        assert self._conflicts_under_host_tz(engine, "Asia/Tokyo") == []

    def test_naive_reading_is_the_practice_clock_not_utc(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        """08:00 local is outside working hours even though 08:00Z is inside."""
        rule_repo.create(
            _rule(RuleType.WORKING_HOURS, {"day_of_week": 2, "start": "09:00", "end": "17:00"})
        )

        naive_early = engine.check_conflicts(
            USER_ID, "2026-08-26T08:00:00", "2026-08-26T08:50:00", tz=NY
        ).conflicts
        assert len(naive_early) == 1
