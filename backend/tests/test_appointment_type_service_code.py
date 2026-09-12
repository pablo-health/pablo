# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The service code lives on the appointment type, and nothing fills it in.

The code is a property of the SERVICE, not of the client or the visit, which
is why it sits beside ``duration_minutes`` rather than being retyped onto every
estimate and superbill. Before this it existed only on ``claim_lines`` and
``contracted_rates``, both downstream of a filed claim — so a self-pay client,
who never generates one, left the expected code unreachable.

Bug classes these cover:
  * a code being INFERRED. The psychotherapy codes band by session length and
    mapping 50 minutes onto one is a one-line change away at all times. The
    bands have edges, practices bill differently, and a wrong code we chose
    ourselves is worse than an empty one because nobody will ever look at it
    again. Several tests here exist only to make that change fail loudly.
  * the column being dropped on the way in or out of the repository — the
    mapping is one shared tuple precisely so a new field cannot go missing in
    one direction.
  * a hand-typed code arriving with the stray space or lower case that would
    stop it matching the contracted rate filed under the same code.
  * the field becoming required, which would stand between a private-pay
    therapist and her first charge.
"""

from __future__ import annotations

import pytest
from app.models.scheduling import (
    CreateAppointmentTypeRequest,
    UpdateAppointmentTypeRequest,
)
from app.repositories.postgres.appointment_type import _SCHEDULING_FIELDS
from app.routes.scheduling import _SEED_APPOINTMENT_TYPES, _ensure_default_appointment_types
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment_type import (
    InMemoryAppointmentTypeRepository,
)
from pydantic import ValidationError

#: The session lengths the psychotherapy codes band around. If anything ever
#: starts guessing, these are the durations it will guess from.
TEMPTING_DURATIONS = [15, 30, 38, 45, 50, 53, 60, 90]


class TestNothingChoosesACode:
    """The whole point of the field: she sets it, we never do."""

    def test_a_new_type_has_no_service_code(self) -> None:
        assert AppointmentType(id="t1", user_id="u1", name="Session").cpt is None

    @pytest.mark.parametrize("duration", TEMPTING_DURATIONS)
    def test_no_duration_produces_a_code(self, duration: int) -> None:
        # 90832/90834/90837 band at roughly 30/45/60 minutes and the mapping is
        # always one tempting line away. It does not belong to us: the bands
        # have edges and two practices bill the same 50 minutes differently.
        request = CreateAppointmentTypeRequest(name="Session", duration_minutes=duration)

        assert request.cpt is None

    @pytest.mark.parametrize("name", ["Intake", "Consultation", "Session", "Family"])
    def test_no_name_produces_a_code(self, name: str) -> None:
        # "Intake" is not always 90791 either — a name is no better a source
        # than a duration.
        assert CreateAppointmentTypeRequest(name=name).cpt is None

    def test_the_seeded_types_arrive_without_one(self) -> None:
        assert all("cpt" not in seed for seed in _SEED_APPOINTMENT_TYPES)

    def test_a_brand_new_practice_gets_types_with_no_codes(self) -> None:
        repo = InMemoryAppointmentTypeRepository()

        created, _ = _ensure_default_appointment_types(repo, "u1")

        assert created
        assert all(t.cpt is None for t in created)

    def test_changing_the_duration_does_not_touch_the_code(self) -> None:
        appointment_type = AppointmentType(
            id="t1", user_id="u1", name="Session", duration_minutes=45, cpt="90834"
        )
        patch = UpdateAppointmentTypeRequest(duration_minutes=60)

        for name, value in patch.model_dump(exclude_unset=True).items():
            setattr(appointment_type, name, value)

        # Not 90837. She moved the session's length; whether that changes what
        # she bills is hers to decide and hers alone.
        assert appointment_type.cpt == "90834"


class TestNothingRequiresACode:
    """A practice that never bills insurance must never be stopped by this."""

    def test_a_type_is_creatable_without_one(self) -> None:
        assert CreateAppointmentTypeRequest(name="Session").cpt is None

    def test_a_code_already_set_can_be_cleared(self) -> None:
        # An explicit null clears; that is what exclude_unset separates from an
        # omitted field. Changing her mind must not need a support ticket.
        patch = UpdateAppointmentTypeRequest(cpt=None)

        assert patch.model_dump(exclude_unset=True) == {"cpt": None}

    def test_an_omitted_code_is_left_alone(self) -> None:
        patch = UpdateAppointmentTypeRequest(duration_minutes=30)

        assert "cpt" not in patch.model_dump(exclude_unset=True)

    def test_an_emptied_field_clears_the_code_rather_than_storing_a_blank(self) -> None:
        assert UpdateAppointmentTypeRequest(cpt="   ").cpt is None


class TestTheCodeSetIsNotOurs:
    """Free text, because the code sets change without asking us."""

    def test_a_code_we_have_never_heard_of_is_accepted(self) -> None:
        # Not on any suggestion list. A payer that wants it must not be a
        # reason for the practice to come and ask us for a release.
        assert CreateAppointmentTypeRequest(name="Session", cpt="T1015").cpt == "T1015"

    def test_a_hcpcs_code_is_upper_cased_so_it_matches_its_contracted_rate(self) -> None:
        assert CreateAppointmentTypeRequest(name="Session", cpt="h0004").cpt == "H0004"

    def test_surrounding_space_is_stripped(self) -> None:
        assert CreateAppointmentTypeRequest(name="Session", cpt=" 90837 ").cpt == "90837"

    def test_a_code_too_long_for_the_column_is_rejected_here_rather_than_at_write_time(
        self,
    ) -> None:
        with pytest.raises(ValidationError):
            CreateAppointmentTypeRequest(name="Session", cpt="9083790837X")


class TestTheCodeSurvivesStorage:
    def test_it_round_trips_through_the_domain_model(self) -> None:
        original = AppointmentType(id="t1", user_id="u1", name="Session", cpt="90837")

        assert AppointmentType.from_dict(original.to_dict()).cpt == "90837"

    def test_a_row_written_before_the_column_existed_reads_as_no_code(self) -> None:
        legacy = {"id": "t1", "user_id": "u1", "name": "Session"}

        assert AppointmentType.from_dict(legacy).cpt is None

    def test_the_repository_maps_it_in_both_directions(self) -> None:
        # One tuple drives read and write, so being in it is the whole
        # guarantee that a PATCH does not silently discard the code.
        assert "cpt" in _SCHEDULING_FIELDS
