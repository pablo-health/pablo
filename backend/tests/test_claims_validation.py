# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the refuse-and-list claim validation helper."""

from __future__ import annotations

from dataclasses import dataclass

from app.claims.validation import dx_at_highest_specificity, dx_pointers_valid, missing_fields


@dataclass
class _Superbill:
    patient_name: str | None
    diagnosis_code: str | None
    rendering_npi: str | None


def test_missing_fields_none_missing_when_all_present() -> None:
    obj = _Superbill(patient_name="A", diagnosis_code="F41.1", rendering_npi="1234567890")
    assert missing_fields(obj, ["patient_name", "diagnosis_code", "rendering_npi"]) == []


def test_missing_fields_reports_absent_attribute() -> None:
    obj = {"patient_name": "A"}
    assert missing_fields(obj, ["patient_name", "diagnosis_code"]) == ["diagnosis_code"]


def test_missing_fields_reports_none_value() -> None:
    obj = _Superbill(patient_name="A", diagnosis_code=None, rendering_npi="1234567890")
    assert missing_fields(obj, ["patient_name", "diagnosis_code", "rendering_npi"]) == [
        "diagnosis_code"
    ]


def test_missing_fields_reports_blank_string_as_missing() -> None:
    obj = _Superbill(patient_name="A", diagnosis_code="   ", rendering_npi="1234567890")
    assert missing_fields(obj, ["patient_name", "diagnosis_code", "rendering_npi"]) == [
        "diagnosis_code"
    ]


def test_missing_fields_preserves_required_order() -> None:
    obj = {"a": None, "b": None}
    assert missing_fields(obj, ["b", "a"]) == ["b", "a"]


def test_missing_fields_works_on_plain_dict() -> None:
    obj = {"legal_name": "Acme Therapy", "tax_id": ""}
    assert missing_fields(obj, ["legal_name", "tax_id", "billing_npi"]) == [
        "tax_id",
        "billing_npi",
    ]


class TestDxAtHighestSpecificity:
    def test_a_subdivided_code_passes_in_either_form(self) -> None:
        assert dx_at_highest_specificity("F41.1")
        assert dx_at_highest_specificity("F411")
        assert dx_at_highest_specificity("f33.1")

    def test_a_bare_category_is_rejected(self) -> None:
        assert not dx_at_highest_specificity("F41")
        assert not dx_at_highest_specificity("F41.")

    def test_a_malformed_code_is_rejected(self) -> None:
        assert not dx_at_highest_specificity("")
        assert not dx_at_highest_specificity("41.1")
        assert not dx_at_highest_specificity("U07.1x!")
        assert not dx_at_highest_specificity("F41.12345")


class TestDxPointersValid:
    def test_pointers_within_the_claims_diagnoses_pass(self) -> None:
        assert dx_pointers_valid(["1"], 1)
        assert dx_pointers_valid(["1", "2"], 2)
        assert dx_pointers_valid([2, 1], 3)

    def test_a_pointer_past_the_last_diagnosis_fails(self) -> None:
        assert not dx_pointers_valid(["2"], 1)
        assert not dx_pointers_valid(["0"], 1)

    def test_a_line_needs_one_to_four_distinct_pointers(self) -> None:
        assert not dx_pointers_valid([], 1)
        assert not dx_pointers_valid(["1", "1"], 1)
        assert not dx_pointers_valid(["1", "2", "3", "4", "5"], 5)

    def test_a_non_numeric_pointer_fails(self) -> None:
        assert not dx_pointers_valid(["A"], 1)
