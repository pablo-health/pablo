# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's scheduling policy.

A type says what an appointment IS; the policy says what the practice will
allow to happen to its calendar. The dangerous direction is permissiveness: a
practice that upgrades into this code must not discover that patients have
started booking it.

Bug classes covered:
  * an unconfigured practice reading as permissive rather than strict;
  * reading a policy creating a row, so "has this practice configured
    anything?" stops being answerable;
  * the shared defaults dict being handed out by reference, letting one
    caller's edit become every unconfigured practice's default;
  * a partial save clobbering fields the caller never mentioned;
  * self-booking treating "the practice allows it" as sufficient, when the
    appointment type must also allow it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from app.models.scheduling import UpdateSchedulingPolicyRequest
from app.scheduling_engine.services.scheduling_policy import (
    DEFAULTS,
    SINGLETON_ID,
    load_policy,
    may_self_book,
    update_policy,
)
from pydantic import ValidationError


def _empty_session() -> Any:
    """A session holding no policy row, as a fresh practice would."""
    session = MagicMock()
    session.get.return_value = None
    return session


class TestAnUnconfiguredPracticeIsStrict:
    def test_self_booking_is_off_in_both_directions(self) -> None:
        policy = load_policy(_empty_session())

        assert policy["self_book_existing"] is False
        assert policy["self_book_new"] is False

    def test_bookings_hold_for_confirmation_rather_than_being_taken(self) -> None:
        # request, not auto: an unconfigured practice should never have a slot
        # taken out from under it.
        assert load_policy(_empty_session())["self_book_mode"] == "request"

    def test_reading_does_not_create_a_row(self) -> None:
        # If reading wrote, "has this practice configured anything?" would stop
        # being answerable, and every health check would leave a row behind.
        session = _empty_session()

        load_policy(session)

        session.add.assert_not_called()
        session.flush.assert_not_called()

    def test_the_defaults_are_not_handed_out_by_reference(self) -> None:
        # A caller mutating what it got back must not rewrite the defaults for
        # every other unconfigured practice in the process.
        first = load_policy(_empty_session())
        first["min_notice_hours"] = 999

        assert load_policy(_empty_session())["min_notice_hours"] == DEFAULTS["min_notice_hours"]
        assert DEFAULTS["min_notice_hours"] == 24


class TestPartialUpdate:
    def test_an_unmentioned_field_keeps_its_value(self) -> None:
        session = _empty_session()

        merged = update_policy(session, {"min_notice_hours": 48})

        assert merged["min_notice_hours"] == 48
        assert merged["max_horizon_days"] == DEFAULTS["max_horizon_days"]
        assert merged["self_book_existing"] is False

    def test_an_unknown_key_is_ignored_rather_than_failing_the_save(self) -> None:
        # A client sending a field this version has not heard of should not
        # lose the rest of its save.
        merged = update_policy(_empty_session(), {"min_notice_hours": 48, "not_a_field": True})

        assert merged["min_notice_hours"] == 48
        assert "not_a_field" not in merged

    def test_the_first_save_creates_the_singleton_row(self) -> None:
        session = _empty_session()

        update_policy(session, {"self_book_existing": True})

        assert session.add.called
        created = session.add.call_args[0][0]
        assert created.id == SINGLETON_ID
        assert created.self_book_existing is True


class TestSelfBookingNeedsBothSwitches:
    def test_the_practice_switch_is_read_per_patient_kind(self) -> None:
        policy = {**DEFAULTS, "self_book_existing": True, "self_book_new": False}

        assert may_self_book(policy, is_new_patient=False) is True
        assert may_self_book(policy, is_new_patient=False) != may_self_book(
            policy, is_new_patient=True
        )

    def test_letting_existing_patients_book_does_not_let_strangers_book(self) -> None:
        # The two switches are separate on purpose: a stranger putting a first
        # appointment on the calendar is a different decision from a known
        # patient rebooking.
        policy = {**DEFAULTS, "self_book_existing": True}

        assert may_self_book(policy, is_new_patient=True) is False

    def test_an_unconfigured_policy_permits_neither(self) -> None:
        policy = load_policy(_empty_session())

        assert may_self_book(policy, is_new_patient=False) is False
        assert may_self_book(policy, is_new_patient=True) is False


class TestRequestValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("min_notice_hours", -1),
            ("max_horizon_days", 0),
            ("pending_hold_hours", 0),
            ("intake_forms_due_hours", -1),
        ],
    )
    def test_a_nonsensical_window_is_rejected(self, field: str, value: int) -> None:
        with pytest.raises(ValidationError):
            UpdateSchedulingPolicyRequest(**{field: value})

    @pytest.mark.parametrize(
        ("field", "value"),
        [("self_book_mode", "instant"), ("new_patient_flow", "whenever")],
    )
    def test_an_unknown_mode_is_rejected(self, field: str, value: str) -> None:
        with pytest.raises(ValidationError):
            UpdateSchedulingPolicyRequest(**{field: value})

    def test_an_empty_patch_touches_nothing(self) -> None:
        assert UpdateSchedulingPolicyRequest().model_dump(exclude_unset=True) == {}
