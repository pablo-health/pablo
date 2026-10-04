# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The availability-parse eval grades what a rule set does, not its shape."""

from __future__ import annotations

from evals.availability_parse.cases import EvalCase, ExpectedRule
from evals.availability_parse.run import _grade


def _dates_case(*dates: str) -> EvalCase:
    return EvalCase(
        name="out_two_fridays",
        phrasing="I'm out September 4th and September 11th",
        description="two specific dates",
        category="positive",
        expected=(ExpectedRule("block_specific_dates", {"dates": list(dates)}),),
    )


def _dates(*dates: str, enforcement: str = "hard", type_id: str | None = None) -> ExpectedRule:
    return ExpectedRule(
        "block_specific_dates",
        {"dates": list(dates)},
        enforcement,
        appointment_type_id=type_id,
    )


class TestEquivalentRuleSets:
    def test_split_dates_match_one_rule_with_both(self) -> None:
        case = _dates_case("2026-09-04", "2026-09-11")
        produced = [_dates("2026-09-11"), _dates("2026-09-04")]

        graded = _grade(case, produced, produced_exclusive=False)

        assert graded["hard_failures"] == []
        assert graded["clean"]

    def test_dates_in_another_order_or_repeated_still_match(self) -> None:
        case = _dates_case("2026-09-04", "2026-09-11")
        produced = [_dates("2026-09-11", "2026-09-04", "2026-09-04")]

        assert _grade(case, produced, produced_exclusive=False)["clean"]

    def test_an_exact_duplicate_rule_adds_nothing(self) -> None:
        case = EvalCase(
            name="no_fridays",
            phrasing="no meetings on Friday",
            description="one day block",
            category="positive",
            expected=(ExpectedRule("block_day_of_week", {"day_of_week": 4}),),
        )
        produced = [
            ExpectedRule("block_day_of_week", {"day_of_week": 4}),
            ExpectedRule("block_day_of_week", {"day_of_week": 4}),
        ]

        assert _grade(case, produced, produced_exclusive=False)["clean"]


class TestDifferentEffectsStillFail:
    def test_different_dates_fail(self) -> None:
        case = _dates_case("2026-09-04", "2026-09-11")
        produced = [_dates("2026-09-04"), _dates("2026-09-18")]

        assert _grade(case, produced, produced_exclusive=False)["hard_failures"]

    def test_a_missing_date_fails(self) -> None:
        case = _dates_case("2026-09-04", "2026-09-11")

        graded = _grade(case, [_dates("2026-09-04")], produced_exclusive=False)

        assert graded["hard_failures"]

    def test_dates_scoped_to_a_type_are_not_merged_with_unscoped_ones(self) -> None:
        case = _dates_case("2026-09-04", "2026-09-11")
        produced = [_dates("2026-09-04"), _dates("2026-09-11", type_id="type-intake")]

        assert _grade(case, produced, produced_exclusive=False)["hard_failures"]

    def test_split_dates_with_mixed_enforcement_are_a_soft_finding(self) -> None:
        case = _dates_case("2026-09-04", "2026-09-11")
        produced = [_dates("2026-09-04"), _dates("2026-09-11", enforcement="soft")]

        graded = _grade(case, produced, produced_exclusive=False)

        assert graded["hard_failures"] == []
        assert graded["soft_findings"]
