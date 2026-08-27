# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for SchedulingService using InMemory repositories."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from app.scheduling_engine.exceptions import (
    AppointmentConflictError,
    AppointmentNotFoundError,
    InvalidAppointmentError,
    RuleViolationError,
)
from app.scheduling_engine.models.appointment import AppointmentStatus
from app.scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from app.scheduling_engine.models.conflict import ConflictCheckResult
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService

USER_ID = "user-1"
PATIENT_ID = "patient-1"


def _appt_data(**overrides: Any) -> dict[str, Any]:
    """Build appointment data dict with sensible defaults."""
    defaults: dict[str, Any] = {
        "patient_id": PATIENT_ID,
        "title": "Session",
        "start_at": datetime.fromisoformat("2026-03-20T14:00:00+00:00"),
        "end_at": datetime.fromisoformat("2026-03-20T14:50:00+00:00"),
        "duration_minutes": 50,
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def service(repo: InMemoryAppointmentRepository) -> SchedulingService:
    return SchedulingService(repo)


class TestCreateAppointment:
    def test_creates_appointment(self, service: SchedulingService) -> None:
        appt = service.create_appointment(USER_ID, data=_appt_data(title="Session with Patient"))
        assert appt.id
        assert appt.user_id == USER_ID
        assert appt.patient_id == PATIENT_ID
        assert appt.status == AppointmentStatus.CONFIRMED
        assert appt.session_type == "individual"
        assert appt.created_at

    def test_creates_with_optional_fields(self, service: SchedulingService) -> None:
        appt = service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Couples Session",
                session_type="couples",
                video_link="https://zoom.us/j/123",
                video_platform="zoom",
                notes="First session",
            ),
        )
        assert appt.session_type == "couples"
        assert appt.video_link == "https://zoom.us/j/123"
        assert appt.video_platform == "zoom"
        assert appt.notes == "First session"

    def test_rejects_empty_patient_id(self, service: SchedulingService) -> None:
        with pytest.raises(InvalidAppointmentError, match="patient_id"):
            service.create_appointment(USER_ID, data=_appt_data(patient_id=""))

    def test_rejects_invalid_duration(self, service: SchedulingService) -> None:
        with pytest.raises(InvalidAppointmentError, match="duration_minutes"):
            service.create_appointment(USER_ID, data=_appt_data(duration_minutes=0))

    def test_rejects_overlapping_appointment(self, service: SchedulingService) -> None:
        service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(AppointmentConflictError):
            service.create_appointment(USER_ID, data=_appt_data(patient_id="patient-2"))

    def test_accepts_back_to_back_appointment(self, service: SchedulingService) -> None:
        service.create_appointment(USER_ID, data=_appt_data())
        second = service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-2",
                start_at=datetime.fromisoformat("2026-03-20T14:50:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T15:40:00+00:00"),
            ),
        )
        assert second.id

    def test_rebooks_slot_freed_by_cancellation(
        self, service: SchedulingService, repo: InMemoryAppointmentRepository
    ) -> None:
        first = service.create_appointment(USER_ID, data=_appt_data())
        service.cancel_appointment(first.id, USER_ID)
        second = service.create_appointment(USER_ID, data=_appt_data(patient_id="patient-2"))
        assert second.id

    def test_overlap_check_is_scoped_to_user(self, service: SchedulingService) -> None:
        service.create_appointment("other-user", data=_appt_data())
        appt = service.create_appointment(USER_ID, data=_appt_data())
        assert appt.id


