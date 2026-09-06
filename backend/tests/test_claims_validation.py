# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the refuse-and-list claim validation helper."""

from __future__ import annotations

from dataclasses import dataclass

from app.claims.validation import missing_fields


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
