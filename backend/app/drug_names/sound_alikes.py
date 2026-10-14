# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A drug name the transcript may have heard as a different drug, found without a model.

A transcriber primed with one name of a sound-alike pair writes that name
when the other is said: "lorazepam" comes back as "clonazepam". With no
priming it garbles an unfamiliar name instead, which a reader can see is
wrong. A wrong real name is the dangerous miss, because it reads right.

So when the transcript names a drug the chart does not list, and that name
sounds like one the chart does list, the transcript keeps the word as heard,
the draft's medication list carries "(heard as X; the chart lists Y)" for
the clinician to settle at sign, and nothing is proposed to the chart from
it. Two names sound alike when:

- they are a known pair (``data/sound_alike_pairs.json``): names a
  transcriber was measured writing for each other, every time; or
- their letters are within a third of the longer name of each other
  (edit distance, rounded down). The measured pairs differ by 3 letters of
  13 (nortriptyline, amitriptyline), 2 of 10 (lorazepam, clonazepam) and 3
  of 13 (aripiprazole, brexpiprazole): a long shared stem with a different
  start. The rule also pairs other look-alike names a prescriber already
  double-checks, such as clonidine and clozapine, lamotrigine and
  famotidine, Restoril and Zestril.

Generic and brand names count as one drug on both sides: "Ativan" heard
against a chart's "clonazepam" is lorazepam against clonazepam.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from .names import DATA_DIR, name_key, names_for, names_in, same_drug

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..notes.chart_context import ChartContext


@dataclass(frozen=True)
class SoundAlike:
    """A drug name heard in the visit that the chart does not list but sounds like one it does."""

    heard: str
    listed: str
    """The chart's name, as the chart writes it."""
    segment_ids: tuple[int, ...] = ()

    @property
    def mark(self) -> str:
        return f"(heard as {self.heard}; the chart lists {self.listed})"


@cache
def known_pairs() -> frozenset[frozenset[str]]:
    data = json.loads((DATA_DIR / "sound_alike_pairs.json").read_text())
    return frozenset(
        frozenset((name_key(p["said"]), name_key(p["written"]))) for p in data["pairs"]
    )


@cache
def known_pair_names() -> frozenset[str]:
    """Every name that is one side of a known pair."""
    return frozenset(name for pair in known_pairs() for name in pair)


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _letters(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalpha())


def sounds_like(a: str, b: str) -> bool:
    """Whether two different names are a known pair or within a third of each other."""
    if frozenset((name_key(a), name_key(b))) in known_pairs():
        return True
    la, lb = _letters(a), _letters(b)
    if not la or not lb or la == lb:
        return False
    return _edit_distance(la, lb) <= max(len(la), len(lb)) // 3


def chart_names(chart: ChartContext) -> list[str]:
    """The names the chart lists: its current medications and its allergy substances."""
    names = [m.name for m in chart.medications]
    names.extend(str(a.get("substance") or "") for a in chart.allergies)
    return [n for n in names if n.strip()]


def listed_sound_alike(name: str, listed: Iterable[str]) -> str | None:
    """The listed name ``name`` sounds like, or ``None`` when the chart lists ``name``
    itself (under any of its names) or nothing like it."""
    listed = list(listed)
    if any(same_drug(name, n) for n in listed):
        return None
    for chart_name in listed:
        if any(sounds_like(h, c) for h in names_for(name) for c in names_for(chart_name)):
            return chart_name
    return None


def find_sound_alikes(
    chart: ChartContext,
    segments: Mapping[int, str],
    named: Iterable[tuple[str, tuple[int, ...]]] = (),
) -> tuple[SoundAlike, ...]:
    """Every drug name heard in ``segments`` that sounds like one the chart lists.

    A drug name is one the name map knows in a line, or one ``named`` gives
    (a medication the extraction read, with the lines it cites), so a name
    the map does not know is still checked when it was read as a medication.
    """
    listed = chart_names(chart)
    if not listed:
        return ()
    heard: list[tuple[str, tuple[int, ...]]] = [
        (name, (n,)) for n, text in sorted(segments.items()) for name in names_in(text)
    ]
    heard.extend((name, ids) for name, ids in named if name.strip())
    found: dict[tuple[str, str], tuple[str, set[int]]] = {}
    for name, ids in heard:
        chart_name = listed_sound_alike(name, listed)
        if chart_name is None:
            continue
        key = (name_key(name), chart_name)
        found.setdefault(key, (name.strip(), set()))[1].update(ids)
    return tuple(
        SoundAlike(heard=written, listed=chart_name, segment_ids=tuple(sorted(ids)))
        for (_, chart_name), (written, ids) in found.items()
    )


def unconfirmed(drug_name: str, cited: Iterable[str], chart: ChartContext) -> SoundAlike | None:
    """Why a medication read from ``cited`` lines is not to be proposed, or ``None``.

    Either the drug is not listed and sounds like one that is, or it is a
    listed drug that the cited lines never name, naming a sound-alike of it
    instead (a reply that read "lorazepam" as the listed "clonazepam").
    """
    listed = chart_names(chart)
    chart_name = listed_sound_alike(drug_name, listed)
    if chart_name is not None:
        return SoundAlike(heard=drug_name.strip(), listed=chart_name)
    said = [name for text in cited for name in names_in(text)]
    if any(same_drug(drug_name, name) for name in said):
        return None
    for name in said:
        if (chart_name := listed_sound_alike(name, listed)) is not None and same_drug(
            chart_name, drug_name
        ):
            return SoundAlike(heard=name, listed=chart_name)
    return None
