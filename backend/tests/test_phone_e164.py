# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stored phone number is in the shape a carrier will accept.

``validate_phone`` used to strip whitespace, count digits, and hand back
whatever was typed. That passes every test you would think to write about
validation and then fails at the only moment that matters: the portal invite
route texts a step-up code, and Twilio rejects anything that is not E.164.
The clinician has already clicked by then, and the error names the vendor
rather than the field that accepted the value.

So the contract is stronger than "looks like a phone number": what comes back
is dialable. What is worth pinning:

1. **The ordinary ways a person types a US number all converge.** Somebody
   entering ``(404) 555-1234`` and somebody entering ``404-555-1234`` must end
   up with the same stored value, or the same patient reached twice looks like
   two people.
2. **An explicit country code survives.** A ``+`` means the caller has already
   said which country this is; re-deriving it as American would silently
   redirect an international number.
3. **What cannot be normalized is refused here, not later.** An eleven-digit
   number that does not start with 1 is not a NANP number and not qualified
   either — better a 422 on the form than a 400 from a carrier.
"""

from __future__ import annotations

import pytest
from app.models.validators import validate_phone


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("4047544201", "+14047544201"),
        ("404-754-4201", "+14047544201"),
        ("(404) 754-4201", "+14047544201"),
        ("404.754.4201", "+14047544201"),
        ("  4047544201  ", "+14047544201"),
        # The long-distance 1, with and without punctuation.
        ("14047544201", "+14047544201"),
        ("1 (404) 754-4201", "+14047544201"),
        # Already qualified: kept, punctuation dropped.
        ("+14047544201", "+14047544201"),
        ("+1 404 754 4201", "+14047544201"),
        # A non-US number that says so is not re-read as American.
        ("+442071838750", "+442071838750"),
    ],
)
def test_ordinary_input_becomes_e164(typed: str, stored: str) -> None:
    assert validate_phone(typed) == stored


def test_the_same_number_typed_two_ways_stores_once() -> None:
    assert validate_phone("(404) 754-4201") == validate_phone("404-754-4201")


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_absent_stays_absent(empty: str | None) -> None:
    assert validate_phone(empty) is None


@pytest.mark.parametrize("bad", ["12345", "555-1234", "abc"])
def test_too_short_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="at least"):
        validate_phone(bad)


def test_an_unqualifiable_number_is_refused_at_the_field() -> None:
    """11 digits not starting with 1 is neither NANP nor qualified."""
    with pytest.raises(ValueError, match="country code"):
        validate_phone("44207183875")
