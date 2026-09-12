# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The variance arithmetic, on plain values. No database.

Every case here is one a practice would otherwise get wrong by hand: the
effective-date boundary when a schedule changes mid-year, a percentage of a cent
amount that does not divide evenly, and the difference between "paid correctly"
and "nothing to compare against".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.credentialing.rates import (
    LineVariance,
    Rate,
    VarianceStatus,
    by_cpt,
    by_payer,
    line_variance,
)

_SERVICE = date(2026, 6, 15)


def _fixed(cents: int, *, effective: date = date(2026, 1, 1), end: date | None = None) -> Rate:
    return Rate(
        basis="fixed",
        amount_cents=cents,
        percent=None,
        mpfs_amount_cents=None,
        effective_date=effective,
        end_date=end,
    )


def _percent(
    percent: str,
    mpfs_cents: int | None,
    *,
    effective: date = date(2026, 1, 1),
    end: date | None = None,
) -> Rate:
    return Rate(
        basis="percent_of_mpfs",
        amount_cents=None,
        percent=Decimal(percent),
        mpfs_amount_cents=mpfs_cents,
        effective_date=effective,
        end_date=end,
    )


def _line(allowed: int | None, rates: list[Rate], *, units: int = 1) -> LineVariance:
    return line_variance(
        cpt="90837",
        service_date=_SERVICE,
        units=units,
        allowed_cents=allowed,
        rates=rates,
    )


class TestFixedBasis:
    def test_paid_exactly_the_contracted_rate(self) -> None:
        result = _line(14500, [_fixed(14500)])
        assert result.status is VarianceStatus.MATCH
        assert result.variance_cents == 0
        assert result.underpaid is False

    def test_underpayment_is_negative(self) -> None:
        """The finding. $145 contracted, $103 allowed — the shape of a real cut."""
        result = _line(10300, [_fixed(14500)])
        assert result.status is VarianceStatus.VARIANCE
        assert result.variance_cents == -4200
        assert result.underpaid is True

    def test_overpayment_is_a_variance_but_not_underpayment(self) -> None:
        result = _line(15000, [_fixed(14500)])
        assert result.status is VarianceStatus.VARIANCE
        assert result.variance_cents == 500
        assert result.underpaid is False

    def test_units_multiply_the_contracted_amount(self) -> None:
        """The rate is per unit; the payer's allowed amount is for the line."""
        result = _line(29000, [_fixed(14500)], units=2)
        assert result.contracted_cents == 29000
        assert result.status is VarianceStatus.MATCH


class TestPercentOfMedicareBasis:
    def test_a_percentage_is_computed_from_the_medicare_amount(self) -> None:
        result = _line(12000, [_percent("100", 12000)])
        assert result.contracted_cents == 12000
        assert result.status is VarianceStatus.MATCH

    def test_over_one_hundred_percent_is_ordinary(self) -> None:
        result = _line(17400, [_percent("145", 12000)])
        assert result.contracted_cents == 17400
        assert result.status is VarianceStatus.MATCH

    def test_rounding_is_half_up_to_the_penny_not_truncated(self) -> None:
        """85% of 12001c is 10200.85c. Truncating loses the penny; money rounds.

        The exact-half case below is the one float arithmetic gets wrong, and
        this is a number a practice would put in front of a payer.
        """
        assert _line(10201, [_percent("85", 12001)]).contracted_cents == 10201

        # 50% of an odd cent count lands exactly on the half: 500.5 -> 501.
        exact_half = line_variance(
            cpt="90834",
            service_date=_SERVICE,
            units=1,
            allowed_cents=None,
            rates=[_percent("50", 1001)],
        )
        assert exact_half.contracted_cents == 501

    def test_no_medicare_amount_is_not_computable_not_zero(self) -> None:
        """A percentage rate with nothing to take a percentage OF.

        Reporting zero here would tell a practice it was overpaid by the whole
        allowed amount.
        """
        result = _line(10300, [_percent("85", None)])
        assert result.status is VarianceStatus.NOT_COMPUTABLE
        assert result.contracted_cents is None
        assert result.variance_cents is None
        assert result.underpaid is False


class TestNoRateOnFile:
    def test_reports_no_rate_rather_than_a_zero_variance(self) -> None:
        result = _line(10300, [])
        assert result.status is VarianceStatus.NO_RATE_ON_FILE
        assert result.contracted_cents is None
        assert result.variance_cents is None

    def test_a_rate_for_a_later_period_does_not_apply(self) -> None:
        result = _line(10300, [_fixed(14500, effective=date(2026, 7, 1))])
        assert result.status is VarianceStatus.NO_RATE_ON_FILE


