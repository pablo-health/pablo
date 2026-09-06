# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every scrub rule, one failing and one passing case, plus determinism.

The baseline claim (``tests/claims_fixtures.py``) passes every rule, so a
test that changes one thing and sees one finding is proving that rule and
only that rule. Three rules are keyed to edits the clearinghouse actually
returned (the recorded ``837p_submission_edit_rejected_*`` fixtures); those
tests read the rejection description off the fixture so the rule and the
edit it answers stay visibly tied together.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.claims.scrub import Finding, blocking, scrub

from tests.claims_fixtures import (
    TODAY,
    billing_snapshot,
    claim,
    line,
    person,
    subscriber_snapshot,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"


def _codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


def _findings(**overrides: object) -> list[Finding]:
    return scrub(claim(**overrides), today=TODAY)


def _edit_description(name: str) -> str:
    payload = json.loads((_FIXTURES / f"837p_submission_edit_rejected_{name}.json").read_text())
    return " ".join(error["description"] for error in payload["errors"])


# ---------------------------------------------------------------------------
# Baseline and determinism
# ---------------------------------------------------------------------------


def test_baseline_claim_has_no_findings() -> None:
    assert scrub(claim(), today=TODAY) == []


def test_scrub_is_deterministic() -> None:
    broken = claim(
        control_number="",
        diagnosis_codes=["F41"],
        place_of_service="11",
        lines=[line(charge_cents=0, dx_pointers=[2])],
        subscriber_snapshot=subscriber_snapshot(payer_id="UNKNOWN"),
    )
    first = scrub(broken, today=TODAY)
    second = scrub(broken, today=TODAY)
    assert first == second
    assert len(first) >= 5
    assert [f.code for f in first] == [f.code for f in second]


def test_blocking_filters_out_warnings() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(taxonomy_code=None))
    assert _codes(findings) == ["taxonomy_missing"]
    assert blocking(findings) == []


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


def test_required_fields_fails_on_blank_billing_name() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(legal_name="  "))
    assert [(f.code, f.field) for f in findings] == [
        ("missing_field", "billing_provider.legal_name")
    ]


def test_required_fields_fails_on_missing_rendering_npi() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(npi=None))
    assert ("missing_field", "billing_provider.npi") in [(f.code, f.field) for f in findings]


def test_required_fields_fails_on_no_diagnosis_and_no_place_of_service() -> None:
    findings = _findings(diagnosis_codes=[], place_of_service=None, lines=[line(dx_pointers=[])])
    fields = [f.field for f in findings if f.code == "missing_field"]
    assert fields == ["place_of_service", "diagnosis_codes"]


def test_required_fields_fails_on_no_lines() -> None:
    findings = _findings(lines=[], total_charge_cents=0)
    assert [(f.code, f.field) for f in findings] == [("missing_field", "lines")]


def test_required_fields_fails_on_blank_cpt() -> None:
    findings = _findings(lines=[line(cpt="")])
    assert [(f.code, f.field) for f in findings] == [("missing_field", "lines[0].cpt")]


def test_required_fields_passes_when_all_present() -> None:
    assert [f for f in _findings() if f.code == "missing_field"] == []


# ---------------------------------------------------------------------------
# Payer and coverage
# ---------------------------------------------------------------------------


def test_unknown_payer_id_is_blocking() -> None:
    findings = _findings(subscriber_snapshot=subscriber_snapshot(payer_id="unknown"))
    assert _codes(findings) == ["payer_unknown"]
    assert findings[0].severity == "blocking"


def test_known_payer_id_passes() -> None:
    assert "payer_unknown" not in _codes(_findings())


def test_inactive_coverage_is_blocking() -> None:
    findings = _findings(subscriber_snapshot=subscriber_snapshot(coverage_active=False))
    assert _codes(findings) == ["coverage_inactive"]


def test_active_coverage_passes() -> None:
    assert "coverage_inactive" not in _codes(_findings())


# ---------------------------------------------------------------------------
# Place of service and telehealth modifier
# ---------------------------------------------------------------------------


def test_office_pos_on_video_visit_is_blocking_and_suggests_fix() -> None:
    findings = _findings(place_of_service="11")
    assert _codes(findings) == ["pos_telehealth_mismatch"]
    assert "10" in findings[0].message
    assert "02" in findings[0].message
    assert "95" in findings[0].message


def test_office_pos_on_in_person_visit_passes() -> None:
    findings = _findings(place_of_service="11", lines=[line(telehealth=False, modifiers=[])])
    assert "pos_telehealth_mismatch" not in _codes(findings)


