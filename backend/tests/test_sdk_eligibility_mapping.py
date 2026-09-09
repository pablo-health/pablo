# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading the vendor SDK's eligibility answer into the chart's summary.

These are the edge cases, built by hand: a payer that reports both a
plan-year deductible and what is left of it, a coordination-of-benefits entry
that names the payer we just asked, an inquiry the payer refused outright.
They are not evidence that the mapping matches what a real payer sends —
that is the live suite's job, against a captured response — but they are
where the reasoning is, and they fail loudly when it changes.
"""

from __future__ import annotations

import pytest
from app.claims.sdk_eligibility import outcome_from_sdk
from stedi.models import (
    CreateEligibilityCheckOutput,
    EligibilityCheckBenefits,
    EligibilityCheckCoInsurance,
    EligibilityCheckCoPayment,
    EligibilityCheckDeductible,
    EligibilityCheckEligibilityStatus,
    EligibilityCheckLimitation,
    EligibilityCheckOtherOrAdditionalPayer,
    EligibilityCheckPayer,
    EligibilityCheckPersonOrOrganizationNameOrganization,
    EligibilityCheckPlan,
    EligibilityCheckQuantity,
    EligibilityCheckRelatedEntity,
    EligibilityCheckResponseError,
)


def _organization(name: str):
    return EligibilityCheckPersonOrOrganizationNameOrganization(name)


def _payer(name: str = "Aetna") -> EligibilityCheckPayer:
    return EligibilityCheckPayer(type="PAYER", name=_organization(name))


def _output(
    *,
    benefits: EligibilityCheckBenefits | None = None,
    errors: list[EligibilityCheckResponseError] | None = None,
    payer_id: str | None = "60054",
    plan_name: str | None = None,
) -> CreateEligibilityCheckOutput:
    plans = None
    if benefits is not None:
        plans = [EligibilityCheckPlan(benefits=benefits, name=plan_name)]
    return CreateEligibilityCheckOutput(
        id="ec_test",
        x12="ISA*...~",
        payer=_payer(),
        payer_id=payer_id,
        plans=plans,
        errors=errors,
    )


def _map(output: CreateEligibilityCheckOutput):
    return outcome_from_sdk(output, stored={"id": output.id})


class TestStatus:
    @pytest.mark.parametrize(
        "status",
        [
            "ACTIVE_COVERAGE",
            "ACTIVE_FULL_RISK_CAPITATION",
            "ACTIVE_SERVICES_CAPITATED",
            "ACTIVE_PENDING_INVESTIGATION",
        ],
    )
    def test_every_active_variant_reads_as_active(self, status: str) -> None:
        """The variants differ in how the payer settles, not in whether it pays."""
        benefits = EligibilityCheckBenefits(
            statuses=[EligibilityCheckEligibilityStatus(status=status)]
        )

        assert _map(_output(benefits=benefits)).status == "active"

    def test_inactive_reads_as_inactive(self) -> None:
        benefits = EligibilityCheckBenefits(
            statuses=[EligibilityCheckEligibilityStatus(status="INACTIVE")]
        )

        assert _map(_output(benefits=benefits)).status == "inactive"

    def test_active_wins_when_the_payer_reports_both(self) -> None:
        """A payer often reports one service active and another not."""
        benefits = EligibilityCheckBenefits(
            statuses=[
                EligibilityCheckEligibilityStatus(status="INACTIVE"),
                EligibilityCheckEligibilityStatus(status="ACTIVE_COVERAGE"),
            ]
        )

        assert _map(_output(benefits=benefits)).status == "active"

    def test_no_statuses_is_unknown_not_inactive(self) -> None:
        """A 271 that answered without saying is not a denial."""
        assert _map(_output(benefits=EligibilityCheckBenefits())).status == "unknown"

    def test_a_refused_inquiry_is_an_error_even_alongside_a_plan(self) -> None:
        """An AAA rejection means the coverage question was never answered."""
        errors = [
            EligibilityCheckResponseError(
                code="75",
                description="Subscriber/Insured Not Found",
                followup_action="C",
                location="SUBSCRIBER",
                possible_resolutions="Check the member id.",
            )
        ]
        benefits = EligibilityCheckBenefits(
            statuses=[EligibilityCheckEligibilityStatus(status="ACTIVE_COVERAGE")]
        )

        outcome = _map(_output(benefits=benefits, errors=errors))

        assert outcome.status == "error"
        assert [error.code for error in outcome.aaa_errors] == ["75"]
        assert outcome.aaa_errors[0].resolution == "Check the member id."


class TestMoney:
    @pytest.mark.parametrize(("sent", "cents"), [("25", 2500), ("25.0", 2500), ("40.00", 4000)])
    def test_a_copay_survives_whatever_precision_the_payer_sent(
        self, sent: str, cents: int
    ) -> None:
        benefits = EligibilityCheckBenefits(co_payment=[EligibilityCheckCoPayment(amount=sent)])

        assert _map(_output(benefits=benefits)).copay_cents == cents

    def test_coinsurance_is_shown_as_a_percentage(self) -> None:
        """The vendor sends the patient's share as a fraction."""
        benefits = EligibilityCheckBenefits(
            co_insurance=[EligibilityCheckCoInsurance(percent="0.2")]
        )

        assert _map(_output(benefits=benefits)).coinsurance_pct == 20.0

    def test_only_the_remaining_deductible_is_reported(self) -> None:
        """The dangerous one: reporting the plan-year total as the remainder
        tells a client they owe the whole deductible over again."""
        benefits = EligibilityCheckBenefits(
            deductible=[
                EligibilityCheckDeductible(amount="1500.00", time_period="CALENDAR_YEAR"),
                EligibilityCheckDeductible(amount="250.00", time_period="REMAINING"),
            ]
        )

        assert _map(_output(benefits=benefits)).deductible_remaining_cents == 25000

    def test_a_deductible_with_no_remaining_entry_is_left_unsaid(self) -> None:
        benefits = EligibilityCheckBenefits(
            deductible=[EligibilityCheckDeductible(amount="1500.00", time_period="CALENDAR_YEAR")]
        )

        assert _map(_output(benefits=benefits)).deductible_remaining_cents is None