class TestEffectiveDateBoundary:
    def test_the_effective_date_itself_is_covered(self) -> None:
        rate = _fixed(14500, effective=_SERVICE)
        assert _line(14500, [rate]).status is VarianceStatus.MATCH

    def test_the_day_before_is_not(self) -> None:
        from datetime import timedelta  # noqa: PLC0415

        rate = _fixed(14500, effective=_SERVICE + timedelta(days=1))
        assert _line(14500, [rate]).status is VarianceStatus.NO_RATE_ON_FILE

    def test_the_end_date_itself_is_covered(self) -> None:
        rate = _fixed(14500, effective=date(2026, 1, 1), end=_SERVICE)
        assert _line(14500, [rate]).status is VarianceStatus.MATCH

    def test_the_day_after_the_end_date_is_not(self) -> None:
        from datetime import timedelta  # noqa: PLC0415

        rate = _fixed(14500, effective=date(2026, 1, 1), end=_SERVICE - timedelta(days=1))
        assert _line(14500, [rate]).status is VarianceStatus.NO_RATE_ON_FILE

    def test_a_mid_year_increase_prices_each_claim_against_its_own_schedule(self) -> None:
        """The reason rates are versioned instead of edited in place."""
        old = _fixed(12000, effective=date(2026, 1, 1), end=date(2026, 5, 31))
        new = _fixed(14500, effective=date(2026, 6, 1))

        before = line_variance(
            cpt="90837",
            service_date=date(2026, 5, 20),
            units=1,
            allowed_cents=12000,
            rates=[old, new],
        )
        after = line_variance(
            cpt="90837",
            service_date=date(2026, 6, 20),
            units=1,
            allowed_cents=12000,
            rates=[old, new],
        )
        assert before.status is VarianceStatus.MATCH
        assert after.status is VarianceStatus.VARIANCE
        assert after.variance_cents == -2500

    def test_overlapping_periods_take_the_more_recently_agreed_rate(self) -> None:
        """Hand-entered schedules overlap; the answer must still be determinate."""
        stale = _fixed(12000, effective=date(2026, 1, 1))
        agreed_later = _fixed(14500, effective=date(2026, 4, 1))
        result = _line(14500, [stale, agreed_later])
        assert result.contracted_cents == 14500
        assert result.status is VarianceStatus.MATCH


class TestNotAdjudicated:
    def test_no_allowed_amount_yet(self) -> None:
        result = _line(None, [_fixed(14500)])
        assert result.status is VarianceStatus.NOT_ADJUDICATED
        assert result.contracted_cents == 14500
        assert result.variance_cents is None


class TestAggregation:
    def _mixed(self) -> list[LineVariance]:
        return [
            _line(10300, [_fixed(14500)]),  # underpaid 4200
            _line(14500, [_fixed(14500)]),  # match
            _line(9000, [_fixed(10000)]),  # underpaid 1000
            _line(10300, []),  # no rate
            _line(10300, [_percent("85", None)]),  # not computable
            _line(None, [_fixed(14500)]),  # not adjudicated
        ]

    def test_totals_cover_compared_lines_only(self) -> None:
        """A line with no contracted amount must not enter the sums.

        Including it would make allowed minus contracted disagree with the sum
        of the per-line variances, and the report would not add up.
        """
        group = by_cpt(self._mixed())[0]
        assert group.line_count == 6
        assert group.compared_count == 3
        assert group.allowed_cents == 10300 + 14500 + 9000
        assert group.contracted_cents == 14500 + 14500 + 10000
        assert group.variance_cents == -5200

    def test_the_non_comparable_states_are_counted_separately(self) -> None:
        group = by_cpt(self._mixed())[0]
        assert group.no_rate_on_file_count == 1
        assert group.not_computable_count == 1
        assert group.not_adjudicated_count == 1

    def test_underpayment_is_reported_as_a_positive_recoverable_amount(self) -> None:
        group = by_cpt(self._mixed())[0]
        assert group.underpaid_count == 2
        assert group.underpaid_cents == 5200

    def test_by_cpt_splits_codes_and_orders_them(self) -> None:
        lines = [
            line_variance(
                cpt="90837",
                service_date=_SERVICE,
                units=1,
                allowed_cents=10300,
                rates=[_fixed(14500)],
            ),
            line_variance(
                cpt="90834",
                service_date=_SERVICE,
                units=1,
                allowed_cents=9000,
                rates=[_fixed(9000)],
            ),
        ]
        groups = by_cpt(lines)
        assert [g.key for g in groups] == ["90834", "90837"]
        assert groups[0].variance_cents == 0
        assert groups[1].variance_cents == -4200

    def test_by_payer_rolls_each_payers_lines_up(self) -> None:
        report = {
            "payer-b": [_line(10300, [_fixed(14500)])],
            "payer-a": [_line(9000, [_fixed(9000)])],
        }
        groups = by_payer(report)
        assert [g.key for g in groups] == ["payer-a", "payer-b"]
        assert groups[0].variance_cents == 0
        assert groups[1].underpaid_cents == 4200

    def test_an_empty_group_reports_zeroes_rather_than_dividing_by_nothing(self) -> None:
        groups = by_cpt([])
        assert groups == []


class TestRateValueObject:
    @pytest.mark.parametrize(
        ("service", "expected"),
        [
            (date(2025, 12, 31), False),
            (date(2026, 1, 1), True),
            (date(2026, 12, 31), True),
            (date(2027, 1, 1), False),
        ],
    )
    def test_covers_is_inclusive_at_both_ends(self, service: date, expected: bool) -> None:
        rate = _fixed(14500, effective=date(2026, 1, 1), end=date(2026, 12, 31))
        assert rate.covers(service) is expected

    def test_an_open_ended_rate_covers_everything_after_its_start(self) -> None:
        rate = _fixed(14500, effective=date(2026, 1, 1))
        assert rate.covers(date(2099, 1, 1)) is True
