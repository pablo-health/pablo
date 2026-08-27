# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Resolve a model-emitted date intent into concrete calendar dates.

The natural-language availability parser never lets the model compute a
date itself -- it only extracts *tokens* (an explicit month-day/year, or a
weekday plus a "this"/"next" qualifier). This module is the deterministic,
clock-free second stage: given those tokens and a caller-supplied reference
date, it resolves the concrete date(s) or explains why it can't.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

_DAYS_PER_WEEK = 7
_YEAR_MONTH_DAY_PARTS = 3
_MONTH_DAY_PARTS = 2
_RANGE_ITEM_COUNT = 2


@dataclass(frozen=True)
class DateToken:
    """One date reference from the model: exactly one of ``explicit`` or
    ``day_of_week`` is set."""

    explicit: str | None = None
    day_of_week: int | None = None
    modifier: str | None = None


@dataclass(frozen=True)
class DateIntent:
    """A parsed ``date_intent`` block: either a start/end pair (``range``)
    or a list of individual dates."""

    items: list[DateToken]
    range: bool = False


@dataclass(frozen=True)
class ResolvedDates:
    """Resolved dates in the engine's own param shape -- either a
    start/end pair or a list of individual dates, never both."""

    start_date: str | None = None
    end_date: str | None = None
    dates: list[str] | None = None


@dataclass(frozen=True)
class UnresolvableDateIntent:
    """A date intent that can't be resolved to a concrete date, with a
    reason meant to be shown to the therapist as-is."""

    reason: str


def resolve_date_intent(
    intent: DateIntent, reference: date
) -> ResolvedDates | UnresolvableDateIntent:
    """Resolve tokens to concrete dates relative to ``reference``."""
    if intent.range:
        return _resolve_range(intent.items, reference)
    return _resolve_list(intent.items, reference)


def _resolve_range(
    items: list[DateToken], reference: date
) -> ResolvedDates | UnresolvableDateIntent:
    if len(items) != _RANGE_ITEM_COUNT:
        return UnresolvableDateIntent(reason="a date range needs both a start and an end")
    start = _resolve_token(items[0], reference)
    if isinstance(start, str):
        return UnresolvableDateIntent(reason=start)
    # The end of a range resolves relative to its own start (so "Friday to
    # Monday" means the Monday after that Friday), not relative to the
    # original reference date.
    end_reference = start if items[1].day_of_week is not None else reference
    end = _resolve_token(items[1], end_reference)
    if isinstance(end, str):
        return UnresolvableDateIntent(reason=end)
    if end < start:
        return UnresolvableDateIntent(reason="the range's end date comes before its start date")
    return ResolvedDates(start_date=start.isoformat(), end_date=end.isoformat())


def _resolve_list(
    items: list[DateToken], reference: date
) -> ResolvedDates | UnresolvableDateIntent:
    if not items:
        return UnresolvableDateIntent(reason="no date was given")
    resolved_dates: list[str] = []
    for token in items:
        resolved = _resolve_token(token, reference)
        if isinstance(resolved, str):
            return UnresolvableDateIntent(reason=resolved)
        resolved_dates.append(resolved.isoformat())
    return ResolvedDates(dates=resolved_dates)


def _resolve_token(token: DateToken, reference: date) -> date | str:
    """Resolve one token to a date, or return an unresolvable-reason string."""
    if token.explicit is not None:
        return _resolve_explicit(token.explicit, reference)
    if token.day_of_week is not None:
        return _resolve_weekday(token.day_of_week, token.modifier, reference)
    return "that date wasn't specific enough to resolve"


def _resolve_explicit(value: str, reference: date) -> date | str:
    parts = value.split("-")
    try:
        if len(parts) == _YEAR_MONTH_DAY_PARTS:
            resolved = date(int(parts[0]), int(parts[1]), int(parts[2]))
        elif len(parts) == _MONTH_DAY_PARTS:
            month, day = int(parts[0]), int(parts[1])
            resolved = date(reference.year, month, day)
            if resolved < reference:
                resolved = date(reference.year + 1, month, day)
        else:
            return f"'{value}' isn't a date this can resolve"
    except ValueError:
        return f"'{value}' isn't a valid date"

    if resolved < reference:
        return "that date has already passed"
    return resolved


def _resolve_weekday(day_of_week: int, modifier: str | None, reference: date) -> date:
    days_ahead = (day_of_week - reference.weekday()) % _DAYS_PER_WEEK
    this_occurrence = reference + timedelta(days=days_ahead)
    if modifier == "next":
        return this_occurrence + timedelta(days=_DAYS_PER_WEEK)
    return this_occurrence
