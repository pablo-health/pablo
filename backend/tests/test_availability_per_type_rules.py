# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Availability rules scoped to one appointment type.

Every date here is 2026-03-18, a Wednesday (weekday 2), and every practice
below keeps 09:00-17:00 on that day unless a test says otherwise.
"""

from __future__ import annotations

import itertools
from datetime import datetime

import pytest
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import InMemoryAvailabilityRuleRepository
from app.scheduling_engine.services.availability import AvailabilityEngine

USER_ID = "user-1"
WEDNESDAY = "2026-03-18"
INTAKE = "type-intake"
FOLLOW_UP = "type-follow-up"
GROUP = "type-group"

_CREATED = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
_ids = itertools.count()


def _rule(
    rule_type: str,
    params: dict[str, object],
    *,
    enforcement: str = EnforcementLevel.HARD,
    appointment_type_id: str | None = None,
    allow_other_types: bool = True,
) -> AvailabilityRule:
    return AvailabilityRule(
        id=f"rule-{next(_ids)}",
        user_id=USER_ID,
        rule_type=rule_type,
        enforcement=enforcement,
        params=params,
        appointment_type_id=appointment_type_id,
        allow_other_types=allow_other_types,
        created_at=_CREATED,
    )


def _hours(
    start: str,
    end: str,
    *,
    appointment_type_id: str | None = None,
    allow_other_types: bool = True,
    enforcement: str = EnforcementLevel.HARD,
) -> AvailabilityRule:
    return _rule(
        RuleType.WORKING_HOURS,
        {"day_of_week": 2, "start": start, "end": end},
        enforcement=enforcement,
        appointment_type_id=appointment_type_id,
        allow_other_types=allow_other_types,
    )


def _appt(hour: int, *, appointment_type_id: str | None, status: str = AppointmentStatus.CONFIRMED):
    return Appointment(
        id=f"appt-{next(_ids)}",
        user_id=USER_ID,
        patient_id="patient-1",
        title="Session",
        start_at=datetime.fromisoformat(f"{WEDNESDAY}T{hour:02d}:00:00+00:00"),
        end_at=datetime.fromisoformat(f"{WEDNESDAY}T{hour:02d}:50:00+00:00"),
        duration_minutes=50,
        status=status,
        session_type="individual",
        appointment_type_id=appointment_type_id,
        created_at=_CREATED,
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


def _starts(engine: AvailabilityEngine, appointment_type_id: str | None) -> list[str]:
    result = engine.get_free_slots(USER_ID, WEDNESDAY, 60, appointment_type_id=appointment_type_id)
    return [s.start[11:16] for s in result.slots]


class TestScopingIsOptIn:
    """None means every type, which is what every rule written so far means."""

    def test_naming_a_type_changes_nothing_for_an_unscoped_rule_set(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(_rule(RuleType.BLOCK_TIME_RANGE, {"start": "12:00", "end": "13:00"}))

        assert _starts(engine, INTAKE) == _starts(engine, None)

    def test_a_rule_scoped_to_another_type_does_not_apply(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(
            _rule(
                RuleType.BLOCK_TIME_RANGE,
                {"start": "12:00", "end": "13:00"},
                appointment_type_id=INTAKE,
            )
        )

        assert "12:00" not in _starts(engine, INTAKE)
        assert "12:00" in _starts(engine, FOLLOW_UP)


class TestMaxPerDayPerType:
    """At most two intakes a day, without a rule type to say it."""

    def test_type_scoped_cap_counts_only_that_type(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 2}, appointment_type_id=INTAKE))
        appt_repo.create(_appt(9, appointment_type_id=INTAKE))
        appt_repo.create(_appt(10, appointment_type_id=INTAKE))
        appt_repo.create(_appt(11, appointment_type_id=FOLLOW_UP))

        assert _starts(engine, INTAKE) == []
        assert _starts(engine, FOLLOW_UP) != []

    def test_practice_wide_cap_keeps_counting_every_type(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 3}))
        appt_repo.create(_appt(9, appointment_type_id=INTAKE))
        appt_repo.create(_appt(10, appointment_type_id=FOLLOW_UP))
        appt_repo.create(_appt(11, appointment_type_id=GROUP))

        assert _starts(engine, INTAKE) == []
        assert _starts(engine, FOLLOW_UP) == []
        assert _starts(engine, None) == []

    def test_both_caps_apply_and_an_intake_still_takes_a_slot_in_the_day(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 4}))
        rule_repo.create(_rule(RuleType.MAX_PER_DAY, {"max": 2}, appointment_type_id=INTAKE))
        appt_repo.create(_appt(9, appointment_type_id=INTAKE))
        appt_repo.create(_appt(10, appointment_type_id=INTAKE))
        appt_repo.create(_appt(11, appointment_type_id=FOLLOW_UP))

        # Intakes are done for the day; the practice is not.
        assert _starts(engine, INTAKE) == []
        assert _starts(engine, FOLLOW_UP) != []

        # The fourth appointment of any kind closes the day for everyone,
        # the two intakes very much included in the count.
        appt_repo.create(_appt(13, appointment_type_id=FOLLOW_UP))
        assert _starts(engine, FOLLOW_UP) == []

    def test_soft_type_cap_offers_slots_marked_over_cap(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(
            _rule(
                RuleType.MAX_PER_DAY,
                {"max": 1},
                enforcement=EnforcementLevel.SOFT,
                appointment_type_id=INTAKE,
            )
        )
        appt_repo.create(_appt(9, appointment_type_id=INTAKE))

        intakes = engine.get_free_slots(USER_ID, WEDNESDAY, 60, appointment_type_id=INTAKE)
        assert intakes.slots
        assert all(s.over_cap for s in intakes.slots)

        follow_ups = engine.get_free_slots(USER_ID, WEDNESDAY, 60, appointment_type_id=FOLLOW_UP)
        assert not any(s.over_cap for s in follow_ups.slots)


class TestPrecedence:
    """What happens when a practice-wide and a type-scoped rule both apply."""

    def test_working_hours_intersect(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(_hours("13:00", "19:00", appointment_type_id=INTAKE))

        # 13:00-17:00: the type narrows the practice's hours and cannot
        # reach past them, so nothing is offered at 17:00 or 18:00.
        assert _starts(engine, INTAKE) == ["13:00", "14:00", "15:00", "16:00"]
        assert _starts(engine, FOLLOW_UP)[0] == "09:00"

    def test_a_type_window_on_a_day_the_practice_is_shut_offers_nothing(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        thursday_only = {"day_of_week": 3, "start": "09:00", "end": "17:00"}
        rule_repo.create(_rule(RuleType.WORKING_HOURS, thursday_only))
        rule_repo.create(_hours("09:00", "17:00", appointment_type_id=INTAKE))

        assert _starts(engine, INTAKE) == []

    def test_the_larger_buffer_wins(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 10}))
        rule_repo.create(_rule(RuleType.BUFFER_AFTER, {"minutes": 30}, appointment_type_id=INTAKE))

        assert _starts(engine, INTAKE)[:2] == ["09:00", "10:30"]
        assert _starts(engine, FOLLOW_UP)[:2] == ["09:00", "10:10"]


class TestExclusiveWindows:
    """allow_other_types=False turns a type's window into a claim."""

    def test_allow_other_types_defaults_true_and_takes_nothing_away(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        before = _starts(engine, FOLLOW_UP)
        rule_repo.create(_hours("09:00", "12:00", appointment_type_id=INTAKE))

        assert _starts(engine, FOLLOW_UP) == before

    def test_hard_claim_removes_the_window_from_every_other_type(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(
            _hours("09:00", "12:00", appointment_type_id=INTAKE, allow_other_types=False)
        )

        assert _starts(engine, INTAKE) == ["09:00", "10:00", "11:00"]
        assert _starts(engine, FOLLOW_UP) == ["12:00", "13:00", "14:00", "15:00", "16:00"]

    def test_an_untyped_listing_loses_every_claimed_window(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(
            _hours("09:00", "12:00", appointment_type_id=INTAKE, allow_other_types=False)
        )

        # Nothing here promises the slot will be used for an intake.
        assert _starts(engine, None) == ["12:00", "13:00", "14:00", "15:00", "16:00"]

    def test_soft_claim_offers_the_window_marked_rather_than_removing_it(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(
            _hours(
                "09:00",
                "12:00",
                appointment_type_id=INTAKE,
                allow_other_types=False,
                enforcement=EnforcementLevel.SOFT,
            )
        )

        result = engine.get_free_slots(USER_ID, WEDNESDAY, 60, appointment_type_id=FOLLOW_UP)
        marked = {s.start[11:16]: s.over_cap for s in result.slots}
        assert marked["09:00"] is True
        assert marked["11:00"] is True
        assert marked["12:00"] is False

    def test_overlapping_claims_are_lost_to_both_types(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(
            _hours("09:00", "12:00", appointment_type_id=INTAKE, allow_other_types=False)
        )
        rule_repo.create(
            _hours("11:00", "14:00", appointment_type_id=GROUP, allow_other_types=False)
        )

        # 11:00-12:00 is claimed twice and offered to neither — each claim
        # subtracts from the other, whichever was written first.
        assert _starts(engine, INTAKE) == ["09:00", "10:00"]
        assert _starts(engine, GROUP) == ["12:00", "13:00"]
        assert _starts(engine, FOLLOW_UP) == ["14:00", "15:00", "16:00"]

    def test_a_booking_made_before_the_claim_is_left_alone(
        self,
        rule_repo: InMemoryAvailabilityRuleRepository,
        appt_repo: InMemoryAppointmentRepository,
        engine: AvailabilityEngine,
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        booked = appt_repo.create(_appt(10, appointment_type_id=FOLLOW_UP))

        rule_repo.create(
            _hours("09:00", "12:00", appointment_type_id=INTAKE, allow_other_types=False)
        )

        # The appointment is untouched, and the booking-side check still
        # says it is fine: a claim governs future listing, never the diary
        # as it already stands.
        still_there = appt_repo.get(booked.id, USER_ID)
        assert still_there is not None
        assert still_there.status == AppointmentStatus.CONFIRMED
        assert (
            engine.check_conflicts(
                USER_ID, f"{WEDNESDAY}T10:00:00Z", f"{WEDNESDAY}T10:50:00Z"
            ).conflicts
            == []
        )


class TestCreationWarnings:
    """A practice should not find out it locked itself out from an empty week."""

    def test_a_claim_that_swallows_the_day_warns(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        claim = _hours("09:00", "17:00", appointment_type_id=INTAKE, allow_other_types=False)

        warnings = engine.exclusivity_warnings(claim)
        assert any("no Wednesday availability" in w for w in warnings)

    def test_a_claim_overlapping_another_type_s_claim_warns(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        rule_repo.create(
            _hours("11:00", "14:00", appointment_type_id=GROUP, allow_other_types=False)
        )
        claim = _hours("09:00", "12:00", appointment_type_id=INTAKE, allow_other_types=False)

        warnings = engine.exclusivity_warnings(claim)
        assert any("overlapping minutes" in w for w in warnings)
        assert not any("no Wednesday availability" in w for w in warnings)

    def test_a_claim_that_leaves_room_warns_about_nothing(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))
        claim = _hours("09:00", "12:00", appointment_type_id=INTAKE, allow_other_types=False)

        assert engine.exclusivity_warnings(claim) == []

    def test_an_ordinary_rule_warns_about_nothing(
        self, rule_repo: InMemoryAvailabilityRuleRepository, engine: AvailabilityEngine
    ) -> None:
        rule_repo.create(_hours("09:00", "17:00"))

        assert engine.exclusivity_warnings(_rule(RuleType.MAX_PER_DAY, {"max": 2})) == []
