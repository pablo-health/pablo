# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Codes, times and diagnosis codes the clinician dictated, as the transcriber wrote them.

Speech recognition is good with words and poor with numbers said in a row.
Measured on synthesized visits run through the recording path:

- Two billing codes said together become one number: "99214 plus 90836"
  is written "992-149-0836".
- A dictated window runs together with its minutes: "10:14 to 10:55, 41
  minutes" is written "1014 to 105541 minutes", and "3:02 to 3:41" is
  written "3:02 to 3. 41".
- A diagnosis code loses its decimal and can swallow the next word: "F32.1,
  seven months" is written "F32 17 months"; "F41.1" is written "F41 1".

The clinician's codes and times enter the note only through what they
dictate, so before any model reads it, this module rewrites those forms
back, in the clinician's dictation only: the lines after the client's last
turn, and anything dictated after the recording. The client's words are
never touched.

Each rewrite is made only when the digits allow one reading:

- A run of ten digits becomes two codes only when both halves are codes a
  visit is billed with (:data:`PROCEDURE_CODES`); five digits split by
  separators become one such code.
- A window's times are rewritten only when one side already reads as a
  clock time, so "from 150 to 200 milligrams" is left alone; a run-together
  end time and minute count is split only when the minutes fit in the
  window.
- A decimal is never invented for a diagnosis code. "F32 17" becomes
  "F32.1, 7" only when F32.1 is a code the chart or the note already
  holds. Otherwise the words stay as heard and the code is *unparsed*: a
  diagnosis drafted with a code under that stem that the dictation does not
  state plainly gets :data:`CODE_NOT_STATED` (:meth:`DictatedNumbers.unparsed_code`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .client_present import DICTATED_HEADING, MIN_BOUNDARY_CLIENT_WORDS, is_client_speaker
from .mdm import ADD_ON_CODES, EM_CODES

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .chart_context import ChartContext

PROCEDURE_CODES = EM_CODES | ADD_ON_CODES | {"90791", "90792"}
"""Codes a two-code run may split into: office E/M, the psychotherapy add-ons,
and the psychiatric evaluations."""

CODE_NOT_STATED = "Not stated."
CLINICIAN_SPEAKER = "Therapist"

_MAX_MINUTES = 180
_MAX_WINDOW_MINUTES = 240
_HALF_DAY_MINUTES = 12 * 60

_TURN = re.compile(r"^(?:\[[\d:]+\]\s*)?([A-Za-z][A-Za-z .'-]{0,30}):\s*(.*)$")

_DIGIT_RUN = re.compile(r"(?<![\w:.])\d+(?:(?:[-.]\s?|\s)\d+)*(?![\w:])")
_CODE_LENGTH = 5

_CLOCK = r"\d{1,2}:\d{2}"
_LOOSE_CLOCK = r"\d{1,2}(?::|\.\s?|\s)\d{2}|\d{3,4}"
_CONNECTOR = r"\s*(?:to|till|til|until|through|-|\u2013)\s*"
_UNIT = r"(?:minutes?|mins?)\b"
_WINDOW_WITH_MINUTES = re.compile(
    rf"(?<![\d:])(?P<start>{_LOOSE_CLOCK})(?P<conn>{_CONNECTOR})"
    rf"(?P<run>\d{{1,2}}:?\d{{4,5}})\s*(?P<unit>{_UNIT})",
    re.IGNORECASE,
)
_END_WITH_MINUTES = re.compile(
    rf"(?<![\d:])(?P<run>\d{{1,2}}:\d{{4,5}})\s*(?P<unit>{_UNIT})", re.IGNORECASE
)
_WINDOW_START = re.compile(rf"(?<![\d:])(?:{_LOOSE_CLOCK}){_CONNECTOR}$", re.IGNORECASE)
_WINDOW = re.compile(
    rf"(?<![\d:])(?P<start>{_LOOSE_CLOCK})(?P<conn>{_CONNECTOR})(?P<end>{_LOOSE_CLOCK})"
    rf"(?![\d:])(?!\s*{_UNIT})",
    re.IGNORECASE,
)

_ICD_HEARD = re.compile(r"\b(?P<stem>[A-Z]\d{2})(?: +)?(?P<digits>\d{1,3})\b")
_ICD_STATED = re.compile(r"\b[A-Z]\d{2}\.\d{1,2}\b")
_MAX_DECIMALS = 2


@dataclass(frozen=True)
class DictatedNumbers:
    """The transcript with the dictation's numbers rewritten, and what could not be read."""

    content: str
    unparsed_stems: frozenset[str] = frozenset()
    """Diagnosis code stems ("F32") the dictation said in a form with no single reading."""
    stated_codes: frozenset[str] = frozenset()
    """Diagnosis codes the dictation states plainly after the rewrite, and the
    known ones: a chart's code is never unset."""

    def unparsed_code(self, code: str | None) -> bool:
        """Whether a drafted diagnosis code comes from a code the dictation garbled."""
        text = (code or "").strip().upper()
        return bool(text) and text[:3] in self.unparsed_stems and text not in self.stated_codes


def known_diagnosis_codes(
    chart: ChartContext | None, current_note: Mapping[str, Any] | None
) -> set[str]:
    """The diagnosis codes on the chart's problem list and in the note being redrafted."""
    codes = {p.icd10_code for p in (chart.problems if chart else ()) if p.icd10_code}
    return codes | {item["code"] for item in _diagnosis_items(current_note) if item.get("code")}


def without_unparsed_codes(content: dict[str, Any], dictated: DictatedNumbers) -> dict[str, Any]:
    """``content`` with every diagnosis code read from a garbled dictation set to
    :data:`CODE_NOT_STATED`, so the clinician states it rather than signs a guess."""
    for item in _diagnosis_items(content):
        if dictated.unparsed_code(item.get("code")):
            item["code"] = CODE_NOT_STATED
    return content


def _diagnosis_items(content: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Every diagnosis item (``{label, code, status}``) in a note's sections."""
    return [
        item
        for section in (content or {}).values()
        if isinstance(section, dict)
        for value in section.values()
        if isinstance(value, list)
        for item in value
        if isinstance(item, dict) and "label" in item and "code" in item
    ]


def normalise_dictated_numbers(content: str, known_codes: Iterable[str] = ()) -> DictatedNumbers:
    """Rewrite the dictation's codes and times in ``content``.

    ``known_codes`` are the diagnosis codes the chart or the note already
    holds: the only codes a garbled one may be read as.
    """
    known = frozenset(c.strip().upper() for c in known_codes if c and c.strip())
    recording, found, after = content.partition(DICTATED_HEADING)
    lines = recording.split("\n")
    tail = set(_tail_indexes(lines))
    unparsed: set[str] = set()
    out = [_normalise(line, known, unparsed) if i in tail else line for i, line in enumerate(lines)]
    later = _normalise(after, known, unparsed) if found else ""
    dictation = "\n".join([*(out[i] for i in sorted(tail)), later])
    return DictatedNumbers(
        "\n".join(out) + found + later,
        frozenset(unparsed),
        known | frozenset(_ICD_STATED.findall(dictation)),
    )


def _tail_indexes(lines: list[str]) -> list[int]:
    """The clinician's lines after the client's last turn.

    With no client turn at all, only lines the clinician's channel is
    labelled with: an unlabelled recording may hold the client's voice too.
    """
    speakers: list[str | None] = []
    speaker: str | None = None
    last_client = -1
    for i, line in enumerate(lines):
        if match := _TURN.match(line.strip()):
            speaker = match.group(1).strip()
            if is_client_speaker(speaker) and (
                len(match.group(2).split()) >= MIN_BOUNDARY_CLIENT_WORDS
            ):
                last_client = i
        speakers.append(speaker)
    return [
        i
        for i, who in enumerate(speakers)
        if i > last_client
        and who is not None
        and not is_client_speaker(who)
        and (last_client >= 0 or who == CLINICIAN_SPEAKER)
    ]


def _normalise(text: str, known: frozenset[str], unparsed: set[str]) -> str:
    text = _DIGIT_RUN.sub(_procedure_codes, text)
    text = _WINDOW_WITH_MINUTES.sub(_window_with_minutes, text)
    text = _END_WITH_MINUTES.sub(_end_with_minutes, text)
    text = _WINDOW.sub(_window, text)
    return _ICD_HEARD.sub(lambda m: _diagnosis_code(m, known, unparsed), text)


# ---------------------------------------------------------------------------
# Procedure codes
# ---------------------------------------------------------------------------


def _procedure_codes(match: re.Match[str]) -> str:
    run = match.group(0)
    digits = re.sub(r"\D", "", run)
    if all(len(part) == _CODE_LENGTH for part in re.findall(r"\d+", run)):
        return run
    if len(digits) == _CODE_LENGTH and digits in PROCEDURE_CODES:
        return digits
    first, second = digits[:_CODE_LENGTH], digits[_CODE_LENGTH:]
    if len(digits) == 2 * _CODE_LENGTH and {first, second} <= PROCEDURE_CODES:
        joiner = "plus" if second in ADD_ON_CODES else "and"
        return f"{first} {joiner} {second}"
    return run


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Clock:
    hour: int
    minute: int

    def __str__(self) -> str:
        return f"{self.hour}:{self.minute:02d}"

    def minutes_until(self, later: _Clock) -> int:
        """Minutes from this time to ``later`` on a twelve-hour clock."""
        diff = (later.hour * 60 + later.minute) - (self.hour * 60 + self.minute)
        return diff % _HALF_DAY_MINUTES


def _clock(hour: str, minute: str) -> _Clock | None:
    h, m = int(hour), int(minute)
    return _Clock(h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None  # noqa: PLR2004


def _loose_clock(text: str) -> _Clock | None:
    """A clock time from "10:14", "1014", "3. 41" or "3 41"."""
    digits = re.sub(r"\D", "", text)
    if len(digits) not in (3, 4):
        return None
    return _clock(digits[:-2], digits[-2:])


def _end_and_minutes(run: str) -> list[tuple[_Clock, int]]:
    """Every reading of a run-together end time and minute count."""
    hour_lengths = [run.index(":")] if ":" in run else [1, 2]
    digits = run.replace(":", "")
    readings = []
    for h in hour_lengths:
        clock = _clock(digits[:h], digits[h : h + 2])
        count = digits[h + 2 :]
        if clock and 2 <= len(count) <= 3 and not count.startswith("0"):  # noqa: PLR2004
            minutes = int(count)
            if 0 < minutes <= _MAX_MINUTES:
                readings.append((clock, minutes))
    return readings


def _window_with_minutes(match: re.Match[str]) -> str:
    start = _loose_clock(match["start"])
    if start is None:
        return match.group(0)
    fits = [
        (end, minutes)
        for end, minutes in _end_and_minutes(match["run"])
        if 0 < minutes <= start.minutes_until(end) <= _MAX_WINDOW_MINUTES
    ]
    if len(fits) != 1:
        return match.group(0)
    end, minutes = fits[0]
    return f"{start}{match['conn']}{end}, {minutes} {match['unit']}"


def _end_with_minutes(match: re.Match[str]) -> str:
    """An end time with no start: a window's end was already read, or left, above."""
    readings = _end_and_minutes(match["run"])
    if len(readings) != 1 or _WINDOW_START.search(match.string[: match.start()]):
        return match.group(0)
    end, minutes = readings[0]
    return f"{end}, {minutes} {match['unit']}"


def _window(match: re.Match[str]) -> str:
    said = match.group(0)
    if not re.search(_CLOCK, said):
        return said
    start, end = _loose_clock(match["start"]), _loose_clock(match["end"])
    if start is None or end is None or not 0 < start.minutes_until(end) <= _MAX_WINDOW_MINUTES:
        return said
    return f"{start}{match['conn']}{end}"


# ---------------------------------------------------------------------------
# Diagnosis codes
# ---------------------------------------------------------------------------


def _diagnosis_code(match: re.Match[str], known: frozenset[str], unparsed: set[str]) -> str:
    stem, digits = match["stem"], match["digits"]
    readings = [
        (f"{stem}.{digits[:n]}", digits[n:])
        for n in range(1, min(_MAX_DECIMALS, len(digits)) + 1)
        if f"{stem}.{digits[:n]}" in known
    ]
    if len(readings) != 1:
        unparsed.add(stem)
        return match.group(0)
    code, rest = readings[0]
    return f"{code}, {rest}" if rest else code


__all__ = [
    "CODE_NOT_STATED",
    "PROCEDURE_CODES",
    "DictatedNumbers",
    "known_diagnosis_codes",
    "normalise_dictated_numbers",
    "without_unparsed_codes",
]