def test_telehealth_pos_without_95_is_a_warning() -> None:
    findings = _findings(lines=[line(modifiers=[])])
    assert [(f.code, f.severity) for f in findings] == [("telehealth_modifier_missing", "warning")]


def test_telehealth_pos_with_95_passes() -> None:
    assert "telehealth_modifier_missing" not in _codes(_findings(place_of_service="02"))


# ---------------------------------------------------------------------------
# Add-on codes and modifiers
# ---------------------------------------------------------------------------


def test_add_on_without_base_on_same_date_is_blocking() -> None:
    findings = _findings(lines=[line(cpt="90833")])
    assert _codes(findings) == ["add_on_without_base"]


def test_add_on_with_base_on_same_date_passes() -> None:
    base = line(cpt="99214", charge_cents=10000)
    add_on = line(
        id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        line_number=2,
        line_control_number="886598912",
        cpt="90833",
        charge_cents=5000,
    )
    findings = _findings(lines=[base, add_on], total_charge_cents=15000)
    assert "add_on_without_base" not in _codes(findings)


def test_add_on_with_base_on_a_different_date_is_blocking() -> None:
    base = line(cpt="99214", charge_cents=10000, service_date=date(2026, 8, 31))
    add_on = line(
        id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        line_number=2,
        line_control_number="886598912",
        cpt="90833",
        charge_cents=5000,
    )
    findings = _findings(lines=[base, add_on], total_charge_cents=15000)
    assert _codes(findings) == ["add_on_without_base"]


def test_five_modifiers_is_blocking() -> None:
    findings = _findings(lines=[line(modifiers=["95", "GT", "HO", "AJ", "HP"])])
    assert _codes(findings) == ["too_many_modifiers"]


def test_four_modifiers_passes() -> None:
    findings = _findings(lines=[line(modifiers=["95", "GT", "HO", "AJ"])])
    assert "too_many_modifiers" not in _codes(findings)


# ---------------------------------------------------------------------------
# Diagnoses — keyed to the recorded clearinghouse edits
# ---------------------------------------------------------------------------


def test_dx_pointer_past_the_list_is_blocking_like_the_recorded_edit() -> None:
    assert "Diagnosis Pointer" in _edit_description("dx_pointer")
    findings = _findings(lines=[line(dx_pointers=[2])])
    assert _codes(findings) == ["dx_pointer_invalid"]
    assert findings[0].field == "lines[0].dx_pointers"


def test_dx_pointer_into_the_list_passes() -> None:
    findings = _findings(diagnosis_codes=["F41.1", "F33.1"], lines=[line(dx_pointers=[2, 1])])
    assert "dx_pointer_invalid" not in _codes(findings)


def test_category_code_is_blocking_like_the_recorded_edit() -> None:
    assert "highest level of specificity" in _edit_description("dx_specificity")
    findings = _findings(diagnosis_codes=["F41"])
    assert _codes(findings) == ["dx_not_specific"]
    assert findings[0].field == "diagnosis_codes[0]"


def test_specific_code_passes_with_or_without_the_dot() -> None:
    assert "dx_not_specific" not in _codes(_findings(diagnosis_codes=["F41.1"]))
    assert "dx_not_specific" not in _codes(_findings(diagnosis_codes=["F411"]))


# ---------------------------------------------------------------------------
# Subscriber demographics — keyed to the recorded clearinghouse edit
# ---------------------------------------------------------------------------


def test_self_subscriber_without_demographics_is_blocking_like_the_recorded_edit() -> None:
    assert "subscriber address and demographics" in _edit_description("subscriber_demographics")
    bare = person(date_of_birth=None, sex=None, address_line1=None, city=None)
    findings = _findings(subscriber_snapshot=subscriber_snapshot(subscriber=bare, patient=bare))
    assert set(_codes(findings)) == {"subscriber_demographics_missing"}
    assert [f.field for f in findings] == [
        "subscriber.date_of_birth",
        "subscriber.sex",
        "subscriber.address_line1",
        "subscriber.city",
    ]


def test_self_subscriber_with_demographics_passes() -> None:
    assert "subscriber_demographics_missing" not in _codes(_findings())


def test_other_subscriber_needs_their_own_demographics_and_the_client_dob_and_sex() -> None:
    parent = person(first_name="Parent", last_name="Person", sex=None)
    child = person(first_name="Kid", last_name="Person", date_of_birth=None)
    findings = _findings(
        subscriber_snapshot=subscriber_snapshot(
            relationship="child", subscriber=parent, patient=child
        )
    )
    assert [f.field for f in findings] == ["subscriber.sex", "patient.date_of_birth"]
    assert set(_codes(findings)) == {"subscriber_demographics_missing"}


