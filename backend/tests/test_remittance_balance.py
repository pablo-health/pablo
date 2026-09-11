# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Asserting an 835 accounts for its own numbers before believing any of them.

An 835 is a balanced transaction: the adjustments must account for the whole
gap between what was charged and what was paid, at the line and at the
claim, and the client's share must be stated and itemised to the same
figure. Those identities are guaranteed by the standard (TR3 005010X221A1
§1.10.2, SNIP level 3) rather than by any particular payer's care, which is
exactly what makes a failure worth acting on: it means our parse is wrong or
the payer's file is, and either way the amounts are not ones to bill a real
person from.

**A caveat that shapes every fixture below, and it is the important one.**
No captured 835 carrying real adjustments exists to test against. The
vendor's test payer pays every claim in full and adjusts nothing; no
complete, unambiguously public-domain 835 with a service-line ``CO-45``
appears to exist. So every adjusting remittance here is authored, and an
authored fixture can only ever confirm the model of the format that wrote
it — the failure mode this codebase has already paid for once
(``docs/internal``, the log-enrichment drift).

:class:`TestTheDefinitionAgreesWithTheCode` is the mitigation and the reason
this file is worth more than its unit tests. It generates remittances from a
domain model of how adjudication actually works — fee schedule, deductible
remaining, coinsurance, copay, out of network, sequestration — renders each
one, and asserts both that the invariants hold AND that the patient
responsibility we would post equals what the model says the client owes. The
model is an independent statement of the rule; the parser is our code; the
round trip is where the disagreement shows up. It is not a capture, and it
does not replace one. The first real hold is the capture.
"""

from __future__ import annotations

import itertools

import pytest
from app.claims.codes import pairing
from app.claims.codes.carc import CARC
from app.claims.remittance_lines import disagreement_in, patient_responsibility_agrees
from app.models.claims_responses import Adjustment, RemittanceClaim, RemittanceLine


def _adjustment(group: str, reason: str, cents: int) -> Adjustment:
    return Adjustment(group_code=group, reason_code=reason, amount_cents=cents)


def _line(
    control_number: str = "CLM1L1",
    *,
    charge_cents: int,
    paid_cents: int,
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


def _claim(
    *lines: RemittanceLine,
    claim_adjustments: list[Adjustment] | None = None,
    patient_responsibility_cents: int | None = None,
    claim_status_code: str = "1",
    total_charge_cents: int | None = None,
    paid_cents: int | None = None,
) -> RemittanceClaim:
    """A remittance that balances unless a caller deliberately unbalances it.

    The defaults derive the claim totals from the lines, so a test that does
    nothing special gets a compliant document and a test that breaks one
    identity breaks exactly the one it names.
    """
    claim_adjustments = claim_adjustments or []
    charged = (
        total_charge_cents
        if total_charge_cents is not None
        else sum(line.charge_cents for line in lines)
    )
    if paid_cents is None:
        paid_cents = sum(line.paid_cents for line in lines) - sum(
            a.amount_cents for a in claim_adjustments
        )
    if patient_responsibility_cents is None:
        patient_responsibility_cents = sum(
            a.amount_cents
            for a in [*claim_adjustments, *(a for line in lines for a in line.adjustments)]
            if a.group_code == "PR"
        )
    return RemittanceClaim(
        patient_control_number="CLM1",
        payer_claim_control_number="P1",
        claim_status_code=claim_status_code,
        total_charge_cents=charged,
        paid_cents=paid_cents,
        patient_responsibility_cents=patient_responsibility_cents,
        claim_frequency_code="1",
        adjustments=claim_adjustments,
        lines=list(lines),
    )


class TestADocumentThatAccountsForItself:
    """The ordinary case: nothing is held, because nothing disagrees."""

    def test_an_in_network_visit_with_a_copay_balances(self) -> None:
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=8_000,
                adjustments=[_adjustment("CO", "45", 5_000), _adjustment("PR", "3", 2_000)],
            )
        )

        assert disagreement_in(remittance) is None

    def test_a_claim_paid_in_full_with_no_adjustments_balances(self) -> None:
        assert disagreement_in(_claim(_line(charge_cents=15_000, paid_cents=15_000))) is None

    def test_a_denial_balances_when_the_adjustments_cover_the_whole_charge(self) -> None:
        """Adjudicated to nothing is still adjudicated, and still balances."""
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=0,
                adjustments=[_adjustment("CO", "50", 15_000)],
            ),
            claim_status_code="4",
        )

        assert disagreement_in(remittance) is None

    def test_a_reversal_balances_with_every_amount_negated(self) -> None:
        """The balancing identities are signed, so a takeback balances too.

        Only the patient-responsibility cross-check is exempt on a reversal
        (X12 RFI #2548). The arithmetic is not exempt from itself.
        """
        reversal = _claim(
            _line(
                charge_cents=-15_000,
                paid_cents=-8_000,
                adjustments=[_adjustment("CO", "45", -5_000), _adjustment("PR", "3", -2_000)],
            ),
            claim_status_code="22",
            patient_responsibility_cents=0,
        )

        assert disagreement_in(reversal) is None

    def test_two_lines_each_balancing_balance_together(self) -> None:
        remittance = _claim(
            _line(
                "L1",
                charge_cents=15_000,
                paid_cents=12_000,
                adjustments=[_adjustment("PR", "2", 3_000)],
            ),
            _line("L2", charge_cents=10_000, paid_cents=10_000),
        )

        assert disagreement_in(remittance) is None

    def test_an_unclassifiable_group_still_accounts_for_the_gap(self) -> None:
        """``OA`` and ``PI`` are counted by neither total and by the balance.

        That is the point of summing every group: an adjustment we decline
        to classify still has to be somewhere, and one we dropped is exactly
        the gap these identities exist to notice.
        """
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=13_000,
                adjustments=[_adjustment("OA", "23", 2_000)],
            )
        )

        assert disagreement_in(remittance) is None


class TestALineThatDoesNotAccountForItself:
    def test_a_line_whose_adjustments_miss_the_gap_is_caught(self) -> None:
        """$150 charged, $80 paid, and only $50 of it explained."""
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=8_000,
                adjustments=[_adjustment("CO", "45", 5_000)],
            ),
            paid_cents=8_000,
            patient_responsibility_cents=0,
        )

        found = disagreement_in(remittance)

        assert found is not None
        assert found.reason == "line_balance"
        assert found.stated_cents == 15_000
        assert found.computed_cents == 13_000
        assert found.delta_cents == 2_000

    def test_the_failing_line_is_named(self) -> None:
        """A practice looking at the hold should not have to guess which line."""
        remittance = _claim(
            _line("L1", charge_cents=10_000, paid_cents=10_000),
            _line("L2", charge_cents=10_000, paid_cents=9_000),
            paid_cents=19_000,
            patient_responsibility_cents=0,
        )

        found = disagreement_in(remittance)

        assert found is not None
        assert found.reason == "line_balance"
        assert found.line_control_number == "L2"

    def test_a_line_overpaid_relative_to_its_charge_is_caught(self) -> None:
        """The identity is an equality, not a ceiling.

        A payer paying more than the line was charged is as much a reason to
        stop and look as one paying less, and a check written as ``<=``
        would wave it through.
        """
        remittance = _claim(
            _line(charge_cents=10_000, paid_cents=12_000),
            paid_cents=12_000,
            patient_responsibility_cents=0,
        )

        found = disagreement_in(remittance)

        assert found is not None
        assert found.reason == "line_balance"


class TestAClaimThatDoesNotAccountForItself:
    def test_a_claim_total_the_lines_do_not_explain_is_caught(self) -> None:
        """Every line balances and the claim still does not.

        This is the case a line-only check misses: the lines are internally
        consistent, and the claim header disagrees with all of them at once.
        """
        remittance = _claim(
            _line(charge_cents=15_000, paid_cents=15_000),
            paid_cents=12_000,
            patient_responsibility_cents=0,
        )

        found = disagreement_in(remittance)

        assert found is not None
        assert found.reason == "claim_balance"
        assert found.stated_cents == 15_000
        assert found.computed_cents == 12_000

    def test_a_claim_level_adjustment_explains_a_claim_level_gap(self) -> None:
        """The same shape, with the adjustment the payer owed us. Not held."""
        remittance = _claim(
            _line(charge_cents=15_000, paid_cents=15_000),
            claim_adjustments=[_adjustment("PI", "137", 3_000)],
        )

        assert disagreement_in(remittance) is None


class TestWhichDisagreementIsReported:
    def test_the_clients_share_is_named_first_when_more_than_one_fails(self) -> None:
        """A document can fail every check at once.

        Patient responsibility is reported because it is the one that speaks
        directly about the number that bills a client — which is what the
        practice is being asked to decide about.
        """
        remittance = _claim(
            _line(charge_cents=15_000, paid_cents=8_000),
            paid_cents=8_000,
            patient_responsibility_cents=2_000,
        )

        found = disagreement_in(remittance)

        assert found is not None
        assert found.reason == "patient_responsibility"

    def test_a_disagreement_is_logged_at_warning_with_both_numbers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        remittance = _claim(
            _line(charge_cents=15_000, paid_cents=8_000),
            paid_cents=8_000,
            patient_responsibility_cents=0,
        )

        with caplog.at_level("WARNING"):
            assert disagreement_in(remittance) is not None

        assert "remittance_disagrees" in caplog.text
        assert "reason=line_balance" in caplog.text

    def test_the_old_boolean_still_answers_only_about_the_clients_share(self) -> None:
        """``patient_responsibility_agrees`` predates the other two checks.

        A line that does not balance is a real disagreement and this
        function must still say nothing about it, because callers use it to
        ask one specific question.
        """
        remittance = _claim(
            _line(charge_cents=15_000, paid_cents=8_000),
            paid_cents=8_000,
            patient_responsibility_cents=0,
        )

        assert patient_responsibility_agrees(remittance)
        assert disagreement_in(remittance) is not None


# ---------------------------------------------------------------------------
# The domain model: an independent statement of how adjudication works
# ---------------------------------------------------------------------------


class Adjudication:
    """How a plan would actually settle one visit, worked out from first principles.

    Written deliberately WITHOUT reference to the parser: it starts from
    what a plan document says (a fee schedule, a deductible balance, a
    coinsurance rate, a copay, whether the provider is in network, whether
    sequestration applies) and works out who owes what. The renderer below
    turns that into the 835 a payer would send.

    So the test asserts two independent things agree — the definition and
    the code — rather than asserting the code agrees with a fixture written
    by somebody holding the same belief the code encodes.

    The out-of-network arm matters more than it looks: there the write-off
    arrives as ``PR-45`` with no ``CO`` at all, so the client owes MORE than
    the allowed amount. Any check written as "patient responsibility cannot
    exceed the allowed amount" passes every in-network case and is wrong
    here, which is why the model generates it.
    """

    def __init__(
        self,
        *,
        charge_cents: int,
        allowed_cents: int,
        deductible_remaining_cents: int,
        coinsurance_rate: float,
        copay_cents: int,
        in_network: bool,
        sequestration: bool,
    ) -> None:
        self.charge_cents = charge_cents
        # Out of network there is no negotiated rate to reduce to: the payer
        # allows what it allows and the balance is the client's to argue
        # about, not a write-off the practice agreed to.
        self.allowed_cents = allowed_cents if in_network else min(allowed_cents, charge_cents)
        self.in_network = in_network
        self.deductible_cents = min(deductible_remaining_cents, self.allowed_cents)
        after_deductible = self.allowed_cents - self.deductible_cents
        self.copay_cents = min(copay_cents, after_deductible)
        after_copay = after_deductible - self.copay_cents
        self.coinsurance_cents = round(after_copay * coinsurance_rate)
        self.contractual_cents = charge_cents - self.allowed_cents if in_network else 0
        # PR-45: out of network the excess over allowed is the client's.
        self.excess_cents = 0 if in_network else charge_cents - self.allowed_cents
        plan_pays = after_copay - self.coinsurance_cents
        # CO-253: Medicare's 2% sequestration comes out of the PAYMENT, not
        # out of the allowed amount and never out of what the client owes.
        self.sequestration_cents = round(plan_pays * 0.02) if sequestration else 0
        self.paid_cents = plan_pays - self.sequestration_cents

    @property
    def client_owes_cents(self) -> int:
        """What the plan says the client is billed. The number under test."""
        return self.deductible_cents + self.copay_cents + self.coinsurance_cents + self.excess_cents

    def to_remittance(self) -> RemittanceClaim:
        """The 835 a payer would send for this adjudication."""
        adjustments: list[Adjustment] = []
        if self.contractual_cents:
            adjustments.append(_adjustment("CO", "45", self.contractual_cents))
        if self.deductible_cents:
            adjustments.append(_adjustment("PR", "1", self.deductible_cents))
        if self.copay_cents:
            adjustments.append(_adjustment("PR", "3", self.copay_cents))
        if self.coinsurance_cents:
            adjustments.append(_adjustment("PR", "2", self.coinsurance_cents))
        if self.excess_cents:
            adjustments.append(_adjustment("PR", "45", self.excess_cents))
        if self.sequestration_cents:
            adjustments.append(_adjustment("CO", "253", self.sequestration_cents))
        return _claim(
            _line(
                charge_cents=self.charge_cents,
                paid_cents=self.paid_cents,
                adjustments=adjustments,
            )
        )


#: A deterministic sweep rather than a randomised one. Every combination
#: below runs on every CI pass, so a failure is reproducible from the test
#: name alone and nobody has to chase a seed. It is also why no new
#: dependency was taken on for this.
_CASES = list(
    itertools.product(
        (15_000, 20_000, 7_550),  # charge
        (12_000, 9_000),  # allowed
        (0, 5_000, 50_000),  # deductible remaining: none, partial, all of it
        (0.0, 0.2, 0.5),  # coinsurance
        (0, 2_500),  # copay
        (True, False),  # in network
        (True, False),  # sequestration
    )
)


class TestTheDefinitionAgreesWithTheCode:
    """The model says what the client owes; the parser says what we would post."""

    @pytest.mark.parametrize(
        (
            "charge",
            "allowed",
            "deductible",
            "coinsurance",
            "copay",
            "in_network",
            "sequestration",
        ),
        _CASES,
    )
    def test_a_rendered_adjudication_balances_and_bills_what_the_plan_says(
        self,
        charge: int,
        allowed: int,
        deductible: int,
        coinsurance: float,
        copay: int,
        in_network: bool,
        sequestration: bool,
    ) -> None:
        model = Adjudication(
            charge_cents=charge,
            allowed_cents=allowed,
            deductible_remaining_cents=deductible,
            coinsurance_rate=coinsurance,
            copay_cents=copay,
            in_network=in_network,
            sequestration=sequestration,
        )
        remittance = model.to_remittance()

        # Every identity the standard guarantees holds on a document built
        # from a correct adjudication. If this fails, either the model is
        # not a correct adjudication or the checks are wrong about the
        # standard — and both are worth stopping for.
        assert disagreement_in(remittance) is None, (
            f"a correctly adjudicated claim was held: {disagreement_in(remittance)}"
        )

        # And the number that reaches a client's ledger is the number the
        # plan says they owe. This is the assertion the invariants exist to
        # protect; the invariants passing while this fails would mean a
        # self-consistent document billed the wrong person the wrong amount.
        assert remittance.patient_responsibility_cents == model.client_owes_cents

    def test_the_out_of_network_case_really_does_exceed_the_allowed_amount(self) -> None:
        """Proof the sweep above contains the case that breaks the easy rule.

        Without this, an out-of-network arm that silently never triggered
        would leave the sweep looking thorough and testing nothing unusual.
        """
        model = Adjudication(
            charge_cents=20_000,
            allowed_cents=9_000,
            deductible_remaining_cents=0,
            coinsurance_rate=0.2,
            copay_cents=0,
            in_network=False,
            sequestration=False,
        )

        # $110 of balance bill plus $18 of coinsurance against a $90
        # allowed amount.
        assert model.excess_cents == 11_000
        assert model.client_owes_cents > model.allowed_cents
        assert disagreement_in(model.to_remittance()) is None

    def test_sequestration_never_reaches_the_client(self) -> None:
        """``CO-253`` comes out of the payment, not out of what a client owes.

        A parser that treated every reduction as the client's would bill
        this person 2% of the plan's share on top of their own.
        """
        without = Adjudication(
            charge_cents=15_000,
            allowed_cents=12_000,
            deductible_remaining_cents=0,
            coinsurance_rate=0.2,
            copay_cents=0,
            in_network=True,
            sequestration=False,
        )
        with_seq = Adjudication(
            charge_cents=15_000,
            allowed_cents=12_000,
            deductible_remaining_cents=0,
            coinsurance_rate=0.2,
            copay_cents=0,
            in_network=True,
            sequestration=True,
        )

        assert with_seq.sequestration_cents > 0
        assert with_seq.client_owes_cents == without.client_owes_cents
        assert with_seq.paid_cents < without.paid_cents
        assert disagreement_in(with_seq.to_remittance()) is None


class TestASuspiciousCodePairing:
    """A group that contradicts its own reason code. Warn, never hold."""

    def test_a_deductible_written_off_is_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        """``CO-1``: a deductible the practice supposedly absorbed."""
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=12_000,
                adjustments=[_adjustment("CO", "1", 3_000)],
            )
        )

        with caplog.at_level("WARNING"):
            disagreement_in(remittance)

        assert "remittance_suspicious_code_pairing" in caplog.text
        assert "group=CO" in caplog.text
        assert "reason=1" in caplog.text

    def test_sequestration_billed_to_a_client_is_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``PR-253``: a federal budget measure charged to a therapy client."""
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=12_000,
                adjustments=[_adjustment("PR", "253", 3_000)],
            )
        )

        with caplog.at_level("WARNING"):
            disagreement_in(remittance)

        assert "remittance_suspicious_code_pairing" in caplog.text

    def test_a_suspicious_pairing_alone_does_not_hold_the_bill(self) -> None:
        """The check that gates a client's bill is the arithmetic.

        This document is odd and adds up. Holding it would stop a practice
        billing over a code combination nobody has catalogued, which is a
        check that fires on novelty rather than on error.
        """
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=12_000,
                adjustments=[_adjustment("CO", "1", 3_000)],
            )
        )

        assert disagreement_in(remittance) is None

    def test_the_ordinary_pairings_are_quiet(self, caplog: pytest.LogCaptureFixture) -> None:
        """A check that warns on normal remittances teaches people to skip it."""
        remittance = _claim(
            _line(
                charge_cents=15_000,
                paid_cents=8_000,
                adjustments=[
                    _adjustment("CO", "45", 5_000),
                    _adjustment("PR", "1", 1_000),
                    _adjustment("PR", "2", 1_000),
                ],
            )
        )

        with caplog.at_level("WARNING"):
            disagreement_in(remittance)

        assert "remittance_suspicious_code_pairing" not in caplog.text

    def test_the_normative_table_is_not_vendored_and_says_so(self) -> None:
        """Guards the scope claim in the module docstring.

        The CAQH CORE Rule 360 table is the normative source and is not in
        this repo. If somebody later imports it, this test failing is the
        prompt to rewrite the docstring rather than leave it lying.
        """
        assert "NOT vendored here" in pairing.__doc__
        assert set(CARC) > pairing.PATIENT_ONLY_REASONS
        assert set(CARC) > pairing.NEVER_PATIENT_REASONS