class TestGetAppointment:
    def test_gets_existing_appointment(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        fetched = service.get_appointment(created.id, USER_ID)
        assert fetched.id == created.id

    def test_raises_for_missing_appointment(self, service: SchedulingService) -> None:
        with pytest.raises(AppointmentNotFoundError):
            service.get_appointment("nonexistent", USER_ID)

    def test_raises_for_wrong_user(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(AppointmentNotFoundError):
            service.get_appointment(created.id, "other-user")


class TestListAppointments:
    def test_lists_in_range(self, service: SchedulingService) -> None:
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Monday",
                start_at=datetime.fromisoformat("2026-03-16T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-16T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Wednesday",
                start_at=datetime.fromisoformat("2026-03-18T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Next Monday",
                start_at=datetime.fromisoformat("2026-03-23T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-23T14:50:00+00:00"),
            ),
        )
        results = service.list_appointments(USER_ID, "2026-03-16T00:00:00Z", "2026-03-20T00:00:00Z")
        assert len(results) == 2
        assert results[0].title == "Monday"
        assert results[1].title == "Wednesday"

    def test_excludes_other_users(self, service: SchedulingService) -> None:
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Mine",
                start_at=datetime.fromisoformat("2026-03-18T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            "other-user",
            data=_appt_data(
                title="Theirs",
                start_at=datetime.fromisoformat("2026-03-18T15:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T15:50:00+00:00"),
            ),
        )
        results = service.list_appointments(USER_ID, "2026-03-18T00:00:00Z", "2026-03-19T00:00:00Z")
        assert len(results) == 1
        assert results[0].title == "Mine"


class TestUpdateAppointment:
    def test_updates_fields(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data(title="Original"))
        updated = service.update_appointment(
            created.id, USER_ID, title="Updated", notes="Added notes"
        )
        assert updated.title == "Updated"
        assert updated.notes == "Added notes"
        assert updated.updated_at is not None

    def test_rejects_disallowed_fields(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(InvalidAppointmentError, match="Cannot update field"):
            service.update_appointment(created.id, USER_ID, id="new-id")

    def test_marks_recurring_as_exception(
        self,
        repo: InMemoryAppointmentRepository,
        service: SchedulingService,
    ) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data(title="Weekly"))
        created.recurring_appointment_id = "master-id"
        repo.update(created)

        updated = service.update_appointment(created.id, USER_ID, title="Moved")
        assert updated.is_exception is True

    def test_rejects_move_onto_another_appointment(self, service: SchedulingService) -> None:
        service.create_appointment(USER_ID, data=_appt_data())
        second = service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-2",
                start_at=datetime.fromisoformat("2026-03-20T16:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T16:50:00+00:00"),
            ),
        )
        with pytest.raises(AppointmentConflictError):
            service.update_appointment(
                second.id,
                USER_ID,
                start_at=datetime.fromisoformat("2026-03-20T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T14:50:00+00:00"),
            )

    def test_allows_move_that_only_overlaps_itself(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        moved = service.update_appointment(
            created.id,
            USER_ID,
            start_at=datetime.fromisoformat("2026-03-20T14:10:00+00:00"),
            end_at=datetime.fromisoformat("2026-03-20T15:00:00+00:00"),
        )
        assert moved.start_at == datetime.fromisoformat("2026-03-20T14:10:00+00:00")

    def test_non_time_update_does_not_check_overlap(self, service: SchedulingService) -> None:
        """Mirrors the internal start-session call, which only ever attaches
        session_id: no time field is changing, so no collision check runs."""
        created = service.create_appointment(USER_ID, data=_appt_data())
        updated = service.update_appointment(created.id, USER_ID, session_id="session-1")
        assert updated.session_id == "session-1"


class TestCancelAppointment:
    def test_cancels_appointment(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        cancelled = service.cancel_appointment(created.id, USER_ID)
        assert cancelled.status == AppointmentStatus.CANCELLED

    def test_cancel_nonexistent_raises(self, service: SchedulingService) -> None:
        with pytest.raises(AppointmentNotFoundError):
            service.cancel_appointment("nonexistent", USER_ID)


class TestListPatientAppointments:
    def test_lists_by_patient(self, service: SchedulingService) -> None:
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-a",
                title="Session A",
                start_at=datetime.fromisoformat("2026-03-18T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-b",
                title="Session B",
                start_at=datetime.fromisoformat("2026-03-18T15:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T15:50:00+00:00"),
            ),
        )
        results = service.list_patient_appointments(USER_ID, "patient-a")
        assert len(results) == 1
        assert results[0].patient_id == "patient-a"


class TestCreateRecurring:
    def test_rejects_series_with_colliding_occurrence(
        self, service: SchedulingService, repo: InMemoryAppointmentRepository
    ) -> None:
        """A single occurrence colliding with an existing appointment fails
        the whole series — nothing from it is persisted."""
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-2",
                start_at=datetime.fromisoformat("2026-03-27T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-27T14:50:00+00:00"),
            ),
        )

        with pytest.raises(AppointmentConflictError):
            service.create_recurring(
                USER_ID,
                data=_appt_data(),
                recurrence={"frequency": "weekly", "timezone": "UTC", "count": 4},
            )

        remaining = repo.list_by_range(USER_ID, "2026-03-19T00:00:00Z", "2026-04-11T00:00:00Z")
        assert len(remaining) == 1, "only the pre-existing blocker should remain"

    def test_creates_series_with_no_collisions(self, service: SchedulingService) -> None:
        appointments = service.create_recurring(
            USER_ID,
            data=_appt_data(),
            recurrence={"frequency": "weekly", "timezone": "UTC", "count": 4},
        )
        assert len(appointments) == 4


