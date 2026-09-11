# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a payer's service-line decisions onto a claim's own lines.

Every case here is arithmetic about somebody's money, and the failure mode is
never a crash — it is a number that looks plausible and is wrong.

Two different standards of proof are in play, and the tests are organised
around the difference:

* **What a client is billed** comes from the ``PR`` adjustments alone. That is
  safe on definitional grounds rather than empirical ones: the CAS group codes
  are normative, and a provider may bill a client only for adjustments
  carrying ``PR``. No compliant payer can contradict it.
* **What the payer allowed** is only ever what the payer reported. It is not
  derived, because every plausible derivation is wrong somewhere real.

The vendor's test payer pays every claim in full and adjusts nothing, so the
remittances below are constructed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.claims.remittance_lines import (
    applied_to,
    patient_responsibility_agrees,
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
    allowed_cents: int | None = None,
    adjustments: list[Adjustment] | None = None,
) -> RemittanceLine:
    return RemittanceLine(
        line_control_number=control_number,
        service_date="20260901",
        cpt="90837",
        charge_cents=charge_cents,
        paid_cents=paid_cents,
        allowed_cents=allowed_cents,
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


class TestWhatTheClientIsBilled:
    """Only ``PR`` moves money onto a client's ledger."""

    def test_a_contractual_discount_is_owed_by_nobody(self) -> None:
        """The practice agreed to it by joining the network. Billing a client
        for their insurer's negotiated discount is the single worst outcome
        this module can produce."""
        posting = posting_for_line(11_000, [_adjustment("CO", "45", 4_000)])

        assert posting.patient_responsibility_cents == 0

    def test_patient_responsibility_is_owed_by_the_client(self) -> None:
        posting = posting_for_line(12_000, [_adjustment("PR", "2", 3_000)])

        assert posting.patient_responsibility_cents == 3_000

    def test_a_discount_and_a_coinsurance_on_one_line_stay_separate(self) -> None:
        """The ordinary in-network session."""
        posting = posting_for_line(
            8_000, [_adjustment("CO", "45", 4_000), _adjustment("PR", "2", 3_000)]
        )

        assert posting.patient_responsibility_cents == 3_000
        assert posting.paid_cents == 8_000

    def test_several_client_owed_adjustments_are_summed(self) -> None:
        """Deductible and copay can both land on one line."""
        posting = posting_for_line(
            10_000, [_adjustment("PR", "1", 3_000), _adjustment("PR", "3", 2_000)]
        )

        assert posting.patient_responsibility_cents == 5_000

    def test_an_adjustment_in_no_group_we_bill_from_is_not_billed(self) -> None:
        """``OA`` and ``PI`` are neither a network discount nor a client
        balance. Guessing would bill a client wrongly, so they are recorded
        and counted nowhere."""
        posting = posting_for_line(12_000, [_adjustment("OA", "23", 3_000)])

        assert posting.patient_responsibility_cents == 0
        assert posting.adjustments == [
            {"group_code": "OA", "reason_code": "23", "amount_cents": 3_000}
        ]

    @pytest.mark.parametrize("group", ["pr", "Pr", "pR"])
    def test_the_group_code_is_read_regardless_of_case(self, group: str) -> None:
        posting = posting_for_line(12_000, [_adjustment(group, "2", 3_000)])

        assert posting.patient_responsibility_cents == 3_000

    def test_the_whole_charge_going_to_deductible_is_owed_in_full(self) -> None:
        """Paid nothing, and the client owes everything. The claim was
        processed, not denied."""
        posting = posting_for_line(0, [_adjustment("PR", "1", 15_000)])

        assert posting.patient_responsibility_cents == 15_000
        assert posting.paid_cents == 0


class TestWhatThePayerAllowed:
    """Reported or absent. Never derived.

    Every plausible derivation from the adjustments is wrong somewhere real,
    and the wrong answers are the quiet kind — a number in the right range
    that misstates what a payer agreed a session was worth.
    """

    def test_the_reported_amount_is_used(self) -> None:
        posting = posting_for_line(8_000, [_adjustment("CO", "45", 4_000)], allowed_cents=11_000)

        assert posting.allowed_cents == 11_000

    def test_an_unreported_amount_stays_unreported(self) -> None:
        """``None`` says "the payer did not tell us", which is true. A derived
        stand-in would say something false and look identical."""
        posting = posting_for_line(11_000, [_adjustment("CO", "45", 4_000)])

        assert posting.allowed_cents is None

    def test_an_out_of_network_write_off_is_not_read_as_a_full_allowance(self) -> None:
        """The case that broke the old derivation.

        Out of network the write-off arrives as ``PR45`` and there is no
        contractual adjustment at all, so "charge less contractual" returned
        the entire charge — reporting that the payer allowed $150 when it
        allowed $60.
        """
        posting = posting_for_line(6_000, [_adjustment("PR", "45", 9_000)])

        assert posting.allowed_cents is None, (
            "with no reported allowed amount there is nothing to say; the "
            "charge is not an allowance"
        )
        assert posting.patient_responsibility_cents == 9_000


class TestAnAbsentAllowanceMeansTwoDifferentThings:
    """The standard has payers omit the field rather than send a zero.

    So "no allowed amount" covers both "the payer did not itemise" and "the
    payer allowed nothing" — opposite statements. The payer's own claim
    status settles which, and only for the case it actually settles.
    """

    def test_a_refused_claim_allowed_nothing(self) -> None:
        posting = posting_for_line(0, [_adjustment("CO", "29", 15_000)], denied=True)

        assert posting.allowed_cents == 0

    def test_a_processed_claim_with_no_reported_allowance_says_nothing(self) -> None:
        posting = posting_for_line(11_000, [_adjustment("CO", "45", 4_000)], denied=False)

        assert posting.allowed_cents is None

    def test_a_reported_allowance_wins_even_on_a_refused_claim(self) -> None:
        """A payer that troubled to state a number is not second-guessed."""
        posting = posting_for_line(0, [], allowed_cents=8_000, denied=True)

        assert posting.allowed_cents == 8_000

    def test_the_claims_own_status_decides_it(self) -> None:
        """Read from ``CLP02``, which is the payer saying "denied" in its own
        words — not our reading of which adjustment codes imply refusal."""
        refused = _remittance(_line("CLM1L1", paid_cents=0)).model_copy(
            update={"claim_status_code": "4"}
        )

        [posting] = postings_for(refused).values()

        assert posting.allowed_cents == 0


class TestThePayersOwnCrossCheck:
    """``CLP05`` against the itemised ``PR``.

    The payer states the client's total once and then itemises it. Two
    independent statements of one number, so a mis-grouped adjustment shows
    up as a disagreement instead of as a wrong bill.
    """

    def test_a_claim_whose_itemisation_matches_its_total_agrees(self) -> None:
        remittance = _remittance(
            _line("CLM1L1", paid_cents=12_000, adjustments=[_adjustment("PR", "2", 3_000)])
        ).model_copy(update={"patient_responsibility_cents": 3_000})

        assert patient_responsibility_agrees(remittance)

    def test_a_mis_grouped_adjustment_is_caught(self) -> None:
        """The failure this exists for: the payer says the client owes $30 and
        our reading of the lines finds nothing, because the adjustment was
        read as a contractual write-off.

        The log line moved to ``disagreement_in``, which is the function the
        posting path calls and the one that decides a hold; this predicate
        answers one narrow question and says nothing. See
        ``test_remittance_balance.py`` for the logging.
        """
        remittance = _remittance(
            _line("CLM1L1", paid_cents=12_000, adjustments=[_adjustment("CO", "45", 3_000)])
        ).model_copy(update={"patient_responsibility_cents": 3_000})

        assert not patient_responsibility_agrees(remittance)

    def test_a_claim_level_share_counts_towards_the_total(self) -> None:
        """A payer may report the client's share at claim level instead of on
        the lines; that is still the client's share.

        The line is paid in full at line level and the claim pays less after
        a claim-level adjustment, which is the shape a compliant payer sends
        — a claim-level CAS explains the claim's gap, and a line's gap can
        only be explained by a CAS on that line.
        """
        remittance = _remittance(_line("CLM1L1", paid_cents=15_000)).model_copy(
            update={
                "paid_cents": 12_000,
                "adjustments": [_adjustment("PR", "1", 3_000)],
                "patient_responsibility_cents": 3_000,
            }
        )

        assert patient_responsibility_agrees(remittance)

    def test_a_reversal_is_exempt(self) -> None:
        """A takeback negates an earlier adjudication, and the standard does
        not require the stated total to match the itemisation there. Checking
        it anyway would report a disagreement on a claim behaving correctly.

        Every amount is negated, the charge included: a reversal that negated
        only what was paid would not be a reversal of anything, and would not
        balance.
        """
        reversal = _remittance(
            _line(
                "CLM1L1",
                charge_cents=-15_000,
                paid_cents=-12_000,
                adjustments=[_adjustment("PR", "2", -3_000)],
            )
        ).model_copy(update={"claim_status_code": "22", "patient_responsibility_cents": 0})

        assert patient_responsibility_agrees(reversal)

    def test_the_captured_paid_in_full_remittance_agrees(self) -> None:
        body = json.loads((_FIXTURES / "835_report_paid_in_full.json").read_text())
        [remittance] = parse_835(body)

        assert patient_responsibility_agrees(remittance.claims[0])


class TestNothingIsQuietlyDiscarded:
    def test_every_adjustment_is_kept_even_when_it_changes_no_total(self) -> None:
        """A biller appealing a line works from the reason codes. Keeping only
        the ones we bill from would drop exactly the odd case somebody is
        trying to understand."""
        posting = posting_for_line(
            0,
            [
                _adjustment("CO", "45", 4_000),
                _adjustment("PR", "1", 8_000),
                _adjustment("OA", "23", 3_000),
            ],
        )

        assert [a["reason_code"] for a in posting.adjustments] == ["45", "1", "23"]


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
                allowed_cents=11_000,
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

    def test_a_claim_paid_in_full_owes_the_client_nothing(self) -> None:
        body = json.loads((_FIXTURES / "835_report_paid_in_full.json").read_text())
        [remittance] = parse_835(body)
        [paid] = remittance.claims

        postings = postings_for(paid)

        assert len(postings) == 1
        [posting] = postings.values()
        assert posting.paid_cents == paid.total_charge_cents
        assert posting.patient_responsibility_cents == 0
        assert posting.adjustments == []

    def test_this_payer_reported_no_allowed_amount(self) -> None:
        """Which is why deriving one was tempting. The capture proves the
        field really is absent rather than merely unread."""
        body = json.loads((_FIXTURES / "835_report_paid_in_full.json").read_text())
        [remittance] = parse_835(body)

        assert remittance.claims[0].lines[0].allowed_cents is None
