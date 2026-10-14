# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Listed medications the visit may have changed, found without a model.

A stop or a dose change the call misses leaves the list showing what is no
longer prescribed, so the call is not left to notice one on its own. Before
it runs, the transcript is read for each listed medication's name; where a
line naming it, or the line after, gives a different dose or says it was
stopped, the medication is put to the call to decide. The call may still
propose nothing for it (a client's own stop the clinician has not addressed
is left as listed), but then has to say why, from a fixed set of reasons.

This only finds candidates. It never proposes anything, and a medication it
does not find is still the call's to propose as before.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .families import CLIENT_ALONE, MEDICATIONS, listed_medication
from .models import MedicationKept

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..notes.chart_context import ChartContext, ChartMedication
    from .models import DraftedProposal

#: The reply's list of medications put to the call that the visit leaves as listed.
MEDICATIONS_KEPT = "medications_kept"

#: The reply's medication changes (the medication family's ``reply_key``).
MEDICATION_CHANGES = "medication_changes"

#: A client's own stop the clinician has not addressed: a reason, not a miss.
CLIENT_STOP = "client-reported stop, not addressed by the clinician this visit"

#: Why a medication put to the call is left as listed. The list is what is prescribed.
KEPT_REASONS = (
    "taken as listed",
    CLIENT_STOP,
    "only discussed or considered for later",
    "other",
)

#: Said near a medication's name, it may no longer be taken.
_STOPPED = re.compile(
    r"\b(?:stop(?:ped|ping|s)?|quit|discontinu\w*|ran out|run out|no longer|came off"
    r"|went off|gave (?:it )?up|(?:haven't|have not|hasn't|has not|didn't|did not"
    r"|don't|do not|not) (?:been )?(?:tak|us)\w*)\b",
    re.IGNORECASE,
)

