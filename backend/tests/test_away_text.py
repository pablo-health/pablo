# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The words an away acknowledgment is made of.

What these would catch: a suggested body that drops the return date or writes
the covering colleague in before they answered, a cover sentence sent for a
colleague who has not accepted or missing for one who has, a cover sentence
left over in a body saved before it moved to send time, an acknowledgment that
adds anything else to the reviewed text besides the safety lines, a refill
line that names anyone but the accepted colleague (or, without one, anything
but the return date), and a time of day appearing anywhere.
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
    with_cover,
    without_cover,
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


def test_the_suggested_body_never_names_the_covering_colleague() -> None:
    """They have only been asked; the sentence is added on send once they accept."""
    body = default_body(_MONDAY, "Robin Avery", "Sam Okafor")
    assert body == default_body(_MONDAY, "Robin Avery")
    assert "Sam Okafor" not in body


_SAVED = "Thanks for your message. Robin Avery is away and back on Monday, November 2."
_COVER = "Sam Okafor is covering in the meantime."


@pytest.mark.parametrize(
    ("covering", "accepted", "expected"),
    [
        pytest.param("Sam Okafor", True, f"{_SAVED} {_COVER}", id="accepted"),
        pytest.param("Sam Okafor", False, _SAVED, id="asked-or-declined"),
        pytest.param(None, False, _SAVED, id="nobody-named"),
    ],
)
def test_the_cover_sentence_follows_the_colleagues_answer(
    covering: str | None, accepted: bool, expected: str
) -> None:
    assert with_cover(_SAVED, covering, accepted=accepted) == expected


@pytest.mark.parametrize("accepted", [True, False])
def test_a_body_saved_with_the_cover_sentence_is_sent_with_at_most_one(accepted: bool) -> None:
    """Bodies saved before the sentence moved to send time still carry it."""
    sent = with_cover(f"{_SAVED} {_COVER}", "Sam Okafor", accepted=accepted)
    assert sent == (f"{_SAVED} {_COVER}" if accepted else _SAVED)


def test_taking_the_cover_sentence_out_leaves_the_clinicians_own_words() -> None:
    edited = f"Back on the 2nd.\n{_COVER} Write to the front desk for scheduling."
    assert without_cover(edited, "Sam Okafor") == (
        "Back on the 2nd.\nWrite to the front desk for scheduling."
    )
    assert without_cover(edited, None) == edited
    assert without_cover(f"One.\n\n{_COVER}\n\nTwo.", "Sam Okafor") == "One.\n\nTwo."
    assert without_cover("One.\n\nTwo.", "Sam Okafor") == "One.\n\nTwo."
    assert without_cover(f"{_SAVED} Lee Park is covering in the meantime.", "Sam Okafor") == (
        f"{_SAVED} Lee Park is covering in the meantime."
    )


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
        ("Sam Okafor", "Your request is in. Sam Okafor will review it."),
    ],
)
def test_the_refill_line_names_the_accepted_colleague_or_else_the_return_date(
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
