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

from datetime import UTC, datetime

import pytest
from app.api_errors import NotFoundError
from app.models.scheduling import (
    CreateAppointmentTypeRequest,
    UpdateAppointmentTypeRequest,
)
from app.routes.scheduling import _apply_appointment_type, _ensure_default_appointment_types
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment_type import (
    InMemoryAppointmentTypeRepository,
)
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


class TestAppointmentsPointAtTheirType:
    """The link that survives a rename.

    Before this, an appointment recorded its type as a name. The settings UI
    lets a clinician rename a type, and every appointment booked under the old
    name was then orphaned — nothing matched, and fee resolution by name
    silently found nothing.
    """

    def _appointment(self, **overrides: object) -> Appointment:
        base: dict[str, object] = {
            "id": "a1",
            "user_id": "u1",
            "patient_id": "p1",
            "title": "Session",
            "start_at": datetime(2026, 9, 3, 15, 0, tzinfo=UTC),
            "end_at": datetime(2026, 9, 3, 15, 50, tzinfo=UTC),
            "duration_minutes": 50,
            "status": "scheduled",
            "session_type": "Individual",
        }
        base.update(overrides)
        return Appointment(**base)  # type: ignore[arg-type]

    def test_the_link_survives_a_round_trip(self) -> None:
        appointment = self._appointment(appointment_type_id="t1")

        assert Appointment.from_dict(appointment.to_dict()).appointment_type_id == "t1"

    def test_a_renamed_type_keeps_the_name_it_was_booked_under(self) -> None:
        # The id says which type this is now; session_type says what it was
        # called then. A past appointment should not silently re-label itself
        # because the clinician renamed the type in March.
        appointment = self._appointment(appointment_type_id="t1", session_type="Individual")

        renamed = AppointmentType(id="t1", user_id="u1", name="Individual therapy")

        assert appointment.appointment_type_id == renamed.id
        assert appointment.session_type == "Individual"

    def test_an_unlinked_appointment_is_expressible(self) -> None:
        # None means "we cannot tell which type this was" — a deleted type, or
        # a legacy row whose name matched nothing at backfill. Worth surfacing
        # rather than guessing.
        assert self._appointment().appointment_type_id is None

    def test_a_legacy_row_without_the_key_reads_as_unlinked(self) -> None:
        legacy = self._appointment().to_dict()
        del legacy["appointment_type_id"]

        assert Appointment.from_dict(legacy).appointment_type_id is None


class TestBookingAgainstAType:
    """A chosen type speaks for the appointment's label.

    Bug classes covered:
      * the id and session_type drifting apart, so a row says it is a
        Consultation while pointing at the Intake type;
      * one clinician in a shared practice booking against another's type,
        which would import the wrong fee;
      * a type id that does not exist being accepted and then failing at the
        foreign key, far from the caller.
    """

    def _repo(self) -> InMemoryAppointmentTypeRepository:
        repo = InMemoryAppointmentTypeRepository()
        repo.create(AppointmentType(id="t1", user_id="u1", name="Intake", duration_minutes=60))
        repo.create(
            AppointmentType(id="t2", user_id="u2", name="Someone else's", duration_minutes=30)
        )
        return repo

    def test_the_type_overrides_whatever_label_the_caller_sent(self) -> None:
        data: dict[str, object] = {"appointment_type_id": "t1", "session_type": "individual"}

        _apply_appointment_type(data, user_id="u1", type_repo=self._repo())

        assert data["session_type"] == "Intake"

    def test_no_type_leaves_the_caller_s_label_alone(self) -> None:
        # Booking without naming a type stays legal; it just goes unlinked.
        data: dict[str, object] = {"appointment_type_id": None, "session_type": "individual"}

        _apply_appointment_type(data, user_id="u1", type_repo=self._repo())

        assert data["session_type"] == "individual"

    def test_another_clinicians_type_is_refused(self) -> None:
        # Not a permissions nicety: booking against someone else's type would
        # resolve the wrong fee and the wrong length.
        data: dict[str, object] = {"appointment_type_id": "t2", "session_type": "individual"}

        with pytest.raises(NotFoundError):
            _apply_appointment_type(data, user_id="u1", type_repo=self._repo())

    def test_an_unknown_type_is_refused_here_rather_than_at_the_foreign_key(self) -> None:
        data: dict[str, object] = {"appointment_type_id": "nope", "session_type": "individual"}

        with pytest.raises(NotFoundError):
            _apply_appointment_type(data, user_id="u1", type_repo=self._repo())

    def test_the_duration_is_not_inferred_from_the_type(self) -> None:
        # A clinician may book a longer-than-usual session of a type, so the
        # caller's duration stands. Only the label is taken from the type.
        data: dict[str, object] = {"appointment_type_id": "t1", "duration_minutes": 90}

        _apply_appointment_type(data, user_id="u1", type_repo=self._repo())

        assert data["duration_minutes"] == 90


