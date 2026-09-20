# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Is this form finished, and if not, what is still missing.

One function, one answer, computed from the questions and the answers every
time it is asked. Nothing stores "complete": a stored flag is a second copy
of a fact the rows already carry, and the two disagree the first time an
answer is corrected or a version is re-read.

**No client computes this.** The portal renders what the server says is
missing rather than deciding for itself, so a form can never be submitted
because the browser thought it was done. That matters here more than it
usually does — a question the patient never saw and a question they skipped
look the same from the front end, and only the server knows which is which.

**A question the patient was never shown is neither required nor missing.**
A rule may say a question is asked only when an earlier answer calls for
it, which is how clinical guidance is shaped: a positive screener earns the
longer instrument, a non-zero answer to the ninth PHQ-9 item earns a risk
screen. :mod:`app.intake.rules` says what a well-formed rule is,
:mod:`app.intake.visibility` evaluates one, and this module is where the
answer has consequences — a hidden question drops out of ``missing``
entirely rather than being counted and forgiven.

Which questions were hidden is reported alongside, because the caller has
one thing left to do with it: a value saved while a question was visible
and hidden by the time the form is handed in is not part of the
submission.

Evaluation stays a parameter rather than a hard-coded call so a test can
pin the walk against a visibility answer of its own choosing, and so the
export can one day ask the same question through the same door.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .answers import is_answered
from .items import DISPLAY_ONLY_ITEM_TYPES, instrument_item_count
from .visibility import VisibilityItem, evaluate

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from .items import ItemConfig

#: Decides which questions are shown, given each one's rule and every
#: answer given so far, keyed by item key. Pure: same inputs, same answer,
#: no clock and no database. :func:`app.intake.visibility.evaluate` is the
#: one implementation that reads rules.
VisibilityRule = Callable[
    ["Sequence[VisibilityItem]", "Mapping[str, object]"], "Mapping[str, bool]"
]


@dataclass(frozen=True)
class CompletionItem:
    """One question, flattened to what deciding completion needs.

    Built by the caller from stored rows, so this module never has to know
    how an item is stored or which repository read it.

    ``config`` is ``None`` for a stored item whose settings no longer parse.
    That is rare and it is not nothing: the question cannot be answered as
    it stands, so a required one holds the form up until somebody fixes it.
    Reporting it as answerable would let a patient hand in a form with a
    question nobody could have answered.
    """

    item_id: str
    key: str
    required: bool
    config: ItemConfig | None


@dataclass(frozen=True)
class Completion:
    """Whether the form can be submitted, and what is holding it up.

    ``missing`` holds item ids in the order the patient reads them, so the
    portal can send somebody to the first thing it names without sorting
    anything.

    ``hidden`` holds the ids of the questions this patient is not shown, in
    the same order. Not a client's business — the portal is told what is
    outstanding, not what it is being spared — but submitting reads it, so
    that an answer given before a rule stopped holding is not filed as part
    of a form that never asked the question.
    """

    complete: bool
    missing: list[str]
    hidden: list[str] = field(default_factory=list)


def assess(
    items: Sequence[CompletionItem],
    answers: Mapping[str, object],
    *,
    visibility: VisibilityRule = evaluate,
    today: date | None = None,
) -> Completion:
    """Which of *items* are still unanswered, given *answers*.

    ``answers`` is keyed by item key rather than by id, because that is what
    a rule points at and what the shared front-end port is handed.

    A question counts as missing when it is required, shown, and has no
    answer that its own validator accepts. Three kinds of question are
    therefore never missing: the headings and paragraphs that collect
    nothing, the ones the practice marked optional, and the ones this
    patient was never shown.
    """
    shown = visibility([_as_visibility_item(item) for item in items], answers)
    missing: list[str] = []
    hidden: list[str] = []

    for item in items:
        if not shown.get(item.key, True):
            hidden.append(item.item_id)
            continue
        if item.required and not _is_settled(item, answers, today):
            missing.append(item.item_id)

    return Completion(complete=not missing, missing=missing, hidden=hidden)


def _as_visibility_item(item: CompletionItem) -> VisibilityItem:
    """One question as the rule evaluator needs to see it.

    An item whose stored settings no longer parse carries no rule here, so
    it is shown and counted — which is what makes it hold the form up
    rather than quietly disappear from it.
    """
    return VisibilityItem(
        key=item.key,
        rule=item.config.visible_when if item.config is not None else None,
        instrument_items=instrument_item_count(item.config),
    )


def _is_settled(
    item: CompletionItem,
    answers: Mapping[str, object],
    today: date | None,
) -> bool:
    """True when this question is not holding the form up."""
    if item.config is None:
        return False
    if item.config.item_type in DISPLAY_ONLY_ITEM_TYPES:
        return True
    return is_answered(item.config, answers.get(item.key), today=today)


__all__ = [
    "Completion",
    "CompletionItem",
    "VisibilityRule",
    "assess",
]