@pytest.fixture
def rule_repo() -> InMemoryAvailabilityRuleRepository:
    return InMemoryAvailabilityRuleRepository()


@pytest.fixture
def gated_service(
    repo: InMemoryAppointmentRepository,
    rule_repo: InMemoryAvailabilityRuleRepository,
) -> SchedulingService:
    """A SchedulingService with a real AvailabilityEngine wired in."""
    return SchedulingService(repo, AvailabilityEngine(rule_repo, repo))


def _block_day_rule(
    day_of_week: int, *, enforcement: str = EnforcementLevel.HARD
) -> AvailabilityRule:
    # _appt_data() defaults to 2026-03-20, a Friday (weekday() == 4).
    return AvailabilityRule(
        id="rule-1",
        user_id=USER_ID,
        rule_type=RuleType.BLOCK_DAY_OF_WEEK,
        enforcement=enforcement,
        params={"day_of_week": day_of_week},
    )


class TestAvailabilityRuleEnforcement:
    def test_hard_rule_refuses_the_booking(
        self,
        gated_service: SchedulingService,
        rule_repo: InMemoryAvailabilityRuleRepository,
        repo: InMemoryAppointmentRepository,
    ) -> None:
        rule_repo.create(_block_day_rule(4))  # Friday — matches _appt_data()'s default

        with pytest.raises(RuleViolationError):
            gated_service.create_appointment(USER_ID, data=_appt_data())

        assert repo.list_by_range(USER_ID, "2026-03-19T00:00:00Z", "2026-03-21T00:00:00Z") == []

    def test_soft_rule_allows_and_returns_warning(
        self,
        gated_service: SchedulingService,
        rule_repo: InMemoryAvailabilityRuleRepository,
    ) -> None:
        rule_repo.create(_block_day_rule(4, enforcement=EnforcementLevel.SOFT))

        appt = gated_service.create_appointment(USER_ID, data=_appt_data())

        assert appt.id
        assert len(gated_service.rule_warnings) == 1

    def test_no_violation_succeeds_with_no_warnings(
        self,
        gated_service: SchedulingService,
        rule_repo: InMemoryAvailabilityRuleRepository,
    ) -> None:
        rule_repo.create(_block_day_rule(0))  # Monday — doesn't match

        appt = gated_service.create_appointment(USER_ID, data=_appt_data())

        assert appt.id
        assert gated_service.rule_warnings == []

    def test_user_with_no_rules_is_unaffected(self, gated_service: SchedulingService) -> None:
        appt = gated_service.create_appointment(USER_ID, data=_appt_data())

        assert appt.id
        assert gated_service.rule_warnings == []

    def test_engine_absent_skips_rule_checking(self, service: SchedulingService) -> None:
        """SchedulingService(repo) with no engine — the ~35 other tests in this
        file — must keep compiling and behaving exactly as before."""
        appt = service.create_appointment(USER_ID, data=_appt_data())

        assert appt.id
        assert service.rule_warnings == []

    def test_malformed_rule_does_not_raise(
        self,
        gated_service: SchedulingService,
        rule_repo: InMemoryAvailabilityRuleRepository,
    ) -> None:
        """A rule missing an expected params key is treated as non-blocking
        rather than surfacing a KeyError on the booking path."""
        rule_repo.create(
            AvailabilityRule(
                id="rule-1",
                user_id=USER_ID,
                rule_type=RuleType.WORKING_HOURS,
                enforcement=EnforcementLevel.HARD,
                params={"day_of_week": 4},  # missing "start"/"end"
            )
        )

        appt = gated_service.create_appointment(USER_ID, data=_appt_data())

        assert appt.id

    def test_update_checks_rules_only_when_time_changes(
        self,
        gated_service: SchedulingService,
        rule_repo: InMemoryAvailabilityRuleRepository,
    ) -> None:
        appt = gated_service.create_appointment(USER_ID, data=_appt_data())
        rule_repo.create(_block_day_rule(4))  # now blocks the appointment's own day

        # No time field touched — must not trip the rule (mirrors the
        # start-session internal update, which only ever sets session_id).
        updated = gated_service.update_appointment(appt.id, USER_ID, title="Renamed")
        assert updated.title == "Renamed"

        # Moving the time onto the (now-blocked) same day is refused.
        with pytest.raises(RuleViolationError):
            gated_service.update_appointment(
                appt.id,
                USER_ID,
                start_at=datetime.fromisoformat("2026-03-20T15:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T15:50:00+00:00"),
            )


