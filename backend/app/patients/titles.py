# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a calendar event's title could say about who the client is.

A service that puts sessions on a clinician's calendar names the client its
own way: a full name, initials, "Last, First", a name cut short, or a name
inside other words ("Session with Jane Smith", "Jane Smith - Therapy"). The
matcher reads a name, initials and a shortened name from separate fields, so
a title has to be read into those first. This module only reads; deciding
what the readings mean, and whether they agree, is the caller's.

Every reading is a guess. A title is offered to the matcher in each way it
could be read, and a caller suggests a client only when the readings agree
(see ``outside_sessions``). Nothing here is ever enough to book on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Where a title is split into pieces that might each be the name.
_SEPARATORS = re.compile("\\s+[-\u2013\u2014]\\s+|\\s*[:|/]\\s*")  # hyphen, en and em dash
#: "Session with Jane Smith": the name follows "with".
_WITH = re.compile(r"\bwith\s+(.+)$", re.IGNORECASE)
#: Two letters, each with an optional dot, and nothing else: "J.S.", "JS", "J. S."
_INITIALS = re.compile(r"([A-Za-z])\.?\s*([A-Za-z])\.?")
#: A name word cut to its first letter: "J." or "J".
_CUT_WORD = re.compile(r"[A-Za-z]\.?")
#: Two words with one cut short: "J. Smith", "Jane S."
_SHORT_NAME_WORDS = 2


@dataclass(frozen=True)
class TitleReading:
    """One way a title could name its client. Exactly one field is set."""

    full_name: str | None = None
    initials: str | None = None
    abbreviated_name: str | None = None
    """A first and last name with one of them cut to its initial."""


def title_readings(title: str) -> list[TitleReading]:
    """Every way this title could name a client, the whole title first.

    Empty only when no piece has the shape of a name: a single word, say. A
    title like "Therapy Session" is still read as a name; the matcher finds
    no one by it.
    """
    readings: list[TitleReading] = []
    for piece in _pieces(title):
        reading = _read(piece)
        if reading is not None and reading not in readings:
            readings.append(reading)
    return readings


def _pieces(title: str) -> list[str]:
    """The whole title, each piece between separators, and what follows "with"."""
    whole = " ".join(title.split())
    pieces = [whole]
    for piece in _SEPARATORS.split(whole):
        pieces.append(piece)
        after_with = _WITH.search(piece)
        if after_with:
            pieces.append(after_with.group(1))
    return [stripped for p in pieces if (stripped := p.strip(" ,;"))]


def _read(piece: str) -> TitleReading | None:
    if piece.count(",") == 1:
        last, first = (part.strip() for part in piece.split(","))
        # "Smith, Jane" reads as "Jane Smith"; a lone comma names no one.
        piece = f"{first} {last}" if first and last else ""
    words = piece.split()
    cut = [bool(_CUT_WORD.fullmatch(w)) for w in words]
    if _INITIALS.fullmatch(piece) or (len(words) == _SHORT_NAME_WORDS and all(cut)):
        return TitleReading(initials=piece)
    if len(words) == _SHORT_NAME_WORDS and any(cut):
        return TitleReading(abbreviated_name=piece)
    if len(words) >= _SHORT_NAME_WORDS:
        return TitleReading(full_name=piece)
    return None


__all__ = ["TitleReading", "title_readings"]
