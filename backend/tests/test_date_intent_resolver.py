# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the pure date-intent resolver -- no LLM, no wall clock.

Every case pins a fixed reference date (2026-08-26, a Wednesday) and
checks plain date arithmetic, matching the tie-break rules the natural-
language rule parser relies on.
"""

from __future__ import annotations

from datetime import date

from app.scheduling_engine.services.date_intent import (
    DateIntent,
    DateToken,
    ResolvedDates,
    UnresolvableDateIntent,
    resolve_date_intent,
)

REFERENCE = date(2026, 8, 26)  # Wednesday


def test_bare_weekday_with_no_modifier_is_the_next_occurrence_on_or_after_reference() -> None:
    intent = DateIntent(items=[DateToken(day_of_week=4)])
    result = resolve_date_intent(intent, REFERENCE)
    assert result == ResolvedDates(dates=["2026-08-28"])


def test_this_weekday_is_the_same_as_bare_weekday() -> None:
    intent = DateIntent(items=[DateToken(day_of_week=4, modifier="this")])
    result = resolve_date_intent(intent, REFERENCE)
    assert result == ResolvedDates(dates=["2026-08-28"])


def test_next_weekday_skips_a_full_week_past_the_bare_occurrence() -> None:
    intent = DateIntent(items=[DateToken(day_of_week=4, modifier="next")])
    result = resolve_date_intent(intent, REFERENCE)
    assert result == ResolvedDates(dates=["2026-09-04"])


def test_bare_weekday_matching_the_reference_day_resolves_to_today() -> None:
    intent = DateIntent(items=[DateToken(day_of_week=2)])  # Wednesday
    result = resolve_date_intent(intent, REFERENCE)
    assert result == ResolvedDates(dates=["2026-08-26"])


def test_explicit_month_day_with_no_year_rolls_to_next_year_once_passed() -> None:
    intent = DateIntent(items=[DateToken(explicit="03-03")])
    result = resolve_date_intent(intent, REFERENCE)
    assert result == ResolvedDates(dates=["2027-03-03"])


def test_explicit_full_date_before_reference_is_unresolvable() -> None:
    intent = DateIntent(items=[DateToken(explicit="2026-08-01")])
    result = resolve_date_intent(intent, REFERENCE)
    assert isinstance(result, UnresolvableDateIntent)
    assert "passed" in result.reason


def test_range_friday_to_monday_resolves_end_relative_to_start() -> None:
    intent = DateIntent(
        items=[DateToken(day_of_week=4), DateToken(day_of_week=0)],
        range=True,
    )
    result = resolve_date_intent(intent, REFERENCE)
    assert result == ResolvedDates(start_date="2026-08-28", end_date="2026-08-31")


def test_range_whose_end_resolves_before_its_start_is_unresolvable() -> None:
    # Start is pushed out to next month by an explicit "next <weekday>",
    # while the end is a fixed explicit date that lands before it.
    intent = DateIntent(
        items=[
            DateToken(day_of_week=0, modifier="next"),  # next Monday -> 2026-09-07
            DateToken(explicit="08-27"),  # 2026-08-27, before the start above
        ],
        range=True,
    )
    result = resolve_date_intent(intent, REFERENCE)
    assert isinstance(result, UnresolvableDateIntent)
    assert "before" in result.reason