class _RecordingAvailabilityEngine(AvailabilityEngine):
    """Records the ``tz`` it was called with instead of checking real rules."""

    def __init__(self) -> None:
        self.seen_tz: tzinfo | None = None

    def check_conflicts(
        self,
        user_id: str,
        start_at: str | datetime,
        end_at: str | datetime,
        *,
        tz: tzinfo = UTC,
    ) -> ConflictCheckResult:
        self.seen_tz = tz
        return ConflictCheckResult(configured=False, conflicts=[])


@pytest.fixture
def recording_engine() -> _RecordingAvailabilityEngine:
    return _RecordingAvailabilityEngine()


@pytest.fixture
def tz_recording_service(
    repo: InMemoryAppointmentRepository,
    recording_engine: _RecordingAvailabilityEngine,
) -> SchedulingService:
    return SchedulingService(repo, recording_engine)


class TestTimezoneForwarding:
    """`create_appointment`/`update_appointment` forward ``tz`` straight
    through to `AvailabilityEngine.check_conflicts` rather than defaulting
    it away."""

    def test_create_appointment_forwards_tz(
        self,
        tz_recording_service: SchedulingService,
        recording_engine: _RecordingAvailabilityEngine,
    ) -> None:
        ny = ZoneInfo("America/New_York")
        tz_recording_service.create_appointment(USER_ID, data=_appt_data(), tz=ny)
        assert recording_engine.seen_tz is ny

    def test_create_appointment_defaults_to_utc(
        self,
        tz_recording_service: SchedulingService,
        recording_engine: _RecordingAvailabilityEngine,
    ) -> None:
        tz_recording_service.create_appointment(USER_ID, data=_appt_data())
        assert recording_engine.seen_tz is UTC

    def test_update_appointment_forwards_tz(
        self,
        tz_recording_service: SchedulingService,
        recording_engine: _RecordingAvailabilityEngine,
    ) -> None:
        ny = ZoneInfo("America/New_York")
        created = tz_recording_service.create_appointment(USER_ID, data=_appt_data())
        recording_engine.seen_tz = None

        tz_recording_service.update_appointment(
            created.id,
            USER_ID,
            tz=ny,
            start_at=datetime.fromisoformat("2026-03-20T15:00:00+00:00"),
            end_at=datetime.fromisoformat("2026-03-20T15:50:00+00:00"),
        )
        assert recording_engine.seen_tz is ny

    def test_update_appointment_defaults_to_utc(
        self,
        tz_recording_service: SchedulingService,
        recording_engine: _RecordingAvailabilityEngine,
    ) -> None:
        created = tz_recording_service.create_appointment(USER_ID, data=_appt_data())
        recording_engine.seen_tz = None

        tz_recording_service.update_appointment(
            created.id,
            USER_ID,
            start_at=datetime.fromisoformat("2026-03-20T15:00:00+00:00"),
            end_at=datetime.fromisoformat("2026-03-20T15:50:00+00:00"),
        )
        assert recording_engine.seen_tz is UTC


