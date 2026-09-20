# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which questions this patient is shown, given what they have answered.

:mod:`app.intake.rules` says what a well-formed rule is and refuses one that
cannot be published. This is the other half: the rule taking effect. One
function, :func:`evaluate`, answers "shown or not" for every question on a
version at once, and it is authoritative server-side — completion reads it,
the export will read it, and the browser's own port of it decides only what
to draw.

Four properties hold it up, and each is a decision rather than a detail.

**One forward pass, because a rule may only look backwards.** Publishing
refuses a rule that names a later question, so walking the items in order
means every rule's target has already been decided by the time the rule is
read. That is also what makes chaining work without a graph: A shows B and
B shows C is three iterations of the same loop.

**A hidden question has no answer.** When B is hidden, whatever the patient
typed into it before it was hidden is not offered to C's rule — so a stale
value cannot keep a third question on the screen after the answer that
earned it was taken back. The same fact is what submitting acts on: a value
saved while an item was visible and hidden by the time the form is handed
in is discarded rather than filed.

**An unanswered trigger hides, for every operator.** "Do they drink more
than the guideline says" cannot be true of somebody who has not said
whether they drink, and neither can "do they not". ``answered`` is the
operator whose whole business is that case, and it reads false there too,
which is the same statement said plainly.

**A measure is read only once it is finished.** ``score_gte``,
``score_lte`` and ``item_gte`` all compare against a scored instrument, and
a half-answered instrument has no score — the total of the items somebody
has reached so far is not a smaller version of the total, it is a different
number. So a rule on a measure holds nothing until every one of its items
is answered, which is the moment the measure means what the literature says
it means. A patient part-way through a PHQ-9 therefore does not watch a
risk screen appear and disappear as they work down it.

Nothing here reads the item's type, the instrument registry, or a database.
The shape of a stored answer is what says which field to compare — a choice
carries ``key``, a yes-or-no carries ``yes``, a measure carries
``item_scores`` — and publishing has already established that the rule's
value fits the question it points at. What the caller does have to supply,
for an instrument, is how many items it has: see :class:`VisibilityItem`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from .rules import INSTRUMENT_OPS, VisibleWhen

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The fields a stored answer puts its comparable value in, in the order
#: they are looked for. One per kind of question that a rule may point at:
#: a single choice, a yes-or-no, a number, a scale or a date, and an answer
#: written in words. A question answered with several choices carries
#: ``keys`` instead and is handled on its own, because membership rather
#: than equality is what a rule means about one.
_ANSWER_FIELDS: tuple[str, ...] = ("key", "yes", "value", "text")

#: The operators that ask which of two values came first, as opposed to
#: whether they are the same.
_ORDER_OPS = frozenset({"gte", "lte"})

#: The instrument operators that compare against the measure's total, as
#: opposed to ``item_gte``, which reads one of its items.
_SCORE_OPS = frozenset({"score_gte", "score_lte"})


@dataclass(frozen=True)
class VisibilityItem:
    """One question, flattened to what deciding visibility needs.

    ``rule`` is the condition this question is shown under, or ``None``
    when it is always asked.

    ``instrument_items`` is how many items the measure this question asks
    has, and is ``None`` for every question that is not one. It is passed
    in rather than looked up because this module knows nothing about the
    instrument registry and the browser's port has no copy of it — both
    callers already hold the number, the server from the registry and the
    portal from the form the server sent it.
    """

    key: str
    rule: VisibleWhen | None = None
    instrument_items: int | None = None


def evaluate(
    items: Sequence[VisibilityItem],
    answers: Mapping[str, object],
) -> dict[str, bool]:
    """Which of *items* this patient is shown, keyed by item key.

    *answers* is keyed by item key and holds whatever has been saved so
    far; a question with nothing saved against it is simply absent. Pure:
    same items and same answers, same answer, no clock and no database.

    Every key in *items* is present in the result, so a caller can look one
    up without deciding what a missing entry would have meant.
    """
    shown: dict[str, bool] = {}
    # Only the answers to questions this patient is actually shown. What
    # was typed into a question that has since been hidden is not offered
    # to anything downstream — see the module docstring.
    live: dict[str, object] = {}
    sizes = {item.key: item.instrument_items for item in items}

    for item in items:
        visible = _is_visible(item.rule, shown, live, sizes)
        shown[item.key] = visible
        if visible and item.key in answers:
            live[item.key] = answers[item.key]
    return shown


def _is_visible(
    rule: VisibleWhen | None,
    shown: Mapping[str, bool],
    live: Mapping[str, object],
    sizes: Mapping[str, int | None],
) -> bool:
    """Whether one question is shown, given everything decided before it."""
    if rule is None:
        return True
    # A rule naming nothing earlier on the form cannot be published, so
    # this is a version that drifted. Hiding is the safer reading of the
    # two: the practice said "ask this only when", and a condition nobody
    # can evaluate has not been met.
    if not shown.get(rule.item_key, False):
        return False
    return _condition_holds(rule, live.get(rule.item_key), sizes.get(rule.item_key))


def _condition_holds(rule: VisibleWhen, answer: object, instrument_items: int | None) -> bool:
    """Whether *answer* satisfies *rule*."""
    if not isinstance(answer, Mapping) or not answer:
        return False
    if rule.op == "answered":
        return True
    if rule.op in INSTRUMENT_OPS:
        return _measure_holds(rule, answer, instrument_items)
    return _answer_holds(rule, answer)


# ---------------------------------------------------------------------------
# Comparing against the answer itself
# ---------------------------------------------------------------------------


