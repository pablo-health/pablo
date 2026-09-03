# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Money crosses the human boundary exactly once, and exactly.

Fees are stored in cents and typed in dollars. Every screen that shows a fee is
a chance to multiply or divide by a hundred in the wrong direction, and the
result looks plausible either way — a fee field showing 160 when the column
holds 160 cents reads fine right up until the superbill says $1.60.

Bug classes covered:
  * float arithmetic losing a penny: 160.10 * 100 is 16009.999... in binary
    floating point, and int() of that is 16009;
  * "unset" collapsing into "free" — a fee nobody has set is not $0, and a
    superbill should not claim a visit was free because a field was blank;
  * whole amounts rendering as $160.00 in a list where $160 is what people
    read;
  * a blank form field silently becoming a zero fee.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.money import cents_to_dollars, dollars_to_cents, format_money


class TestDollarsToCents:
    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            (160, 16000),
            ("160", 16000),
            (Decimal("160"), 16000),
            ("160.50", 16050),
            (0, 0),
            ("0.01", 1),
        ],
    )
    def test_converts_what_a_person_types(self, typed: object, expected: int) -> None:
        assert dollars_to_cents(typed) == expected  # type: ignore[arg-type]

    def test_does_not_lose_a_penny_to_binary_floating_point(self) -> None:
        # The regression this module exists for. 160.10 * 100 == 16009.999...
        # as a float, so a naive int() stores 16009 and the practice is short
        # a cent on every invoice for this type.
        assert dollars_to_cents(160.10) == 16010
        assert dollars_to_cents("160.10") == 16010

    def test_a_half_cent_rounds_the_way_a_person_expects(self) -> None:
        # Half-up, not banker's rounding: explaining "0.005 rounded down
        # because the digit before it was even" in a billing conversation is
        # not a position worth defending.
        assert dollars_to_cents("0.005") == 1
        assert dollars_to_cents("0.015") == 2

    def test_an_unset_fee_stays_unset(self) -> None:
        assert dollars_to_cents(None) is None

    @pytest.mark.parametrize("bad", ["", "  ", "abc", "$160", "nan", "inf"])
    def test_junk_raises_rather_than_becoming_zero(self, bad: str) -> None:
        # A blank or malformed form field must surface as an error. Quietly
        # storing 0 would tell the world this appointment type is free.
        with pytest.raises(ValueError, match="not a valid amount"):
            dollars_to_cents(bad)


class TestCentsToDollars:
    def test_returns_an_exact_decimal_not_a_float(self) -> None:
        amount = cents_to_dollars(16010)

        assert amount == Decimal("160.10")
        assert isinstance(amount, Decimal)

    def test_survives_a_round_trip(self) -> None:
        for cents in (0, 1, 16000, 16010, 999999):
            assert dollars_to_cents(cents_to_dollars(cents)) == cents

    def test_an_unset_fee_stays_unset(self) -> None:
        assert cents_to_dollars(None) is None


class TestFormatMoney:
    def test_drops_the_trailing_zeros_on_a_whole_amount(self) -> None:
        # A fee list reads "$160", not "$160.00".
        assert format_money(16000) == "$160"

    def test_keeps_the_cents_when_there_are_any(self) -> None:
        assert format_money(16050) == "$160.50"

    def test_groups_thousands(self) -> None:
        assert format_money(160000) == "$1,600"

    def test_zero_says_free(self) -> None:
        assert format_money(0) == "Free"

    def test_unset_is_blank_not_zero(self) -> None:
        # The distinction the whole module turns on: nobody set a fee, which
        # is not a claim that the visit costs nothing.
        assert format_money(None) == ""
        assert format_money(None) != format_money(0)

    def test_the_unset_and_free_wording_is_overridable(self) -> None:
        assert format_money(None, unset="Not set") == "Not set"
        assert format_money(0, free="No charge") == "No charge"
