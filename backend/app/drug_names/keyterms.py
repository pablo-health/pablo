# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The transcriber's vocabulary for one session, built from the client's chart.

Every name the chart gives for a drug the client takes, took, or is allergic
to, widened to its generic and brand names, so an unfamiliar name is written
the way the chart writes it rather than garbled. Nothing else: no fixed
formulary list, because a list holding one name of a sound-alike pair makes
the transcriber write that name when the other is said.

One exception, for the same reason: a name that is one side of a known
sound-alike pair (``data/sound_alike_pairs.json``) is never sent. Listed, it
pulls its partner onto itself every time, and the chart's own drug is then
indistinguishable from the partner; unlisted, both names were heard right
every time. The guard in :mod:`.sound_alikes` then sees the partner as said.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .names import name_key, names_for
from .sound_alikes import known_pair_names

if TYPE_CHECKING:
    from collections.abc import Iterable

#: The provider's cap on the vocabulary, in words, for the speech model the product uses.
KEYTERM_WORD_LIMIT = 200


def chart_keyterms(names: Iterable[str]) -> list[str]:
    """``names`` (the chart's medication and allergy names, most relevant first) widened
    to every name for each drug, once each, within the word cap. Empty when the chart
    names nothing."""
    terms: list[str] = []
    seen: set[str] = set()
    words = 0
    for chart_name in names:
        for term in names_for(chart_name):
            key = name_key(term)
            if not key or key in seen or key in known_pair_names():
                continue
            size = len(key.split())
            if words + size > KEYTERM_WORD_LIMIT:
                return terms
            seen.add(key)
            terms.append(term.strip())
            words += size
    return terms