def _answer_holds(rule: VisibleWhen, answer: Mapping[str, object]) -> bool:
    """Compare a rule against what the patient chose, typed or picked."""
    chosen = answer.get("keys")
    if isinstance(chosen, list):
        return _picked_holds(rule, [key for key in chosen if isinstance(key, str)])

    given = next((answer[field] for field in _ANSWER_FIELDS if field in answer), None)
    if given is None:
        return False
    if rule.op in _ORDER_OPS:
        return _ordered_holds(rule.op, given, rule.value)
    return _matches(rule.op, given, rule.value)


def _matches(op: str, given: object, want: object) -> bool:
    """``eq``, ``neq`` and ``in`` — the operators that ask about equality."""
    if op == "in":
        return isinstance(want, list) and any(_same(given, candidate) for candidate in want)
    if op == "eq":
        return _same(given, want)
    return not _same(given, want)


def _ordered_holds(op: str, given: object, want: object) -> bool:
    """``gte`` and ``lte`` — the operators that ask which came first."""
    order = _compare(given, want)
    if order is None:
        return False
    return order >= 0 if op == "gte" else order <= 0


def _picked_holds(rule: VisibleWhen, chosen: list[str]) -> bool:
    """Compare a rule against a question that takes several answers.

    "Show this when they said alcohol" is what a practice means by a rule
    on a tick-list, so ``eq`` asks whether that answer is among the ones
    picked rather than whether it is the only one — the second reading
    would make a rule stop holding the moment somebody ticked a second box,
    which is not what anybody writing it had in mind. ``in`` is the same
    question asked of several answers at once, and ``neq`` is ``eq``
    negated. Nothing else can be published against one.
    """
    if rule.op == "eq":
        return rule.value in chosen
    if rule.op == "neq":
        return rule.value not in chosen
    if rule.op == "in":
        return isinstance(rule.value, list) and any(key in rule.value for key in chosen)
    return False


# ---------------------------------------------------------------------------
# Comparing against a scored measure
# ---------------------------------------------------------------------------


def _measure_holds(rule: VisibleWhen, answer: Mapping[str, object], size: int | None) -> bool:
    """Compare a rule against an instrument's score.

    Nothing holds until the measure is finished, whichever of the three
    operators is asking — including ``item_gte``, which reads one item and
    could technically answer sooner. It does not, deliberately: a risk
    screen that appears the moment somebody answers the ninth PHQ-9 item
    and vanishes again while they go back for the fourth is a worse thing
    to meet than one that appears once, when the measure is done.
    """
    answered = _measure_scores(answer, size)
    if answered is None:
        return False
    if rule.op not in _SCORE_OPS:
        return _one_item_holds(rule.value, answered)
    threshold = _whole(rule.value)
    if threshold is None:
        return False
    total = sum(answered)
    return total >= threshold if rule.op == "score_gte" else total <= threshold


def _measure_scores(answer: Mapping[str, object], size: int | None) -> list[int] | None:
    """Every item of the measure in order, or ``None`` until it is finished.

    ``None`` covers all three ways there is no score to compare against: no
    scores saved at all, an item still unanswered, and a caller who could
    not say how many items the measure has.
    """
    scores = answer.get("item_scores")
    if not isinstance(scores, Mapping) or size is None:
        return None
    answered: list[int] = []
    for position in range(1, size + 1):
        score = _whole(scores.get(str(position)))
        if score is None:
            return None
        answered.append(score)
    return answered


def _one_item_holds(value: object, answered: list[int]) -> bool:
    """``item_gte``, whose value is ``{"item": <1-based>, "value": <score>}``."""
    if not isinstance(value, Mapping):
        return False
    index, threshold = _whole(value.get("item")), _whole(value.get("value"))
    if index is None or threshold is None or not 1 <= index <= len(answered):
        return False
    return answered[index - 1] >= threshold


def _whole(value: object) -> int | None:
    """*value* as a whole number. ``True`` is not one, however Python feels."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# ---------------------------------------------------------------------------
# Equality and ordering that do not lie about types
# ---------------------------------------------------------------------------


def _same(given: object, want: object) -> bool:
    """Equality that will not read ``True`` as ``1`` or a date as a string.

    A yes-or-no is compared as one, and a date written down as
    ``2026-03-14`` equals the same day given as a date. Everything else —
    the key of a chosen answer, a line of the patient's own words — is
    compared as it stands.
    """
    if isinstance(given, bool) or isinstance(want, bool):
        return given is want
    order = _compare(given, want)
    if order is not None:
        return order == 0
    return bool(given == want)


def _compare(given: object, want: object) -> int | None:
    """``-1``/``0``/``1``, or ``None`` when the two cannot be ordered.

    Two numbers or two dates can be. A number against a date cannot, and
    neither can anything involving words or a yes-or-no — publishing
    refuses those, so reaching here means a version drifted and the honest
    answer is "this condition does not hold".
    """
    left_number, right_number = _as_number(given), _as_number(want)
    if left_number is not None and right_number is not None:
        return (left_number > right_number) - (left_number < right_number)
    left_date, right_date = _as_date(given), _as_date(want)
    if left_date is not None and right_date is not None:
        return (left_date > right_date) - (left_date < right_date)
    return None


def _as_number(value: object) -> float | None:
    """*value* as a number, or ``None``. A yes-or-no is not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _as_date(value: object) -> date | None:
    """*value* as a day, whether it arrived as one or as ``2026-03-14``."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["VisibilityItem", "evaluate"]
