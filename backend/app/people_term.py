# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the app says "clients" or "patients" to a clinician.

Therapists usually say clients; prescribers (PMHNPs, psychiatrists and other
medical clinicians) usually say patients. The word is chosen per clinician, in
this order:

1. the clinician's own choice in Settings;
2. what their license and clinician type suggest, when that settles it;
3. the practice's default, which the practice owner sets;
4. "clients".

The license comes before the practice default so that a group with a therapist
and a prescriber gets each their own word without anyone choosing. Only a
clinician whose license says nothing either way falls through to the practice.

URLs, API paths, table names and code identifiers say "patient" regardless.
This only decides the words a clinician reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

PeopleTerm = Literal["clients", "patients"]

DEFAULT_PEOPLE_TERM: PeopleTerm = "clients"

#: Licenses and degrees that prescribe. A board-certification suffix is
#: dropped before matching, so ``PMHNP-BC`` and ``PA-C`` count.
_PRESCRIBER_CREDENTIALS = frozenset({"PMHNP", "NP", "APRN", "FNP", "CNS", "CNP", "MD", "DO", "PA"})

#: Therapy licenses, and the doctorates a psychologist holds.
_THERAPIST_CREDENTIALS = frozenset(
    {
        "LCSW",
        "LICSW",
        "LISW",
        "LMSW",
        "LPC",
        "LPCC",
        "LCPC",
        "LMHC",
        "LCMHC",
        "LMFT",
        "MFT",
        "LP",
        "PSYD",
        "PHD",
        "LCADC",
        "LADC",
        "LAC",
    }
)

_PRESCRIBING_PROVIDER_TYPES = frozenset({"prescriber", "both"})


def _credential_tokens(credentials: Iterable[str]) -> set[str]:
    """Normalise credential titles: ``"Psy.D."`` → ``PSYD``, ``"PMHNP-BC"`` → ``PMHNP``."""
    tokens: set[str] = set()
    for raw in credentials:
        for part in re.split(r"[,/;]", raw):
            base = part.strip().upper().split("-")[0]
            token = re.sub(r"[^A-Z]", "", base)
            if token:
                tokens.add(token)
    return tokens


def suggest_people_term(
    *,
    provider_type: str | None,
    credential_titles: Iterable[str] | None = None,
    credentials: str | None = None,
    dea_number: str | None = None,
) -> PeopleTerm | None:
    """What a clinician's professional details suggest, or None if they don't settle it.

    Prescribing wins over therapy: a PMHNP who also holds an LMFT prescribes,
    and prescribers say patients.
    """
    titles = list(credential_titles or [])
    if credentials:
        titles.append(credentials)
    tokens = _credential_tokens(titles)

    prescribes = provider_type in _PRESCRIBING_PROVIDER_TYPES or dea_number
    if prescribes or tokens & _PRESCRIBER_CREDENTIALS:
        return "patients"
    if provider_type == "therapist" or tokens & _THERAPIST_CREDENTIALS:
        return "clients"
    return None


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
    suggested: PeopleTerm | None,
    practice_default: PeopleTerm | None,
) -> PeopleTerm:
    """Apply the order in the module docstring."""
    return choice or suggested or practice_default or DEFAULT_PEOPLE_TERM


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
