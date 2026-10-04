# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Naming a new client made from a calendar event.

A clinician who adds a client from an event types the name beside the
event's title. Two things here help with that, and both build on the
readings in ``titles``:

- ``suggested_name`` fills in the name fields with what the title plainly
  says: both parts of a full name ("Session with Casey Morgan"), or the
  whole part of a name with the other cut short ("Jane S." gives a first
  name only). Initials, one word, two names, or anything else uncertain
  gives nothing, and the fields start empty.
- ``name_part`` is what a chart is called when the clinician leaves the
  fields empty: the part of the title that looks like a name, with session
  wording taken out. Such a chart has no last name, which is how it shows as
  needing one (``Patient.needs_name``).

Neither ever puts session wording ("Session", "Therapy", "Intake") in a name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .titles import NOT_NAME_WORDS, TitleReading, title_readings

#: A word that reads as part of a person's name: letters, with an inner
#: hyphen or apostrophe allowed ("Mary-Kate", "O'Neil").
_NAME_WORD = re.compile("[^\\W\\d_]+(?:['\u2019-][^\\W\\d_]+)*")
#: Left over around the name part once session wording is gone.
_EDGES = " ,;:|/-\u2013\u2014()[]"
#: A name word cut to its first letter: "J." or "J". Same shape ``titles`` reads.
_CUT_WORD = re.compile(r"[A-Za-z]\.?")
_FIRST_LAST = 2
_NAME_MAX = 255


@dataclass(frozen=True)
class SuggestedName:
    """A new client's name to fill in. A part the title doesn't give is empty."""

    first_name: str
    last_name: str


def _text(reading: TitleReading) -> str:
    return reading.full_name or reading.initials or reading.abbreviated_name or ""


def _is_session_word(word: str) -> bool:
    return word.lower().strip(_EDGES) in NOT_NAME_WORDS


def _specific_readings(title: str) -> list[TitleReading]:
    """The readings that could say who the client is.

    Not one that is just a longer reading wrapped around a shorter one:
    "Session with Casey Morgan" reads as itself and as "Casey Morgan", and
    only the second names the client. Nor one made only of session wording:
    "Video call - Casey Morgan" has a piece that reads "Video call".
    """
    readings = [
        r for r in title_readings(title) if not all(_is_session_word(w) for w in _text(r).split())
    ]
    texts = [_text(r).lower() for r in readings]
    return [
        reading
        for reading, text in zip(readings, texts, strict=True)
        if not any(other != text and other in text for other in texts)
    ]


def suggested_name(title: str) -> SuggestedName | None:
    """What to fill in for a new client's name, as far as the title plainly says.

    The title has to name one person, in two words shaped like a name and
    neither of them session wording:

    - A full name ("Casey Morgan", "Session with Casey Morgan", "Morgan,
      Casey") fills in both.
    - A name with one part cut to its initial ("Jane S.", "J. Smith") fills in
      the whole part only; the cut one stays empty.
    - Initials ("K.M."), one word ("Jane" could be a first or a last name), or
      anything else gives nothing.
    """
    specific = _specific_readings(title)
    if len(specific) != 1:
        return None
    reading = specific[0]
    if reading.full_name is not None:
        words = reading.full_name.split()
        if len(words) != _FIRST_LAST or not all(_whole_name_word(w) for w in words):
            return None
        return SuggestedName(first_name=words[0], last_name=words[1])
    if reading.abbreviated_name is not None:
        first, last = reading.abbreviated_name.split()
        if _whole_name_word(first) and _CUT_WORD.fullmatch(last):
            return SuggestedName(first_name=first, last_name="")
        if _CUT_WORD.fullmatch(first) and _whole_name_word(last):
            return SuggestedName(first_name="", last_name=last)
    return None


def _whole_name_word(word: str) -> bool:
    """A word that could be a whole first or last name: not cut short, not session wording."""
    return (
        bool(_NAME_WORD.fullmatch(word))
        and not _CUT_WORD.fullmatch(word)
        and not _is_session_word(word)
    )


def _without_session_wording(text: str) -> str:
    kept = [w for w in text.split() if not _is_session_word(w)]
    return " ".join(kept).strip(_EDGES)


def name_part(title: str) -> str:
    """The name-like part of a title, without session wording; may be empty.

    "Session with K.M." gives "K.M."; "Therapy" gives "".
    """
    specific = _specific_readings(title)
    if len(specific) == 1:
        part = _without_session_wording(_text(specific[0]))
        if part:
            return part[:_NAME_MAX]
    return _without_session_wording(title)[:_NAME_MAX]


def chart_name(
    first_name: str | None, last_name: str | None, wording: str | None, *, unnamed: str
) -> tuple[str, str]:
    """The first and last name a new client's chart is made with.

    What the clinician typed, as typed (trimmed). With nothing typed, the
    wording's name part as the first name and no last name, which leaves the
    chart showing as needing a name; ``unnamed`` when not even that is left.
    """
    first = (first_name or "").strip()[:_NAME_MAX]
    last = (last_name or "").strip()[:_NAME_MAX]
    if first or last:
        return first, last
    return name_part(wording or "") or unnamed, ""


__all__ = ["SuggestedName", "chart_name", "name_part", "suggested_name"]
