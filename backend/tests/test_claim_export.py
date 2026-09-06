# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the biller CSV (``app.claims.export``).

What these pin down:

* the header is exactly the fixed column list, in order, and there is one
  row per service line;
* each column reads from the stored claim — money as dollars with two
  decimals, lists pipe-joined, four diagnosis columns padded with blanks;
* the tax id column carries the full id the caller decrypted, with its
  EIN/SSN qualifier beside it, and falls back to the claim's mask when the
  practice has none on file;
* ``check_export`` refuses a package with a blocking finding, naming the
  claim and reusing the scrub's findings, and refuses a draft;
* the in-memory repository's range query leaves drafts out and honours
  both ends of the range.
"""

from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import TYPE_CHECKING, Any

import pytest
from app.claims.export import (
    CSV_COLUMNS,
    BlockedClaim,
    ExportBlockedError,
    check_export,
    claims_to_csv,
    tax_id_for_export,
)
from app.repositories.claims import InMemoryClaimRepository

from tests.claims_fixtures import TODAY, billing_snapshot, claim, line

if TYPE_CHECKING:
    from app.models.claims import Claim

_SECOND_LINE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_TAX_ID = "12-3459714"


def _validated(**overrides: Any) -> Claim:
    return claim(state="validated", **overrides)


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(text)))


def _csv(claims: list[Claim], tax_id: str | None = _TAX_ID) -> str:
    return claims_to_csv(claims, tax_id=tax_id)


class TestColumns:
    def test_header_is_the_fixed_column_list(self) -> None:
        header = next(csv.reader(StringIO(_csv([]))))
        assert header == list(CSV_COLUMNS)
        assert header == [
            "control_number",
            "patient_last",
            "patient_first",
            "patient_dob",
            "patient_sex",
            "member_id",
            "group_number",
            "payer_name",
            "payer_id",
            "subscriber_relationship",
            "rendering_npi",
            "billing_npi",
            "tax_id",
            "tax_id_type",
            "taxonomy",
            "service_date",
            "place_of_service",
            "cpt",
            "modifiers",
            "units",
            "charge",
            "dx1",
            "dx2",
            "dx3",
            "dx4",
            "dx_pointers",
        ]

    def test_one_row_per_claim_line(self) -> None:
        two_lines = _validated(
            lines=[
                line(),
                line(id=_SECOND_LINE_ID, line_number=2, cpt="90833", charge_cents=6000),
            ],
            total_charge_cents=21000,
        )
        rows = _rows(_csv([two_lines, _validated(id="other", control_number="X1")]))
        assert [(r["control_number"], r["cpt"]) for r in rows] == [
            ("88659891", "90837"),
            ("88659891", "90833"),
            ("X1", "90837"),
        ]

    def test_every_column_reads_from_the_stored_claim(self) -> None:
        (row,) = _rows(_csv([_validated()]))
        assert row == {
            "control_number": "88659891",
            "patient_last": "Anon",
            "patient_first": "John",
            "patient_dob": "2000-01-01",
            "patient_sex": "M",
            "member_id": "123456789",
            "group_number": "3335555",
            "payer_name": "Stedi Test Payer",
            "payer_id": "STEDI",
            "subscriber_relationship": "self",
            "rendering_npi": "1999999984",
            "billing_npi": "1999999984",
            "tax_id": _TAX_ID,
            "tax_id_type": "EIN",
            "taxonomy": "101YM0800X",
            "service_date": "2026-09-01",
            "place_of_service": "10",
            "cpt": "90837",
            "modifiers": "95",
            "units": "1",
            "charge": "150.00",
            "dx1": "F41.1",
            "dx2": "",
            "dx3": "",
            "dx4": "",
            "dx_pointers": "1",
        }

    def test_lists_are_pipe_joined_and_money_is_two_decimal_dollars(self) -> None:
        many = _validated(
            diagnosis_codes=["F41.1", "F32.1", "F43.10"],
            lines=[line(modifiers=["95", "GT"], dx_pointers=[1, 3], charge_cents=12345)],
            total_charge_cents=12345,
        )
        (row,) = _rows(_csv([many]))
        assert row["modifiers"] == "95|GT"
        assert row["dx_pointers"] == "1|3"
        assert row["charge"] == "123.45"
        assert (row["dx1"], row["dx2"], row["dx3"], row["dx4"]) == ("F41.1", "F32.1", "F43.10", "")


class TestTaxId:
    def test_the_full_id_is_rendered_not_the_claims_last_four(self) -> None:
        (row,) = _rows(_csv([_validated()]))
        assert (row["tax_id"], row["tax_id_type"]) == (_TAX_ID, "EIN")
        assert "9714" not in row.values()

    def test_an_ssn_keeps_its_qualifier(self) -> None:
        ssn = _validated(billing_snapshot=billing_snapshot(tax_id_type="ssn", tax_id_last4="6789"))
        (row,) = _rows(_csv([ssn], tax_id="123-45-6789"))
        assert (row["tax_id"], row["tax_id_type"]) == ("123-45-6789", "SSN")

    def test_without_an_id_on_file_the_claims_mask_is_printed(self) -> None:
        (row,) = _rows(_csv([_validated()], tax_id=None))
        assert row["tax_id"] == "XX-XXX9714"
        assert row["tax_id_type"] == "EIN"

    def test_the_mask_takes_the_shape_of_the_ids_type(self) -> None:
        ein = billing_snapshot().billing_provider
        ssn = billing_snapshot(tax_id_type="ssn", tax_id_last4="1234").billing_provider
        none = billing_snapshot(tax_id_type=None, tax_id_last4=None).billing_provider
        assert tax_id_for_export(None, ein) == "XX-XXX9714"
        assert tax_id_for_export(None, ssn) == "XXX-XX-1234"
        assert tax_id_for_export(None, none) == ""
        assert tax_id_for_export("12-3459714", ein) == "12-3459714"


class TestRefusal:
    def test_a_clean_validated_claim_passes(self) -> None:
        check_export([_validated()], today=TODAY)

    def test_a_blocking_finding_refuses_the_package_naming_the_claim(self) -> None:
        bad = _validated(diagnosis_codes=["F41"])
        with pytest.raises(ExportBlockedError) as excinfo:
            check_export([_validated(id="ok", control_number="OK1"), bad], today=TODAY)
        blocked = excinfo.value.blocked
        assert [b.control_number for b in blocked] == ["88659891"]
        assert isinstance(blocked[0], BlockedClaim)
        assert blocked[0].claim_id == bad.id
        assert "dx_not_specific" in {f.code for f in blocked[0].findings}
        assert all(f.severity == "blocking" for f in blocked[0].findings)

    def test_a_draft_is_refused_not_dropped(self) -> None:
        with pytest.raises(ExportBlockedError) as excinfo:
            check_export([claim(state="draft")], today=TODAY)
        assert [f.code for f in excinfo.value.blocked[0].findings] == ["claim_is_draft"]


class TestRangeQuery:
    def test_drafts_are_left_out_and_the_range_is_inclusive(self) -> None:
        repo = InMemoryClaimRepository()
        repo.create(_validated(id="in-1", control_number="A1"))
        repo.create(claim(id="draft", control_number="D1", state="draft"))
        repo.create(
            _validated(
                id="edge",
                control_number="E1",
                lines=[line(service_date=date(2026, 9, 30))],
            )
        )
        repo.create(
            _validated(
                id="out",
                control_number="O1",
                lines=[line(service_date=date(2026, 10, 1))],
            )
        )
        found = repo.list_for_export(date(2026, 9, 1), date(2026, 9, 30))
        assert {c.id for c in found} == {"in-1", "edge"}