def test_other_subscriber_fully_described_passes() -> None:
    parent = person(first_name="Parent", last_name="Person", date_of_birth=date(1975, 5, 5))
    child = person(first_name="Kid", last_name="Person", date_of_birth=date(2015, 5, 5))
    findings = _findings(
        subscriber_snapshot=subscriber_snapshot(
            relationship="child", subscriber=parent, patient=child
        )
    )
    assert "subscriber_demographics_missing" not in _codes(findings)


# ---------------------------------------------------------------------------
# Dates of birth
# ---------------------------------------------------------------------------


def test_future_dob_is_blocking() -> None:
    future = person(date_of_birth=date(2027, 1, 1))
    findings = _findings(subscriber_snapshot=subscriber_snapshot(subscriber=future, patient=future))
    assert _codes(findings) == ["dob_implausible", "dob_implausible"]
    assert findings[0].field == "subscriber.date_of_birth"
    assert findings[1].field == "patient.date_of_birth"


def test_dob_older_than_120_years_is_blocking() -> None:
    ancient = person(date_of_birth=date(1900, 1, 1))
    findings = _findings(subscriber_snapshot=subscriber_snapshot(subscriber=ancient))
    assert _codes(findings) == ["dob_implausible"]


def test_plausible_dob_passes() -> None:
    assert "dob_implausible" not in _codes(_findings())


# ---------------------------------------------------------------------------
# Money and units
# ---------------------------------------------------------------------------


def test_zero_charge_is_blocking() -> None:
    findings = _findings(lines=[line(charge_cents=0)], total_charge_cents=0)
    assert _codes(findings) == ["charge_zero"]


def test_positive_charge_passes() -> None:
    assert "charge_zero" not in _codes(_findings())


def test_zero_units_is_blocking() -> None:
    findings = _findings(lines=[line(units=0)])
    assert _codes(findings) == ["units_invalid"]


def test_positive_units_passes() -> None:
    assert "units_invalid" not in _codes(_findings(lines=[line(units=2)]))


def test_total_not_matching_lines_is_blocking() -> None:
    findings = _findings(total_charge_cents=15001)
    assert _codes(findings) == ["total_mismatch"]
    assert "15001" in findings[0].message
    assert "15000" in findings[0].message


def test_total_matching_lines_passes() -> None:
    assert "total_mismatch" not in _codes(_findings())


# ---------------------------------------------------------------------------
# Control numbers, delimiters, phone
# ---------------------------------------------------------------------------


def test_control_number_over_17_characters_is_blocking() -> None:
    findings = _findings(control_number="A" * 18)
    assert _codes(findings) == ["control_number_invalid"]


def test_lower_case_control_number_is_blocking() -> None:
    findings = _findings(control_number="abc123")
    assert _codes(findings) == ["control_number_invalid"]


def test_duplicate_line_control_numbers_are_blocking() -> None:
    first = line(cpt="99214", charge_cents=10000)
    second = line(
        id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        line_number=2,
        cpt="90833",
        charge_cents=5000,
    )
    findings = _findings(lines=[first, second], total_charge_cents=15000)
    assert _codes(findings) == ["control_number_invalid"]
    assert findings[0].field == "lines[1].line_control_number"


def test_well_formed_control_numbers_pass() -> None:
    assert "control_number_invalid" not in _codes(_findings(control_number="1ABC-2DEF.3"))


def test_delimiter_in_a_name_is_blocking_and_names_the_field() -> None:
    star = person(last_name="An*n")
    findings = _findings(subscriber_snapshot=subscriber_snapshot(subscriber=star))
    assert [(f.code, f.field) for f in findings] == [
        ("x12_delimiter_in_text", "subscriber.last_name")
    ]


def test_delimiter_in_the_practice_address_is_blocking() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(address_line1="123 Some St ~ Suite 4"))
    assert [(f.code, f.field) for f in findings] == [
        ("x12_delimiter_in_text", "billing_provider.address_line1")
    ]


def test_ordinary_punctuation_passes() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(address_line1="123 Some St., Apt. #4"))
    assert "x12_delimiter_in_text" not in _codes(findings)


def test_phone_with_extension_is_blocking() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(phone="555-333-4444 x12"))
    assert _codes(findings) == ["phone_not_ten_digits"]


def test_formatted_ten_digit_phone_passes() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(phone="(555) 333-4444"))
    assert "phone_not_ten_digits" not in _codes(findings)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def test_missing_taxonomy_is_a_warning_only() -> None:
    findings = _findings(billing_snapshot=billing_snapshot(taxonomy_code=None))
    assert [(f.code, f.severity) for f in findings] == [("taxonomy_missing", "warning")]


def test_taxonomy_present_passes() -> None:
    assert "taxonomy_missing" not in _codes(_findings())