class TestVisitLimits:
    def test_remaining_and_total_are_kept_apart(self) -> None:
        benefits = EligibilityCheckBenefits(
            limitations=[
                EligibilityCheckLimitation(
                    quantity=EligibilityCheckQuantity(value="20", qualifier="VISITS"),
                    time_period="CALENDAR_YEAR",
                ),
                EligibilityCheckLimitation(
                    quantity=EligibilityCheckQuantity(value="12", qualifier="VISITS"),
                    time_period="REMAINING",
                ),
            ]
        )

        limit = _map(_output(benefits=benefits)).visit_limit

        assert limit is not None
        assert (limit.remaining, limit.total) == (12, 20)

    def test_a_limit_counted_in_something_other_than_visits_is_ignored(self) -> None:
        """Dollar and day limits are not visit caps and must not be shown as one."""
        benefits = EligibilityCheckBenefits(
            limitations=[
                EligibilityCheckLimitation(
                    quantity=EligibilityCheckQuantity(value="30", qualifier="DAYS"),
                )
            ]
        )

        assert _map(_output(benefits=benefits)).visit_limit is None


class TestCarveout:
    def test_another_payer_administering_the_benefit_is_surfaced(self) -> None:
        benefits = EligibilityCheckBenefits(
            other_or_additional_payer=[
                EligibilityCheckOtherOrAdditionalPayer(
                    related_entities=[
                        EligibilityCheckRelatedEntity(
                            type="PAYER",
                            name=_organization("Magellan Behavioral Health"),
                            payer_id="MBH01",
                        )
                    ]
                )
            ]
        )

        carveout = _map(_output(benefits=benefits)).carveout_administrator

        assert carveout is not None
        assert (carveout.name, carveout.payer_id) == ("Magellan Behavioral Health", "MBH01")

    def test_the_answering_payer_describing_itself_is_not_a_carveout(self) -> None:
        """Otherwise every response would look like a carve-out to itself."""
        benefits = EligibilityCheckBenefits(
            other_or_additional_payer=[
                EligibilityCheckOtherOrAdditionalPayer(
                    related_entities=[
                        EligibilityCheckRelatedEntity(
                            type="PAYER", name=_organization("Aetna"), payer_id="60054"
                        )
                    ]
                )
            ]
        )

        assert _map(_output(benefits=benefits, payer_id="60054")).carveout_administrator is None


class TestPriorAuthorization:
    def test_required_is_reported(self) -> None:
        benefits = EligibilityCheckBenefits(
            statuses=[
                EligibilityCheckEligibilityStatus(
                    status="ACTIVE_COVERAGE", prior_auth_indicator="REQUIRED"
                )
            ]
        )

        assert _map(_output(benefits=benefits)).requires_authorization is True

    def test_not_required_is_reported_as_false_not_unknown(self) -> None:
        benefits = EligibilityCheckBenefits(
            statuses=[
                EligibilityCheckEligibilityStatus(
                    status="ACTIVE_COVERAGE", prior_auth_indicator="NOT_REQUIRED"
                )
            ]
        )

        assert _map(_output(benefits=benefits)).requires_authorization is False

    def test_a_payer_that_said_nothing_leaves_it_unknown(self) -> None:
        benefits = EligibilityCheckBenefits(
            statuses=[EligibilityCheckEligibilityStatus(status="ACTIVE_COVERAGE")]
        )

        assert _map(_output(benefits=benefits)).requires_authorization is None


class TestTheStoredResponse:
    def test_the_payers_own_answer_is_kept(self) -> None:
        """The summary cannot anticipate every later question."""
        outcome = _map(_output(benefits=EligibilityCheckBenefits()))

        assert outcome.stored == {"id": "ec_test"}

    def test_the_plan_name_comes_through(self) -> None:
        outcome = _map(
            _output(benefits=EligibilityCheckBenefits(), plan_name="Aetna Choice POS II")
        )

        assert outcome.plan_name == "Aetna Choice POS II"
        assert outcome.payer_name == "Aetna"
