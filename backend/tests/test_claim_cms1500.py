# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the CMS-1500 layout (``app.claims.cms1500``).

What these pin down:

* the box mapping — 1a is the member id, 21 the diagnoses, 24A-J the
  service lines with lettered pointers, 25 the practice's full tax id with
  its qualifier (the claim's mask only when none is on file), 26 the
  control number, 28 the total, 33a the billing NPI;
* the render is deterministic: the same claim under the same clock is the
  same bytes, and matches the committed fixture
  (``tests/fixtures/claims/cms1500_fixture.pdf``) byte for byte;
* every printed value is in the page's text.

The fixture is regenerated with ``python -m tests.test_claim_cms1500``
from the backend directory when the layout changes on purpose; review the
new PDF before committing it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.claims.cms1500 import ServiceLineFields, cms1500_fields, render_cms1500
from app.claims.cms1500_layout import SERVICE_LINE_ROWS

from tests.claims_fixtures import billing_snapshot, claim, line

if TYPE_CHECKING:
    from app.models.claims import Claim

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "claims" / "cms1500_fixture.pdf"
FIXTURE_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
FIXTURE_TAX_ID = "12-3459714"

_SECOND_LINE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def fixture_claim() -> Claim:
    """A validated two-line, two-diagnosis claim: what the committed PDF shows."""
    return claim(
        state="validated",
        diagnosis_codes=["F41.1", "F32.1"],
        lines=[
            line(dx_pointers=[1, 2]),
            line(
                id=_SECOND_LINE_ID,
                line_number=2,
                cpt="90833",
                charge_cents=6000,
                dx_pointers=[1],
            ),
        ],
        total_charge_cents=21000,
    )


def pdf_text(pdf: bytes) -> list[str]:
    """Every string the page draws, in drawing order.

    The render is uncompressed, so the content stream is plain ``(text) Tj``
    operators; this reads them back without a PDF library.
    """
    escaped = re.findall(rb"\((.*?)\) Tj", pdf)
    return [_unescape(s.decode("latin-1")) for s in escaped]


def _unescape(text: str) -> str:
    return re.sub(r"\\([()\\])", r"\1", text)


class TestBoxMapping:
    def test_identity_boxes(self) -> None:
        fields = cms1500_fields(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW)
        assert fields.box_1a_insured_id == "123456789"
        assert fields.box_2_patient_name == "Anon, John"
        assert fields.box_3_birth_date == "01 01 2000"
        assert fields.box_3_sex == "M"
        assert fields.box_5_patient_address == ["2222 Random St", "Atlanta GA 303010000"]
        assert fields.box_11_group_number == "3335555"
        assert fields.box_11c_plan_name == "Stedi Test Payer"

    def test_diagnoses_and_service_lines(self) -> None:
        fields = cms1500_fields(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW)
        assert fields.box_21_diagnoses == ["F41.1", "F32.1"]
        assert fields.box_24_lines == [
            ServiceLineFields(
                date_from="09 01 2026",
                date_to="09 01 2026",
                place_of_service="10",
                cpt="90837",
                modifiers="95",
                dx_pointer="AB",
                charges="150.00",
                units="1",
                rendering_npi="1999999984",
            ),
            ServiceLineFields(
                date_from="09 01 2026",
                date_to="09 01 2026",
                place_of_service="10",
                cpt="90833",
                modifiers="95",
                dx_pointer="A",
                charges="60.00",
                units="1",
                rendering_npi="1999999984",
            ),
        ]

    def test_billing_boxes(self) -> None:
        fields = cms1500_fields(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW)
        assert fields.box_25_tax_id == FIXTURE_TAX_ID
        assert fields.box_25_tax_id_type == "EIN"
        assert fields.box_26_account_number == "88659891"
        assert fields.box_28_total_charge == "210.00"
        assert fields.box_29_amount_paid == "0.00"
        assert fields.box_31_signature == "Jane Smith"
        assert fields.box_31_date == "09 06 2026"
        assert fields.box_33_billing_provider == [
            "Pablo Test Practice",
            "123 Some St",
            "Atlanta GA 303010000",
            "5553334444",
        ]
        assert fields.box_33a_billing_npi == "1999999984"

    def test_an_ssn_prints_in_full_with_its_qualifier(self) -> None:
        ssn = claim(billing_snapshot=billing_snapshot(tax_id_type="ssn", tax_id_last4="1234"))
        fields = cms1500_fields(ssn, tax_id="123-45-1234", now=FIXTURE_NOW)
        assert (fields.box_25_tax_id, fields.box_25_tax_id_type) == ("123-45-1234", "SSN")

    def test_without_an_id_on_file_box_25_shows_the_claims_mask(self) -> None:
        ssn = claim(billing_snapshot=billing_snapshot(tax_id_type="ssn", tax_id_last4="1234"))
        assert cms1500_fields(ssn, tax_id=None, now=FIXTURE_NOW).box_25_tax_id == "XXX-XX-1234"
        ein = cms1500_fields(fixture_claim(), tax_id=None, now=FIXTURE_NOW)
        assert ein.box_25_tax_id == "XX-XXX9714"

    def test_a_pointer_past_the_diagnosis_list_prints_nothing(self) -> None:
        odd = claim(lines=[line(dx_pointers=[1, 3])])
        assert (
            cms1500_fields(odd, tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW).box_24_lines[0].dx_pointer
            == "A"
        )


class TestRender:
    def test_same_claim_same_clock_same_bytes(self) -> None:
        first = render_cms1500(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW)
        second = render_cms1500(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW)
        assert first == second
        assert first.startswith(b"%PDF")

    def test_matches_the_committed_fixture(self) -> None:
        rendered = render_cms1500(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW)
        assert rendered == FIXTURE_PATH.read_bytes(), (
            "The CMS-1500 render changed. If that was intended, regenerate the fixture "
            "with `python -m tests.test_claim_cms1500` and review the new PDF."
        )

    def test_the_clock_only_moves_the_signature_date(self) -> None:
        later = datetime(2026, 12, 25, 8, 0, tzinfo=UTC)
        text_now = pdf_text(render_cms1500(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW))
        text_later = pdf_text(render_cms1500(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=later))
        assert text_now != text_later
        differing = [(a, b) for a, b in zip(text_now, text_later, strict=True) if a != b]
        assert differing == [("DATE 09 06 2026", "DATE 12 25 2026")]

    def test_every_box_value_is_on_the_page(self) -> None:
        text = pdf_text(render_cms1500(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW))
        for expected in (
            "1a. INSURED'S I.D. NUMBER",
            "123456789",
            "Anon, John",
            "01 01 2000    SEX M",
            "A. F41.1",
            "B. F32.1",
            "90837  95",
            "AB",
            "150.00",
            "90833  95",
            "60.00",
            f"{FIXTURE_TAX_ID}  EIN",
            "88659891",
            "$ 210.00",
            "Jane Smith",
            "DATE 09 06 2026",
            "Pablo Test Practice",
            "33a. NPI",
            "1999999984",
        ):
            assert expected in text, expected

    def test_only_six_service_rows_are_printed(self) -> None:
        crowded = claim(
            state="validated",
            lines=[
                line(id=f"{i:08x}-0000-4000-8000-000000000000", line_number=i, cpt=f"9083{i}")
                for i in range(1, 9)
            ],
        )
        text = pdf_text(render_cms1500(crowded, tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW))
        printed = [t for t in text if t.startswith("9083")]
        assert len(printed) == SERVICE_LINE_ROWS


def _regenerate_fixture() -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_bytes(
        render_cms1500(fixture_claim(), tax_id=FIXTURE_TAX_ID, now=FIXTURE_NOW)
    )
    print(f"wrote {FIXTURE_PATH}")


if __name__ == "__main__":
    _regenerate_fixture()
