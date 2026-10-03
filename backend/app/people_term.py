# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the app says "clients" or "patients" to a clinician.

Therapists usually say clients; prescribers usually say patients. The word is
chosen per clinician, in this order:

1. the clinician's own choice in Settings;
2. the practice's default, which the practice owner sets;
3. "clients".

URLs, API paths, table names and code identifiers say "patient" regardless.
This only decides the words a clinician reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PeopleTerm = Literal["clients", "patients"]

DEFAULT_PEOPLE_TERM: PeopleTerm = "clients"


def as_people_term(value: object) -> PeopleTerm | None:
    """``value`` if it is one of the two words, else None."""
    if value == "clients":
        return "clients"
    if value == "patients":
        return "patients"
    return None


def resolve_people_term(
    *,
    choice: PeopleTerm | None,
    practice_default: PeopleTerm | None,
) -> PeopleTerm:
    """Apply the order in the module docstring."""
    return choice or practice_default or DEFAULT_PEOPLE_TERM


@dataclass(frozen=True)
class PeopleWords:
    """The word in each form a sentence needs. ``one``/``many`` are lower case."""

    one: str
    many: str

    @property
    def One(self) -> str:  # noqa: N802 — mirrors the frontend helper's shape
        return self.one.capitalize()

    @property
    def Many(self) -> str:  # noqa: N802 — mirrors the frontend helper's shape
        return self.many.capitalize()


def people_words(term: PeopleTerm) -> PeopleWords:
    """``people_words("patients").one == "patient"``."""
    return PeopleWords(one=term[:-1], many=term)
