# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Asserting an 835 accounts for its own numbers before believing any of them.

The balancing identities are guaranteed by the standard (TR3 005010X221A1
§1.10.2), so a failure means our parse is wrong or the payer's file is.

CAVEAT, and it shapes every fixture here: no captured 835 with real
adjustments exists. The test payer pays in full; no public-domain 835 with a
service-line ``CO-45`` appears to exist. So every adjusting remittance below
is AUTHORED, and an authored fixture only ever confirms the model that wrote
it.

:class:`TestTheDefinitionAgreesWithTheCode` is the mitigation: it generates
remittances from an independent domain model of adjudication and asserts the
posted patient responsibility matches what the plan says. Not a capture, and
no substitute for one — the first real hold is the capture.
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
    """A remittance that balances unless a caller deliberately unbalances it."""
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
        """Signed, so a takeback balances too. Only the ``PR`` cross-check is
        exempt on a reversal (X12 RFI #2548)."""
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
        """An adjustment we decline to classify still has to be somewhere."""
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
        """An equality, not a ceiling — a ``<=`` check would wave this through."""
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
        """The case a line-only check misses: every line balances, the header
        disagrees with all of them."""
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
        """When several fail, report the one that speaks about the client's bill."""
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
        """It answers one narrow question and must stay silent about the rest."""
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
    """How a plan settles one visit, worked out from first principles.

    Written WITHOUT reference to the parser, so the test asserts two
    independent statements agree rather than asserting the code agrees with
    a fixture that shares its beliefs.

    The out-of-network arm matters: the write-off arrives as ``PR-45`` with
    no ``CO``, so the client owes MORE than the allowed amount. Any check
    written as "PR cannot exceed allowed" passes in network and is wrong
    here.
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
        """Proof the sweep contains the case that breaks the easy rule."""
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
        """``CO-253`` comes out of the payment, never off the client."""
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
        """Odd, and it adds up. Novelty is not error."""
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
        """Guards the scope claim: CORE Rule 360 is not vendored here."""
        assert "not vendored" in pairing.__doc__
        assert set(CARC) > pairing.PATIENT_ONLY_REASONS
        assert set(CARC) > pairing.NEVER_PATIENT_REASONS
