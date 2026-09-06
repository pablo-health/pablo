# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the remittance code tables and their lookup API."""

from __future__ import annotations

import pytest
from app.claims.codes import (
    CATEGORIES,
    Category,
    categorize,
    describe_carc,
    describe_claim_status,
    describe_group,
    describe_rarc,
)
from app.claims.codes.carc import CARC, DEACTIVATED_CARC
from app.claims.codes.claim_status import CLAIM_STATUS
from app.claims.codes.groups import GROUPS
from app.claims.codes.lookup import _CATEGORY_BY_ADJUSTMENT
from app.claims.codes.rarc import DEACTIVATED_RARC, RARC

# --- describe_* -----------------------------------------------------------


def test_describe_carc_returns_published_description() -> None:
    assert describe_carc("45") == (
        "Charge exceeds fee schedule/maximum allowable or contracted/legislated fee "
        "arrangement. Usage: This adjustment amount cannot equal the total service or claim "
        "charge amount; and must not duplicate provider adjustment amounts (payments and "
        "contractual reductions) that have resulted from prior payer(s) adjudication. (Use only "
        "with Group Codes PR or CO depending upon liability)"
    )
    assert describe_carc("1") == "Deductible Amount"


def test_describe_carc_unknown_returns_none() -> None:
    assert describe_carc("nonsense") is None
    assert describe_carc("") is None


def test_describe_carc_normalizes_case_and_whitespace() -> None:
    assert describe_carc(" b7 ") == describe_carc("B7")
    assert describe_carc("B7") is not None


def test_describe_carc_keeps_deactivated_codes() -> None:
    # CARC 15 was retired in 2018 but a remittance from before then still cites it.
    assert "15" in DEACTIVATED_CARC
    assert describe_carc("15") == (
        "The authorization number is missing, invalid, or does not apply to the billed "
        "services or provider."
    )


def test_describe_rarc_returns_published_description() -> None:
    assert describe_rarc("N30") == "Patient ineligible for this service."
    assert describe_rarc("M1") is not None


def test_describe_rarc_unknown_returns_none() -> None:
    assert describe_rarc("nonsense") is None
    assert describe_rarc("Z999") is None


def test_describe_rarc_normalizes_case() -> None:
    assert describe_rarc("n30") == describe_rarc("N30")


def test_describe_group_known_and_unknown() -> None:
    assert describe_group("CO") == "Contractual Obligation"
    assert describe_group("pr") == "Patient Responsibility"
    assert describe_group("CR") == "Corrections and Reversal"
    assert describe_group("ZZ") == "Unknown adjustment group ZZ"


def test_describe_claim_status_known_and_unknown() -> None:
    assert describe_claim_status("1") == "Processed as Primary"
    assert describe_claim_status("4") == "Denied"
    assert describe_claim_status("22") == "Reversal of Previous Payment"
    assert describe_claim_status("99") == "Unknown claim status 99"


# --- table integrity ------------------------------------------------------


def test_carc_table_is_not_truncated() -> None:
    assert len(CARC) >= 300


def test_rarc_table_is_not_truncated() -> None:
    assert len(RARC) >= 900


def test_deactivated_codes_are_subsets_of_their_tables() -> None:
    assert CARC.keys() >= DEACTIVATED_CARC
    assert RARC.keys() >= DEACTIVATED_RARC
    # Most of each list is still in force; a regeneration that flipped the
    # flag would show up here.
    assert len(DEACTIVATED_CARC) < len(CARC) / 2
    assert len(DEACTIVATED_RARC) < len(RARC) / 2


def test_tables_have_no_blank_entries() -> None:
    for table in (CARC, RARC, GROUPS, CLAIM_STATUS):
        for code, description in table.items():
            assert code
            assert code == code.strip().upper()
            assert description
            assert description == description.strip()


def test_group_table_has_all_five_groups() -> None:
    assert set(GROUPS) == {"CO", "CR", "OA", "PI", "PR"}


def test_claim_status_table_has_the_835_codes() -> None:
    assert set(CLAIM_STATUS) == {"1", "2", "3", "4", "19", "20", "21", "22", "23", "25"}


def test_category_table_only_names_published_codes() -> None:
    for group, carc in _CATEGORY_BY_ADJUSTMENT:
        assert group in GROUPS
        assert carc in CARC
        assert carc not in DEACTIVATED_CARC


def test_every_category_has_a_row_or_is_paid_or_other() -> None:
    covered = set(_CATEGORY_BY_ADJUSTMENT.values()) | {"paid", "other"}
    assert covered == set(CATEGORIES)


# --- categorize -----------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "carc", "expected"),
    [
        (None, None, "paid"),
        ("", "", "paid"),
        ("PR", "1", "patient_responsibility"),
        ("PR", "2", "patient_responsibility"),
        ("PR", "3", "patient_responsibility"),
        ("CO", "45", "contractual"),
        ("CO", "B7", "enrollment"),
        ("CO", "8", "enrollment"),
        ("CO", "16", "coding"),
        ("CO", "4", "coding"),
        ("CO", "29", "timely_filing"),
        ("CO", "27", "eligibility"),
        ("CO", "26", "eligibility"),
        ("CO", "31", "eligibility"),
        ("CO", "197", "needs_records"),
        ("CO", "198", "needs_records"),
        ("CO", "18", "duplicate"),
        ("OA", "18", "duplicate"),
        ("OA", "23", "other"),
        ("CO", "nonsense", "other"),
        ("ZZ", "45", "other"),
    ],
)
def test_categorize(group: str | None, carc: str | None, expected: Category) -> None:
    assert categorize(group, carc) == expected


def test_categorize_normalizes_case_and_whitespace() -> None:
    assert categorize(" co ", " b7 ") == "enrollment"


def test_categorize_group_without_carc_is_other_not_paid() -> None:
    assert categorize("CO", None) == "other"
    assert categorize(None, "45") == "other"
