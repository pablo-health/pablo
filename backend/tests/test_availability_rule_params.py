# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the availability-rule params tagged union.

``availability_rules.params`` is JSONB, and nothing downstream re-checks
it: the engine reads it with bare subscripts. These tests pin the one
place that does check — that every rule type has a model, that an unknown
key is refused rather than stored, that values are range-checked rather
than merely present, and that the frontend's own validation is still
reading the same shapes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.models.availability_rule_params import (
    RULE_PARAM_MODELS,
    AvailabilityRuleParamsError,
    validate_rule_params,
)
from app.scheduling_engine.models.availability import RuleType
from scripts.regen_availability_param_schema import SCHEMA_PATH, render


class TestEveryRuleTypeHasAModel:
    def test_the_union_covers_the_enum_exactly(self) -> None:
        """A RuleType member added without a params model fails here rather
        than falling through to an unvalidated dict at the boundary."""
        assert set(RULE_PARAM_MODELS) == set(RuleType)

    @pytest.mark.parametrize("rule_type", list(RuleType))
    def test_an_unknown_key_is_refused_for_every_rule_type(self, rule_type: RuleType) -> None:
        with pytest.raises(AvailabilityRuleParamsError):
            validate_rule_params(rule_type, {"not_a_param": 1})

    def test_an_unknown_rule_type_is_refused(self) -> None:
        with pytest.raises(AvailabilityRuleParamsError):
            validate_rule_params("block_full_moons", {})


class TestWellFormedParams:
    @pytest.mark.parametrize(
        ("rule_type", "params"),
        [
            ("working_hours", {"day_of_week": 0, "start": "09:00", "end": "17:00"}),
            ("block_day_of_week", {"day_of_week": 6}),
            ("block_time_range", {"start": "12:00", "end": "13:00"}),
            ("block_date_range", {"start_date": "2026-12-24", "end_date": "2027-01-02"}),
            ("block_specific_dates", {"dates": ["2026-02-28", "2026-03-01"]}),
            ("max_per_day", {"max": 8}),
            ("buffer_before", {"minutes": 0}),
            ("buffer_after", {"minutes": 15}),
            ("session_defaults", {"duration_minutes": 50, "alignment": "half_hour"}),
        ],
    )
    def test_params_round_trip_unchanged(self, rule_type: str, params: dict[str, Any]) -> None:
        assert validate_rule_params(rule_type, params) == params

    def test_an_empty_session_defaults_is_how_a_default_is_cleared(self) -> None:
        assert validate_rule_params("session_defaults", {}) == {}


class TestValuesAreRangeChecked:
    @pytest.mark.parametrize(
        ("rule_type", "params", "expected_field"),
        [
            ("working_hours", {"day_of_week": 7, "start": "09:00", "end": "17:00"}, "day_of_week"),
            ("working_hours", {"day_of_week": -1, "start": "09:00", "end": "17:00"}, "day_of_week"),
            ("working_hours", {"day_of_week": 0, "start": "25:00", "end": "17:00"}, "start"),
            ("working_hours", {"day_of_week": 0, "start": "9:00", "end": "17:00"}, "start"),
            ("working_hours", {"day_of_week": 0, "start": "09:60", "end": "17:00"}, "start"),
            ("block_day_of_week", {"day_of_week": 9}, "day_of_week"),
            ("block_time_range", {"start": "13:00", "end": "12:00"}, "end"),
            ("block_time_range", {"start": "12:00", "end": "12:00"}, "end"),
            ("max_per_day", {"max": 0}, "max"),
            ("max_per_day", {"max": "thirty"}, "max"),
            ("buffer_before", {"minutes": -1}, "minutes"),
            ("buffer_after", {"minutes": "thirty"}, "minutes"),
            (
                "block_date_range",
                {"start_date": "2026-03-10", "end_date": "2026-03-01"},
                "end_date",
            ),
            ("block_date_range", {"start_date": "2026-02-30", "end_date": "2026-03-01"}, "start"),
            ("block_date_range", {"start_date": "10-03-2026", "end_date": "2026-03-01"}, "start"),
            ("block_specific_dates", {"dates": []}, "dates"),
            ("block_specific_dates", {"dates": ["2026-13-01"]}, "dates"),
            ("session_defaults", {"duration_minutes": 0}, "duration_minutes"),
            ("session_defaults", {"alignment": "quarter_hour"}, "alignment"),
        ],
    )
    def test_out_of_range_values_are_refused_naming_the_field(
        self, rule_type: str, params: dict[str, Any], expected_field: str
    ) -> None:
        with pytest.raises(AvailabilityRuleParamsError) as excinfo:
            validate_rule_params(rule_type, params)
        assert expected_field in str(excinfo.value)

    @pytest.mark.parametrize("rule_type", ["buffer_before", "buffer_after"])
    def test_a_misspelled_buffer_key_is_not_a_rule_that_enforces_nothing(
        self, rule_type: str
    ) -> None:
        """The defect this whole module exists for: ``minute`` for
        ``minutes`` stores happily today and reads as a configured gap rule
        that never fires."""
        with pytest.raises(AvailabilityRuleParamsError) as excinfo:
            validate_rule_params(rule_type, {"minute": 15})
        assert "minutes" in str(excinfo.value)

    def test_a_time_of_day_may_not_carry_seconds(self) -> None:
        """The engine's minute arithmetic reads HH:MM by position."""
        with pytest.raises(AvailabilityRuleParamsError):
            validate_rule_params("block_time_range", {"start": "12:00:00", "end": "13:00:00"})

    def test_a_day_of_week_may_not_be_a_bool(self) -> None:
        """``True`` is an int in Python, and Monday is not a boolean."""
        with pytest.raises(AvailabilityRuleParamsError):
            validate_rule_params("block_day_of_week", {"day_of_week": True})


class TestFrontendSchemaIsCurrent:
    def test_the_committed_schema_matches_the_models(self) -> None:
        """AvailabilitySettings.tsx validates against this file, so a field
        renamed in the models without regenerating it would leave the
        frontend checking a shape the API no longer accepts.

        Regenerate with:
            poetry run python backend/scripts/regen_availability_param_schema.py
        """
        assert SCHEMA_PATH.read_text() == render()

    def test_the_schema_tags_every_rule_type_for_the_frontend_to_read(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text())
        assert schema["discriminator"]["propertyName"] == "rule_type"
        assert set(schema["discriminator"]["mapping"]) == {rt.value for rt in RuleType}
