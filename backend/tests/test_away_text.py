# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The words an away acknowledgment is made of.

What these would catch: a suggested body that drops the return date or the
covering colleague, an acknowledgment that adds anything to the reviewed text
besides the safety lines, a refill line that names a reviewer other than the
return date or the colleague, and a time of day appearing anywhere.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from app.away.schemas import AwayDates, CreateAwayPeriodRequest
from app.away.text import (
    CRISIS_LINE,
    EMERGENCY_LINE,
    SAFETY_LINES,
    compose,
    default_body,
    format_day,
    refill_line,
)
from pydantic import ValidationError

_MONDAY = date(2026, 11, 2)
_NO_TIME = re.compile(r"\d{1,2}:\d{2}|\b(am|pm)\b", re.IGNORECASE)


def test_a_day_reads_as_weekday_and_date() -> None:
    assert format_day(_MONDAY) == "Monday, November 2"


def test_the_suggested_body_names_the_return_date() -> None:
    assert default_body(_MONDAY, "Robin Avery", None) == (
        "Thanks for your message. Robin Avery is away and back on Monday, November 2."
    )


def test_the_suggested_body_names_the_covering_colleague() -> None:
    body = default_body(_MONDAY, "Robin Avery", "Sam Okafor")
    assert body.endswith("Sam Okafor is covering in the meantime.")


def test_a_clinician_with_no_name_on_file_is_your_clinician() -> None:
    assert default_body(_MONDAY, None, None).startswith(
        "Thanks for your message. Your clinician is away"
    )


def test_the_safety_lines_carry_911_and_988() -> None:
    assert EMERGENCY_LINE == "If this is an emergency, call 911."
    assert "988" in CRISIS_LINE
    assert f"{EMERGENCY_LINE}\n{CRISIS_LINE}" == SAFETY_LINES


def test_composing_adds_the_safety_lines_and_nothing_else() -> None:
    assert compose("  Back Monday.  ") == f"Back Monday.\n\n{SAFETY_LINES}"


@pytest.mark.parametrize(
    ("covering", "expected"),
    [
        (None, "Your request is in. It will be reviewed by Monday, November 2."),
        (
            "Sam Okafor",
            "Your request is in. It will be reviewed by Monday, November 2 or by Sam Okafor.",
        ),
    ],
)
def test_the_refill_line_names_the_return_date_or_the_colleague(
    covering: str | None, expected: str
) -> None:
    line = refill_line(_MONDAY, covering)
    assert line == expected
    assert not _NO_TIME.search(compose(line))


def test_the_return_date_defaults_to_the_day_after_the_last_day_away() -> None:
    dates = AwayDates(start_date=date(2026, 10, 26), end_date=date(2026, 10, 30))
    assert dates.resolved_return_date == date(2026, 10, 31)


@pytest.mark.parametrize(
    ("start", "end", "back"),
    [
        (date(2026, 10, 30), date(2026, 10, 26), None),
        (date(2026, 10, 26), date(2026, 10, 30), date(2026, 10, 30)),
    ],
    ids=["ends-before-it-starts", "back-on-a-day-away"],
)
def test_dates_out_of_order_are_refused(start: date, end: date, back: date | None) -> None:
    with pytest.raises(ValidationError):
        AwayDates(start_date=start, end_date=end, return_date=back)


def test_the_acknowledgment_is_on_unless_turned_off() -> None:
    request = CreateAwayPeriodRequest(
        start_date=date(2026, 10, 26), end_date=date(2026, 10, 30), acknowledgment_text="Away."
    )
    assert request.acknowledgment_enabled is True