class TestNaiveDatetimeInput:
    """An offset-less datetime means the practice's own wall-clock.

    The API accepts a bare ``2026-03-18T09:00:00`` (``start_at`` is a plain
    ``datetime`` on the request models), so the service has to decide what
    zone that string is in. It reads it in the caller's ``tz`` — the same
    frame availability rules are evaluated in — rather than silently
    stamping UTC.
    """

    NY = ZoneInfo("America/New_York")

    def test_list_range_is_local_midnight_to_midnight(self, service: SchedulingService) -> None:
        # 03:00Z on Mar 18 is 23:00 EDT on Mar *17* — outside a New York
        # "March 18", but inside a UTC one.
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Late on the 17th, New York time",
                start_at=datetime.fromisoformat("2026-03-18T03:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T03:50:00+00:00"),
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Mid-morning on the 18th",
                start_at=datetime.fromisoformat("2026-03-18T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T14:50:00+00:00"),
            ),
        )

        results = service.list_appointments(
            USER_ID, "2026-03-18T00:00:00", "2026-03-19T00:00:00", tz=self.NY
        )

        assert [a.title for a in results] == ["Mid-morning on the 18th"]

    def test_naive_list_range_still_defaults_to_utc(self, service: SchedulingService) -> None:
        """No ``tz`` means UTC — the pre-existing behaviour, unchanged."""
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Late on the 17th, New York time",
                start_at=datetime.fromisoformat("2026-03-18T03:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T03:50:00+00:00"),
            ),
        )

        results = service.list_appointments(USER_ID, "2026-03-18T00:00:00", "2026-03-19T00:00:00")

        assert [a.title for a in results] == ["Late on the 17th, New York time"]

    def test_aware_list_range_ignores_tz(self, service: SchedulingService) -> None:
        """An explicit offset is an instant; ``tz`` must not shift it."""
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Late on the 17th, New York time",
                start_at=datetime.fromisoformat("2026-03-18T03:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T03:50:00+00:00"),
            ),
        )

        results = service.list_appointments(
            USER_ID, "2026-03-18T00:00:00Z", "2026-03-19T00:00:00Z", tz=self.NY
        )

        assert [a.title for a in results] == ["Late on the 17th, New York time"]

    def test_create_reads_naive_start_in_caller_tz(self, service: SchedulingService) -> None:
        appt = service.create_appointment(
            USER_ID,
            data=_appt_data(
                start_at=datetime.fromisoformat("2026-03-20T15:00:00"),
                end_at=datetime.fromisoformat("2026-03-20T15:50:00"),
            ),
            tz=self.NY,
        )

        # 15:00 in New York, not 15:00 UTC and not 15:00 on whatever zone the
        # test host happens to sit in.
        assert appt.start_at == datetime.fromisoformat("2026-03-20T19:00:00+00:00")

    def test_create_leaves_aware_start_alone(self, service: SchedulingService) -> None:
        appt = service.create_appointment(
            USER_ID,
            data=_appt_data(
                start_at=datetime.fromisoformat("2026-03-20T15:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T15:50:00+00:00"),
            ),
            tz=self.NY,
        )

        assert appt.start_at == datetime.fromisoformat("2026-03-20T15:00:00+00:00")
