# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Generic and brand names of the same drug, and drug names found in text.

The map (``data/names.json``) widens a name into every name a speaker may use
for the same drug, so a chart that lists "sertraline" also primes the
transcriber for "Zoloft", and tells the guard that a client saying "Zoloft"
names a drug the chart already lists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

DATA_DIR = Path(__file__).parent / "data"

_WORD = re.compile(r"[a-z0-9']+")


def name_key(text: str) -> str:
    """A name compared without case or punctuation: "St. John's Wort" is "st john's wort"."""
    return " ".join(_WORD.findall(text.lower()))


@dataclass(frozen=True)
class Drug:
    generic: str
    brands: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return (self.generic, *self.brands)


@cache
def _drugs() -> dict[str, Drug]:
    """Every name in the map, by key, to its drug."""
    data = json.loads((DATA_DIR / "names.json").read_text())
    by_key: dict[str, Drug] = {}
    for entry in data["drugs"]:
        drug = Drug(entry["generic"], tuple(entry["brands"]))
        for name in drug.names:
            by_key[name_key(name)] = drug
    return by_key


@cache
def _longest_name_words() -> int:
    return max(len(key.split()) for key in _drugs())


def drug_named(name: str) -> Drug | None:
    """The drug ``name`` names: by the whole name, or failing that by its leading words,
    so "lithium carbonate ER" and "bupropion XL" are lithium and bupropion."""
    words = name_key(name).split()
    for size in range(len(words), 0, -1):
        drug = _drugs().get(" ".join(words[:size]))
        if drug is not None:
            return drug
    return None


def names_for(name: str) -> tuple[str, ...]:
    """Every name for the drug ``name`` names, generic first; just ``name`` when the map
    does not know it."""
    drug = drug_named(name)
    return drug.names if drug is not None else (name.strip(),)


def same_drug(a: str, b: str) -> bool:
    if name_key(a) == name_key(b):
        return True
    drug_a, drug_b = drug_named(a), drug_named(b)
    return drug_a is not None and drug_a == drug_b


def names_in(text: str) -> Iterator[str]:
    """Each drug name ``text`` contains, as the map writes it, longest match first."""
    words = name_key(text).split()
    i = 0
    while i < len(words):
        for size in range(min(_longest_name_words(), len(words) - i), 0, -1):
            drug = _drugs().get(" ".join(words[i : i + size]))
            if drug is not None:
                phrase = " ".join(words[i : i + size])
                yield next(n for n in drug.names if name_key(n) == phrase)
                i += size
                break
        else:
            i += 1
