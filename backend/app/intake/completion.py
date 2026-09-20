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

**Visibility is a seam, and today it is a stub.** A question may eventually
be shown only when an earlier answer calls for it, which is how clinical
guidance is shaped: a positive screener earns the longer instrument, a
non-zero answer to the ninth PHQ-9 item earns a risk screen. A rule already
has a stored shape and is already validated at publish
(:mod:`app.intake.rules`), but nothing evaluates one yet, so
:func:`every_item_visible` answers "shown" for every question and this
module never consults a rule. Completion therefore asks for every required
question on the form, which is the behaviour a form with no rules on it has
anyway.

The seam is a parameter rather than a later refactor because of what
changes when evaluation lands: a hidden question must be neither required
nor missing, and that is a statement about *this* function. Passing the
decision in means the rule engine arrives as one new argument and one set
of fixtures, with the walk below untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .answers import is_answered
from .items import DISPLAY_ONLY_ITEM_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from .items import ItemConfig
    from .rules import VisibleWhen

#: Decides whether one question is shown, given the rule it carries and
#: every answer given so far, keyed by item key. Pure: same inputs, same
#: answer, no clock and no database.
VisibilityRule = Callable[["VisibleWhen | None", "Mapping[str, object]"], bool]


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
    """

    complete: bool
    missing: list[str]


def every_item_visible(
    rule: VisibleWhen | None,  # noqa: ARG001 — the seam's shape, not yet read
    answers: Mapping[str, object],  # noqa: ARG001 — the seam's shape, not yet read
) -> bool:
    """Show every question, whatever rule it carries.

    The v1 implementation of the visibility seam. Rules are stored and
    validated but not evaluated yet, so a form behaves exactly as one with
    no rules on it does: every question is asked of everybody.
    """
    return True


def assess(
    items: Sequence[CompletionItem],
    answers: Mapping[str, object],
    *,
    visibility: VisibilityRule = every_item_visible,
    today: date | None = None,
) -> Completion:
    """Which of *items* are still unanswered, given *answers*.

    ``answers`` is keyed by item key rather than by id, because that is what
    a rule points at and what the shared front-end port will be handed.

    A question counts as missing when it is required, shown, and has no
    answer that its own validator accepts. Three kinds of question are
    therefore never missing: the headings and paragraphs that collect
    nothing, the ones the practice marked optional, and — once rules are
    evaluated — the ones this patient was never shown.
    """
    missing = [
        item.item_id
        for item in items
        if item.required and not _is_settled(item, answers, visibility, today)
    ]
    return Completion(complete=not missing, missing=missing)


def _is_settled(
    item: CompletionItem,
    answers: Mapping[str, object],
    visibility: VisibilityRule,
    today: date | None,
) -> bool:
    """True when this question is not holding the form up."""
    if item.config is None:
        return False
    if item.config.item_type in DISPLAY_ONLY_ITEM_TYPES:
        return True
    if not visibility(item.config.visible_when, answers):
        return True
    return is_answered(item.config, answers.get(item.key), today=today)


__all__ = [
    "Completion",
    "CompletionItem",
    "VisibilityRule",
    "assess",
    "every_item_visible",
]