class TestDefaultAppointmentTypeSeeding:
    """A practice's first read of /api/appointment-types seeds its catalog.

    A brand-new practice gets all three seed types. A practice that already
    had its own types keeps them untouched and only gains whichever of
    Consultation / Intake it is missing — the settings page's migration
    sentence hinges on telling those two cases apart.
    """

    def test_a_practice_with_no_types_gets_all_three_seeds(self) -> None:
        repo = InMemoryAppointmentTypeRepository()

        types, migrated = _ensure_default_appointment_types(repo, "u1")

        assert {t.name for t in types} == {"Session", "Consultation", "Intake"}
        assert migrated is False

    def test_seed_windows_match_the_handoff_table(self) -> None:
        repo = InMemoryAppointmentTypeRepository()

        types, _ = _ensure_default_appointment_types(repo, "u1")
        by_name = {t.name: t for t in types}

        assert by_name["Session"].duration_minutes == 50
        assert by_name["Session"].audience == "existing"
        assert by_name["Session"].min_notice_hours == 24
        assert by_name["Session"].horizon == 10
        assert by_name["Session"].horizon_unit == "business"

        assert by_name["Consultation"].duration_minutes == 15
        assert by_name["Consultation"].default_fee_cents == 0
        assert by_name["Consultation"].audience == "new"
        assert by_name["Consultation"].horizon == 5
        assert by_name["Consultation"].horizon_unit == "business"

        assert by_name["Intake"].duration_minutes == 60
        assert by_name["Intake"].audience == "new"
        assert by_name["Intake"].min_notice_hours == 72
        assert by_name["Intake"].earliest_offer_business_days == 3
        assert by_name["Intake"].horizon == 21
        assert by_name["Intake"].horizon_unit == "days"

    def test_seeding_a_fresh_practice_is_idempotent(self) -> None:
        repo = InMemoryAppointmentTypeRepository()

        _ensure_default_appointment_types(repo, "u1")
        types, migrated = _ensure_default_appointment_types(repo, "u1")

        assert len(types) == 3
        assert migrated is False

    def test_a_practice_with_its_own_types_keeps_them_and_gains_the_two_seeds(self) -> None:
        repo = InMemoryAppointmentTypeRepository()
        for name in ("Individual", "Couples", "Group"):
            repo.create(AppointmentType(id=name, user_id="u1", name=name))

        types, migrated = _ensure_default_appointment_types(repo, "u1")

        assert {t.name for t in types} == {"Individual", "Couples", "Group", "Consultation", "Intake"}
        assert migrated is True
        # The pre-existing types are untouched, not overwritten with seed windows.
        individual = next(t for t in types if t.name == "Individual")
        assert individual.audience == "existing"
        assert individual.min_notice_hours is None

    def test_migration_is_idempotent_and_does_not_duplicate_the_seeds(self) -> None:
        repo = InMemoryAppointmentTypeRepository()
        repo.create(AppointmentType(id="i1", user_id="u1", name="Individual"))

        _ensure_default_appointment_types(repo, "u1")
        types, migrated = _ensure_default_appointment_types(repo, "u1")

        assert len(types) == 3
        assert migrated is True

    def test_seeding_is_scoped_to_the_calling_practice(self) -> None:
        repo = InMemoryAppointmentTypeRepository()

        _ensure_default_appointment_types(repo, "u1")
        other_types, other_migrated = _ensure_default_appointment_types(repo, "u2")

        assert {t.name for t in other_types} == {"Session", "Consultation", "Intake"}
        assert other_migrated is False
        assert all(t.user_id == "u2" for t in other_types)
