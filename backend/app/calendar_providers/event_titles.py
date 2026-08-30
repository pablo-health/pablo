# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How a session reads on the therapist's own calendar.

Pushing every session out as "Therapy Session" is safe and unreadable: a
therapist looking at their week sees a column of identical blocks. Putting
full names there is readable and is a disclosure — to whoever else can see
that calendar, and to whoever hosts it.

So there are three rungs rather than a checkbox, and the middle one is the
point: initials are enough to tell Thursday's client from Friday's, and
not enough for someone reading over a shoulder on a train.

Nothing here decides whether a disclosure is permitted. That is the
therapist's own agreement with their calendar provider, and the top rung
asks them to say so explicitly.
"""

from __future__ import annotations

import hashlib
import unicodedata
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..models.patient import Patient

DEFAULT_EVENT_SUMMARY = "Therapy Session"
"""What a session reads as at the floor, and whenever anything above it
cannot be worked out."""

_MAX_LAST_NAME_LETTERS = 4
"""How far a last name is extended to break a tie before falling back to a
suffix. "J.M." becomes "J.Mi." becomes "J.Mil." — past that, two people are
similar enough that more letters stop helping."""

_SUFFIX_LENGTH = 2


class EventTitleStyle(Enum):
    """How much of a patient's identity a pushed event carries."""

    GENERIC = "generic"
    """"Therapy Session". Nothing identifying leaves Pablo."""

    INITIALS = "initials"
    """"J.M." — enough to recognise, not enough to identify."""

    FULL = "full"
    """The patient's name, and only under the therapist's own agreement
    covering the calendar it lands on."""


CURRENT_ATTESTATION_VERSION = "v1"
"""Which wording a new attestation is recorded against.

Versioned rather than read live from the interface: an audit row is
evidence of what someone agreed to at the time, and a later copy change
must not rewrite what past attestations appear to have said. Add a new
version, never edit an existing one.
"""

ATTESTATION_STATEMENTS: dict[str, str] = {
    "v1": (
        "I confirm this Google account is covered by a business associate "
        "agreement (BAA) my practice holds. Pablo's BAA does not cover this "
        "Google account, and a personal Gmail address never qualifies."
    ),
}
"""What each version of the attestation says, in full.

Recorded into the audit row alongside its version so the row stands on
its own — a reader six months from now should not have to find this table
to know what was agreed to, and should still be able to if the table
moves.
"""


def parse_style(value: str | None) -> EventTitleStyle:
    """Read a stored style, falling back to the floor rather than raising.

    An unreadable preference must not stop a session being pushed, and it
    must not guess upward: anything unrecognised reads as generic.
    """
    try:
        return EventTitleStyle(value)
    except ValueError:
        return EventTitleStyle.GENERIC


def _fold(value: str) -> str:
    """Strip accents so an initial is a letter a calendar can render.

    Losing the accent is a cosmetic loss on a two-letter label; leaving a
    combining mark to be mangled somewhere downstream is worse.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _letters(value: str) -> str:
    return "".join(c for c in _fold(value).strip() if c.isalpha())


def _initials(first: str, last: str, *, last_letters: int = 1) -> str:
    """ "J.M." from a first and last name, or as much of it as there is."""
    first_part = _letters(first)[:1].upper()
    last_part = _letters(last)[:last_letters]
    if last_part:
        last_part = last_part[0].upper() + last_part[1:].lower()
    parts = [p for p in (first_part, last_part) if p]
    if not parts:
        return ""
    return ".".join(parts) + "."


def _disambiguating_suffix(patient_id: str) -> str:
    """A short, stable tag for two people a name cannot separate.

    Derived from the patient id so it never moves, and so it says nothing
    about the person it belongs to.
    """
    return hashlib.sha256(patient_id.encode()).hexdigest()[:_SUFFIX_LENGTH]


def initials_by_patient(patients: Iterable[Patient]) -> dict[str, str]:
    """Initials for a caseload, with ties broken rather than left to collide.

    Two clients who both come out "J.M." are a real and ordinary thing, and
    showing the therapist the same label twice would make the calendar less
    useful than the generic one it replaced. So a tie extends the last name
    a letter at a time, and if the names are genuinely the same, a stable
    per-person suffix separates them.
    """
    roster = list(patients)
    resolved: dict[str, str] = {}

    by_label: dict[str, list[Patient]] = {}
    for patient in roster:
        by_label.setdefault(_initials(patient.first_name, patient.last_name), []).append(patient)

    for label, group in by_label.items():
        if not label:
            continue
        if len(group) == 1:
            resolved[group[0].id] = label
            continue

        # Try longer last-name prefixes until they separate the group.
        separated = False
        for length in range(2, _MAX_LAST_NAME_LETTERS + 1):
            longer = {
                p.id: _initials(p.first_name, p.last_name, last_letters=length) for p in group
            }
            if len(set(longer.values())) == len(group):
                resolved.update(longer)
                separated = True
                break
        if separated:
            continue

        # Same name, or close enough that letters stopped helping.
        for patient in group:
            resolved[patient.id] = f"{label} {_disambiguating_suffix(patient.id)}"

    return resolved


def summary_for(
    style: EventTitleStyle,
    patient: Patient | None,
    *,
    initials: Mapping[str, str] | None = None,
) -> str:
    """What one session reads as, for a style and the patient it is with.

    Falls back to the generic summary whenever the name it would need is
    missing — an event with no title is not an option, and guessing upward
    from a half-known name is not either.
    """
    if style is EventTitleStyle.GENERIC or patient is None:
        return DEFAULT_EVENT_SUMMARY

    if style is EventTitleStyle.FULL:
        full = " ".join(part for part in (patient.first_name, patient.last_name) if part).strip()
        return full or DEFAULT_EVENT_SUMMARY

    label = (initials or {}).get(patient.id) or _initials(patient.first_name, patient.last_name)
    return label or DEFAULT_EVENT_SUMMARY
