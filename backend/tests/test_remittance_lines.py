# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a payer's service-line decisions onto a claim's own lines.

Every case here is arithmetic about somebody's money, and the failure mode is
never a crash — it is a number that looks plausible and is wrong. A
contractual discount counted as patient responsibility bills a client for the
practice's own network discount; the reverse silently writes off money the
client owed.

The vendor's test payer pays every claim in full and adjusts nothing, so the
remittances below are constructed. The group codes are the published X12
meanings, not this vendor's invention, but the arithmetic is unproven against
a real payer until the first one sends an 835.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.claims.remittance_lines import (
    LinePosting,
    applied_to,
    posting_for_line,
    postings_for,
)
from app.claims.responses import parse_835
from app.models.claims_responses import Adjustment, RemittanceClaim, RemittanceLine

from .claims_fixtures import claim, line

_FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"


def _adjustment(group: str, reason: str, cents: int) -> Adjustment:
    return Adjustment(group_code=group, reason_code=reason, amount_cents=cents)


def _line(
    control_number: str = "CLM1L1",
    *,
    charge_cents: int = 15_000,
    paid_cents: int = 15_000,
    adjustments: list[Adjustment] | None = None,
) -> RemittanceLine:
    return RemittanceLine(
        line_control_number=control_number,
        service_date="20260901",
        cpt="90837",
        charge_cents=charge_cents,
        paid_cents=paid_cents,
        adjustments=adjustments or [],
    )


def _remittance(*lines: RemittanceLine) -> RemittanceClaim:
    return RemittanceClaim(
        patient_control_number="CLM1",
        payer_claim_control_number="P1",
        claim_status_code="1",
        total_charge_cents=sum(line.charge_cents for line in lines),
        paid_cents=sum(line.paid_cents for line in lines),
        patient_responsibility_cents=0,
        claim_frequency_code="1",
        adjustments=[],
        lines=list(lines),
    )


class TestWhoIsOutTheMoney:
    """The group code decides, and getting it wrong moves real money."""

    def test_a_contractual_discount_is_owed_by_nobody(self) -> None:
        """The practice agreed to it by joining the network. Billing a client
        for their insurer's negotiated discount is the single worst outcome
        this module can produce."""
        posting = posting_for_line(15_000, 11_000, [_adjustment("CO", "45", 4_000)])

        assert posting.patient_responsibility_cents == 0
        assert posting.allowed_cents == 11_000

    def test_patient_responsibility_is_owed_by_the_client(self) -> None:
        posting = posting_for_line(15_000, 12_000, [_adjustment("PR", "2", 3_000)])

        assert posting.patient_responsibility_cents == 3_000
        # No contractual write-off, so the payer allowed the whole charge.
        assert posting.allowed_cents == 15_000

    def test_the_two_are_added_up_separately(self) -> None:
        """The ordinary in-network session: a discount the practice eats and
        a coinsurance the client owes, on the same line."""
        posting = posting_for_line(
            15_000,
            8_000,
            [_adjustment("CO", "45", 4_000), _adjustment("PR", "2", 3_000)],
        )

        assert posting.allowed_cents == 11_000
        assert posting.patient_responsibility_cents == 3_000
        assert posting.paid_cents == 8_000

    def test_several_client_owed_adjustments_are_summed(self) -> None:
        """Deductible and copay can both land on one line."""
        posting = posting_for_line(
            15_000,
            10_000,
            [_adjustment("PR", "1", 3_000), _adjustment("PR", "3", 2_000)],
        )

        assert posting.patient_responsibility_cents == 5_000

    def test_an_adjustment_we_cannot_classify_lands_in_neither_total(self) -> None:
        """``OA`` and ``PI`` are neither a network discount nor a client
        balance. Guessing would either bill a client wrongly or write off
        money quietly, so it is recorded and counted nowhere."""
        posting = posting_for_line(15_000, 12_000, [_adjustment("OA", "23", 3_000)])

        assert posting.patient_responsibility_cents == 0
        assert posting.allowed_cents == 15_000
        assert posting.adjustments == [
            {"group_code": "OA", "reason_code": "23", "amount_cents": 3_000}
        ]

    @pytest.mark.parametrize("group", ["co", "Co", "cO"])
    def test_the_group_code_is_read_regardless_of_case(self, group: str) -> None:
        posting = posting_for_line(15_000, 11_000, [_adjustment(group, "45", 4_000)])

        assert posting.allowed_cents == 11_000


