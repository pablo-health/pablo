# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment types carry their own scheduling window.

A type used to be a name and a fee, with every question of WHEN answered
practice-wide. That made a fifteen-minute consultation and a sixty-minute
intake share one notice period, one lead time and one horizon, which is wrong
in both directions: the consultation could not be offered tomorrow, and the
intake could be offered with a day's warning.

Bug classes these cover:
  * a new field dropped on the way in or out of the repository (the mapping
    used to list every column twice, once per direction);
  * ``min_notice_hours`` collapsing None into 0 — "use the practice default"
    and "no notice needed" are different promises, and a type that silently
    became the second would let someone book an intake an hour out;
  * a PATCH of one field resetting the others, or being unable to clear
    min_notice_hours back to the practice default;
  * validation letting through a duration, audience or horizon unit the
    database CHECK would then reject at write time.
"""

from __future__ import annotations

import pytest
from app.models.scheduling import (
    CreateAppointmentTypeRequest,
    UpdateAppointmentTypeRequest,
)
from app.scheduling_engine.models.appointment_type import AppointmentType
from pydantic import ValidationError


class TestDefaultsDescribeAStandardSession:
    """A caller that sends only a name must get a usable type."""

    def test_a_bare_type_is_offerable_with_sane_windows(self) -> None:
        appointment_type = AppointmentType(id="t1", user_id="u1", name="Session")

        assert appointment_type.duration_minutes == 50
        assert appointment_type.audience == "existing"
        assert appointment_type.earliest_offer_business_days == 1
        assert appointment_type.horizon == 10
        assert appointment_type.horizon_unit == "business"
        assert appointment_type.offerable is True

    def test_self_booking_is_off_until_asked_for(self) -> None:
        # Letting a patient take a slot without the clinician in the loop is
        # never a default.
        assert AppointmentType(id="t1", user_id="u1", name="Session").self_bookable is False

    def test_notice_defers_to_the_practice_rather_than_guessing(self) -> None:
        assert AppointmentType(id="t1", user_id="u1", name="Session").min_notice_hours is None


class TestRoundTrip:
    def test_every_field_survives_to_dict_and_back(self) -> None:
        original = AppointmentType(
            id="t1",
            user_id="u1",
            name="Intake",
            default_fee_cents=20000,
            duration_minutes=60,
            audience="new",
            min_notice_hours=72,
            earliest_offer_business_days=3,
            horizon=21,
            horizon_unit="days",
            self_bookable=True,
            offerable=False,
        )

        assert AppointmentType.from_dict(original.to_dict()) == original

    def test_a_zero_notice_is_not_read_back_as_the_practice_default(self) -> None:
        # 0 and None both look falsey. Conflating them turns "no notice
        # needed" into "whatever the practice says", or the reverse.
        stored = AppointmentType(id="t1", user_id="u1", name="Urgent", min_notice_hours=0)

        assert AppointmentType.from_dict(stored.to_dict()).min_notice_hours == 0

    def test_a_free_type_is_not_read_back_as_having_no_fee_set(self) -> None:
        # 0 means free; None means nobody has set a fee. A superbill cares.
        stored = AppointmentType(id="t1", user_id="u1", name="Consultation", default_fee_cents=0)

        assert AppointmentType.from_dict(stored.to_dict()).default_fee_cents == 0

    def test_a_row_written_before_these_fields_existed_reads_as_todays_behaviour(self) -> None:
        legacy = {"id": "t1", "user_id": "u1", "name": "Individual", "default_fee_cents": 16000}

        restored = AppointmentType.from_dict(legacy)

        assert restored.duration_minutes == 50
        assert restored.audience == "existing"
        assert restored.offerable is True


class TestRequestValidation:
    """The API refuses what the database CHECK would refuse, but with a 422."""

    @pytest.mark.parametrize("duration", [4, 481])
    def test_a_duration_outside_the_allowed_range_is_rejected(self, duration: int) -> None:
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(name="X", duration_minutes=duration)

    def test_an_unknown_audience_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(name="X", audience="everyone")  # type: ignore[arg-type]

    def test_an_unknown_horizon_unit_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(name="X", horizon_unit="weeks")  # type: ignore[arg-type]

    def test_a_zero_horizon_is_rejected(self) -> None:
        # A type offerable zero days ahead can never be offered at all.
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(name="X", horizon=0)

    def test_same_day_offers_are_allowed(self) -> None:
        # 0 business days out is a real choice, distinct from a zero horizon.
        assert CreateAppointmentTypeRequest(name="X", earliest_offer_business_days=0)

    def test_negative_notice_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(name="X", min_notice_hours=-1)


class TestPartialUpdate:
    """PATCH touches only what the caller sent."""

    def test_an_omitted_field_is_absent_from_the_patch(self) -> None:
        patch = UpdateAppointmentTypeRequest(duration_minutes=30)

        assert patch.model_dump(exclude_unset=True) == {"duration_minutes": 30}

    def test_clearing_notice_back_to_the_practice_default_is_expressible(self) -> None:
        # The whole reason the route uses exclude_unset: an explicit null has
        # to mean "defer to the practice" while an omitted field means
        # "leave it alone", and both serialise as None.
        patch = UpdateAppointmentTypeRequest(min_notice_hours=None)

        assert patch.model_dump(exclude_unset=True) == {"min_notice_hours": None}

    def test_applying_a_patch_leaves_untouched_fields_alone(self) -> None:
        appointment_type = AppointmentType(
            id="t1", user_id="u1", name="Intake", duration_minutes=60, horizon=21
        )
        patch = UpdateAppointmentTypeRequest(duration_minutes=90)

        for name, value in patch.model_dump(exclude_unset=True).items():
            setattr(appointment_type, name, value)

        assert appointment_type.duration_minutes == 90
        assert appointment_type.horizon == 21
        assert appointment_type.name == "Intake"