_UNITS = frozenset({"mg", "milligram", "milligrams", "mcg", "microgram", "micrograms"})
_DIGITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}
_TEENS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_HUNDRED = 100
_TOKEN = re.compile(r"\d+(?:\.\d+)?|\.\d+|[a-z']+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().replace(",", "").replace("-", " "))


def _at(tokens: list[str], i: int) -> str:
    return tokens[i] if i < len(tokens) else ""


def _under_a_hundred(tokens: list[str], i: int) -> tuple[int, int] | None:
    """ "seven", "fifteen", "twenty" or "twenty five" from ``tokens[i]``, and the index after."""
    word = _at(tokens, i)
    if word in _DIGITS:
        return _DIGITS[word], i + 1
    if word in _TEENS:
        return _TEENS[word], i + 1
    if word in _TENS:
        unit = _DIGITS.get(_at(tokens, i + 1), 0)
        return (_TENS[word] + unit, i + 2) if unit else (_TENS[word], i + 1)
    return None


def _decimal(tokens: list[str], whole: int, i: int) -> tuple[float, int]:
    """``whole`` and the digits spoken after "point" at ``tokens[i]``."""
    digits = ""
    j = i + 1
    while _at(tokens, j) in _DIGITS:
        digits += str(_DIGITS[tokens[j]])
        j += 1
    return float(f"{whole}.{digits}"), j


def _spoken(tokens: list[str], i: int) -> tuple[float, int] | None:
    """A number said in words from ``tokens[i]``: "twenty", "a hundred and twenty five",
    "two hundred", "one fifty" (as a dose is often said), "zero point five", "point five"."""
    if tokens[i] == "point" and _at(tokens, i + 1) in _DIGITS:
        return _decimal(tokens, 0, i)
    if tokens[i] == "a" and _at(tokens, i + 1) == "hundred":
        value, j = _HUNDRED, i + 2
    else:
        first = _under_a_hundred(tokens, i)
        if first is None:
            return None
        value, j = first
        rest = _under_a_hundred(tokens, j)
        if value in _DIGITS.values() and _at(tokens, j) == "hundred":
            value, j = value * _HUNDRED, j + 1
        elif value in _DIGITS.values() and rest is not None and rest[0] >= _TEENS["ten"]:
            return float(value * _HUNDRED + rest[0]), rest[1]
    if value >= _HUNDRED:
        after_and = j + 1 if _at(tokens, j) == "and" else j
        rest = _under_a_hundred(tokens, after_and)
        if rest is not None:
            value, j = value + rest[0], rest[1]
    if _at(tokens, j) == "point" and _at(tokens, j + 1) in _DIGITS:
        return _decimal(tokens, value, j)
    return float(value), j


def _number_at(tokens: list[str], i: int) -> tuple[float, int] | None:
    """The number written or said from ``tokens[i]``, and the index after it."""
    word = tokens[i]
    if word[0].isdigit() or word[0] == ".":
        return float(word), i + 1
    if word == "half":
        return 0.5, i + 1
    return _spoken(tokens, i)


def _numbers(tokens: list[str]) -> list[tuple[float, int, int]]:
    """Each number in ``tokens``, with where it starts and the index after it."""
    found = []
    i = 0
    while i < len(tokens):
        number = _number_at(tokens, i)
        if number is None:
            i += 1
            continue
        found.append((number[0], i, number[1]))
        i = number[1]
    return found


def _doses(text: str, name: str) -> set[float]:
    """The doses ``text`` states: a number followed by a unit, or just after the name."""
    tokens = _tokens(text)
    return {
        value
        for value, start, end in _numbers(tokens)
        if _at(tokens, end) in _UNITS or name in tokens[max(0, start - 2) : start]
    }


def _mentioned_as_changed(medication: ChartMedication, segments: Mapping[int, str]) -> bool:
    words = _tokens(medication.name)
    if not words:
        return False
    name = words[0]
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    listed = {value for value, _, _ in _numbers(_tokens(medication.dose or ""))}
    ordered = sorted(segments)
    for position, n in enumerate(ordered):
        if not pattern.search(segments[n]):
            continue
        near = segments[n]
        if position + 1 < len(ordered):
            near += "\n" + segments[ordered[position + 1]]
        if _STOPPED.search(near) or (listed and _doses(near, name) - listed):
            return True
    return False


def medications_to_decide(chart: ChartContext, segments: Mapping[int, str]) -> list[str]:
    """The listed medications the visit names near a different dose or a word saying
    it was stopped, as the list names them, in the list's order."""
    return [m.name for m in chart.medications if _mentioned_as_changed(m, segments)]


def kept_medications(
    reply: Mapping[str, Any],
    chart: ChartContext,
    to_decide: Sequence[str],
    proposals: Sequence[DraftedProposal],
) -> tuple[MedicationKept, ...]:
    """Each medication put to the call that it proposed no change to, with the reason it
    gave under ``medications_kept``. A reason outside ``KEPT_REASONS``, or none, is empty."""
    given: dict[str, MedicationKept] = {}
    raw = reply.get(MEDICATIONS_KEPT)
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        listed = listed_medication(chart, str(item.get("drug_name") or ""))
        reason = str(item.get("reason") or "")
        if listed is None or reason not in KEPT_REASONS:
            continue
        note = str(item.get("note") or "").strip()
        given.setdefault(listed.name.lower(), MedicationKept(listed.name, reason, note))
    # A change the call says the client made alone is not proposed (the family drops
    # it); it was considered all the same, and that is its reason.
    changes = reply.get(MEDICATION_CHANGES)
    for item in changes if isinstance(changes, list) else []:
        if not isinstance(item, Mapping) or item.get("decided_by") != CLIENT_ALONE:
            continue
        listed = listed_medication(chart, str(item.get("drug_name") or ""))
        if listed is not None:
            note = str(item.get("what_changed") or "").strip()
            given.setdefault(listed.name.lower(), MedicationKept(listed.name, CLIENT_STOP, note))
    proposed = {p.item_key.lower() for p in proposals if p.field_key == MEDICATIONS}
    return tuple(
        given.get(name.lower(), MedicationKept(drug_name=name, reason=""))
        for name in to_decide
        if name.lower() not in proposed
    )