class TestNothingIsQuietlyDiscarded:
    def test_every_adjustment_is_kept_even_when_it_changes_no_total(self) -> None:
        """A biller appealing a line works from the reason codes. Keeping only
        the ones we could classify would drop exactly the odd case somebody
        is trying to understand."""
        posting = posting_for_line(
            15_000,
            0,
            [
                _adjustment("CO", "45", 4_000),
                _adjustment("PR", "1", 8_000),
                _adjustment("OA", "23", 3_000),
            ],
        )

        assert [a["reason_code"] for a in posting.adjustments] == ["45", "1", "23"]

    def test_a_line_with_no_charge_reports_no_allowed_amount(self) -> None:
        """Rather than zero, which would read as "the payer allowed nothing"
        — a different and much worse statement than "we cannot say"."""
        posting = posting_for_line(0, 0, [])

        assert posting.allowed_cents is None


class TestMatchingPayerLinesToOurOwn:
    def test_each_line_is_found_by_its_own_control_number(self) -> None:
        remittance = _remittance(
            _line("CLM1L1", paid_cents=15_000),
            _line("CLM1L2", charge_cents=5_000, paid_cents=4_000),
        )

        postings = postings_for(remittance)

        assert set(postings) == {"CLM1L1", "CLM1L2"}
        assert postings["CLM1L2"].paid_cents == 4_000

    def test_the_decision_lands_on_the_matching_claim_line(self) -> None:
        built = claim(
            control_number="CLM1",
            lines=[line(line_control_number="CLM1L1", charge_cents=15_000)],
        )
        remittance = _remittance(
            _line(
                "CLM1L1",
                paid_cents=8_000,
                adjustments=[
                    _adjustment("CO", "45", 4_000),
                    _adjustment("PR", "2", 3_000),
                ],
            )
        )

        [updated] = applied_to(built.lines, remittance)

        assert updated.allowed_cents == 11_000
        assert updated.paid_cents == 8_000
        assert updated.patient_resp_cents == 3_000
        assert updated.adjustments is not None
        assert len(updated.adjustments) == 2

    def test_a_line_the_payer_did_not_mention_is_left_alone(self) -> None:
        """Silence is not a decision. Zeroing it would report that the payer
        allowed nothing for a service it has not ruled on yet."""
        built = claim(
            control_number="CLM1",
            lines=[
                line(line_control_number="CLM1L1", charge_cents=15_000),
                line(line_control_number="CLM1L2", charge_cents=5_000, line_number=2),
            ],
        )
        remittance = _remittance(_line("CLM1L1", paid_cents=15_000))

        first, second = applied_to(built.lines, remittance)

        assert first.paid_cents == 15_000
        assert second.allowed_cents is None
        assert second.patient_resp_cents is None
        assert second.adjustments is None

    def test_a_payer_line_matching_nothing_is_dropped_not_invented(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Adding a line to hold it would put a service on the claim that was
        never billed."""
        built = claim(
            control_number="CLM1",
            lines=[line(line_control_number="CLM1L1", charge_cents=15_000)],
        )
        remittance = _remittance(_line("CLM1L1"), _line("CLM1L9", charge_cents=5_000))

        with caplog.at_level("WARNING"):
            updated = applied_to(built.lines, remittance)

        assert [line_.line_control_number for line_ in updated] == ["CLM1L1"]
        assert "remittance_line_unmatched" in caplog.text

    def test_reading_the_same_remittance_twice_writes_the_same_numbers(self) -> None:
        """The posting is driven off a schedule, so this runs more than once."""
        built = claim(
            control_number="CLM1",
            lines=[line(line_control_number="CLM1L1", charge_cents=15_000)],
        )
        remittance = _remittance(
            _line("CLM1L1", paid_cents=8_000, adjustments=[_adjustment("PR", "2", 3_000)])
        )

        once = applied_to(built.lines, remittance)
        twice = applied_to(once, remittance)

        assert [line_.model_dump() for line_ in once] == [line_.model_dump() for line_ in twice]


class TestAgainstARealRemittance:
    """The one 835 that was actually captured: the test payer, paid in full."""

    def test_a_claim_paid_in_full_allows_the_whole_charge_and_owes_nothing(self) -> None:
        body = json.loads((_FIXTURES / "835_report_paid_in_full.json").read_text())
        [remittance] = parse_835(body)
        [paid] = remittance.claims

        postings = postings_for(paid)

        assert len(postings) == 1
        [posting] = postings.values()
        assert isinstance(posting, LinePosting)
        assert posting.paid_cents == paid.total_charge_cents
        assert posting.allowed_cents == paid.total_charge_cents
        assert posting.patient_responsibility_cents == 0
        assert posting.adjustments == []
