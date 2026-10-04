# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a calendar event's title could say about who the client is.

A service that puts sessions on a clinician's calendar names the client its
own way: a full name, initials, "Last, First", a name cut short, or a name
inside other words ("Session with Jane Smith", "Jane Smith - Therapy"). The
matcher reads a name, initials and a shortened name from separate fields, so
a title has to be read into those first. This module only reads; deciding
what the readings mean, and whether they agree, is the caller's.

Every reading is a guess, and a title is offered to the matcher in each way
it could be read. A reading also says whether it is the session's name rather
than a name somewhere in the title (``names_the_session``): the whole title, a
whole piece of it between separators, or the name after a session word and
"with" ("Session with Jane Smith"). "Lunch with Jane Smith" mentions Jane
Smith without being her session. Only such a reading can book a session, and
only when the caller's other rules agree (see ``outside_sessions``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

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

#: What may come before "with" in a session's own title. Anything else
#: ("Lunch with", "Call with") mentions a client without being their session.
SESSION_WORDS: frozenset[str] = frozenset(
    {
        "session",
        "therapy",
        "therapy session",
        "appointment",
        "telehealth",
        "telehealth session",
        "intake",
        "consult",
        "consultation",
        "follow-up",
        "follow up",
        "med management",
        "medication management",
    }
)

#: Words that are never part of a person's name, one word at a time and
#: lowercased: every word of ``SESSION_WORDS`` and more besides. Used to keep
#: session wording out of a new client's name, never to decide what books —
#: "Call with Jane Smith" still asks, though "Call" is no name.
NOT_NAME_WORDS: frozenset[str] = frozenset(
    {word for phrase in SESSION_WORDS for word in phrase.split()}
    | {
        "appt",
        "call",
        "check-in",
        "couples",
        "eval",
        "evaluation",
        "family",
        "followup",
        "group",
        "initial",
        "meds",
        "meeting",
        "video",
        "virtual",
        "visit",
        "with",
        "zoom",
    }
)


@dataclass(frozen=True)
class TitleReading:
    """One way a title could name its client. Exactly one name field is set."""

    full_name: str | None = None
    initials: str | None = None
    abbreviated_name: str | None = None
    """A first and last name with one of them cut to its initial."""
    names_the_session: bool = field(default=False, compare=False)
    """The whole title, a whole piece of it, or the name after a session word
    and "with": the session's name, not a name the title mentions."""


def title_readings(title: str) -> list[TitleReading]:
    """Every way this title could name a client, the whole title first.

    Empty only when no piece has the shape of a name: a single word, say. A
    title like "Therapy Session" is still read as a name; the matcher finds
    no one by it. A reading found more than one way names the session if any
    of them does.
    """
    readings: list[TitleReading] = []
    for piece, names_the_session in _pieces(title):
        reading = _read(piece)
        if reading is None:
            continue
        reading = replace(reading, names_the_session=names_the_session)
        if reading in readings:
            at = readings.index(reading)
            if names_the_session and not readings[at].names_the_session:
                readings[at] = reading
            continue
        readings.append(reading)
    return readings


def is_session_word(words: str) -> bool:
    """Whether this is a session's own word ("Session", "Med management")."""
    return " ".join(words.split()).strip(" ,;.").lower() in SESSION_WORDS


def _pieces(title: str) -> list[tuple[str, bool]]:
    """The whole title, each piece between separators, and what follows "with".

    Each with whether it is the session's name: everything but a name after
    "with" is, and that one only after a session word.
    """
    whole = " ".join(title.split())
    pieces = [(whole, True)]
    for piece in _SEPARATORS.split(whole):
        pieces.append((piece, True))
        after_with = _WITH.search(piece)
        if after_with:
            pieces.append((after_with.group(1), is_session_word(piece[: after_with.start()])))
    return [(stripped, names) for p, names in pieces if (stripped := p.strip(" ,;"))]


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


__all__ = ["NOT_NAME_WORDS", "SESSION_WORDS", "TitleReading", "is_session_word", "title_readings"]
