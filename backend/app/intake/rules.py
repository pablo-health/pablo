# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Conditional visibility — "ask this only when they said that".

Clinical guidance is branch-shaped. A positive PHQ-2 is what earns the full
PHQ-9; a positive AUDIT-C is what earns the AUDIT; a non-zero answer to the
PHQ-9's ninth item is what earns a risk screen. A form that cannot branch
either asks everybody everything or asks nobody anything.

One condition per item, no nesting and no AND/OR. That is a deliberate floor
rather than a first cut at a rules engine: every guideline branch above is a
single comparison against a single earlier answer, and the shapes that need
two conditions can wait until somebody asks for one.

Three properties hold the whole design up.

* **A rule names an item by key, not by position.** Keys are assigned by
  whoever builds the form and never change, so reordering a version cannot
  silently re-point a rule at a different question.
* **A rule may only look backwards.** Referencing a later item would make
  visibility depend on an answer that has not been given, so publish refuses
  it. That is also what makes evaluation a single forward pass.
* **The value has to fit the question.** Comparing a date against an
  instrument's total, or naming a choice the question does not offer, is
  rejected when the version is published rather than silently evaluating
  false for every patient forever.

Nothing here evaluates a rule. This module says what a well-formed rule is;
answering "is this item visible for this patient" is the completion
function's job, and it is authoritative server-side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, TypeGuard

from pydantic import BaseModel, ConfigDict, model_validator

#: Operators that compare against the referenced item's own answer.
ANSWER_OPS = frozenset({"eq", "neq", "in", "gte", "lte", "answered"})

#: Operators that compare against a scored instrument rather than an answer.
#: ``score_*`` read the instrument's computed total; ``item_gte`` reads one
#: of its items, which is how "PHQ-9 item 9 above zero" is expressed.
INSTRUMENT_OPS = frozenset({"score_gte", "score_lte", "item_gte"})

#: Operators whose value must be a number.
_NUMERIC_OPS = frozenset({"gte", "lte"})

#: Item types that display text and collect nothing. A rule cannot point at
#: one, because there is no answer for the condition to be about. Named here
#: rather than imported from :mod:`app.intake.items` so that module can
#: import this one; :func:`app.intake.items.validate_item_list` is what keeps
#: the two in step, and a test pins them equal.
DISPLAY_ONLY_TARGETS = frozenset({"instructions", "section"})


class RuleError(ValueError):
    """A visibility rule that cannot be published as written."""


class VisibleWhen(BaseModel):
    """Show the item carrying this rule only when the condition holds.

    ``value`` is absent for ``answered`` and required for every other
    operator. It is typed as ``object`` because what a valid value looks like
    depends on the item the rule points at, which this model cannot see;
    :func:`check_rule` is where that is settled.
    """

    model_config = ConfigDict(extra="forbid")

    item_key: str
    op: Literal["eq", "neq", "in", "gte", "lte", "answered", "score_gte", "score_lte", "item_gte"]
    value: object = None

    @model_validator(mode="after")
    def _value_presence(self) -> VisibleWhen:
        if self.op == "answered":
            if self.value is not None:
                raise ValueError("'answered' compares nothing, so it takes no value")
        elif self.value is None:
            raise ValueError(f"'{self.op}' needs a value to compare against")
        return self


@dataclass(frozen=True)
class ReferencedItem:
    """What a rule's target looks like, flattened to what a rule can use.

    Built by :mod:`app.intake.items` so this module never has to import the
    config union it would otherwise need — and so a new item type describes
    itself here rather than growing a branch below.
    """

    key: str
    item_type: str
    #: The choices the question offers, for the item types that offer any.
    option_keys: frozenset[str] = frozenset()
    #: How many items the referenced instrument has, when it is one.
    instrument_item_count: int | None = None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuleError(message)


def _is_whole_number(value: object) -> TypeGuard[int]:
    """True for a real integer. ``bool`` is an ``int`` and is not one here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _check_instrument_op(rule: VisibleWhen, target: ReferencedItem) -> None:
    """Validate ``score_gte`` / ``score_lte`` / ``item_gte``."""
    _require(
        target.instrument_item_count is not None,
        f"'{rule.op}' compares a score, and '{target.key}' is not a measure",
    )
    if rule.op in {"score_gte", "score_lte"}:
        _require(_is_whole_number(rule.value), f"'{rule.op}' needs a whole-number score")
        return

    # item_gte: {"item": <1-based index>, "value": <score>}
    if not isinstance(rule.value, dict):
        raise RuleError("'item_gte' needs an item number and a score")
    index = rule.value.get("item")
    threshold = rule.value.get("value")
    if not _is_whole_number(index):
        raise RuleError("'item_gte' needs an item number")
    if not _is_whole_number(threshold):
        raise RuleError("'item_gte' needs a whole-number score")
    count = target.instrument_item_count or 0
    _require(
        1 <= index <= count,
        f"'{target.key}' has {count} items, so item {index} is not one of them",
    )


def _check_choice_op(rule: VisibleWhen, target: ReferencedItem) -> None:
    """Validate an equality or membership test against a choice question."""
    offered = target.option_keys
    if rule.op == "in":
        if not isinstance(rule.value, list) or not rule.value:
            raise RuleError("'in' needs a list of answers to match")
        wanted: list[object] = list(rule.value)
    else:
        wanted = [rule.value]
    unknown = [v for v in wanted if not isinstance(v, str) or v not in offered]
    if unknown:
        raise RuleError(f"'{target.key}' does not offer {unknown[0]!r} as an answer")


def check_rule(rule: VisibleWhen, target: ReferencedItem) -> None:
    """Refuse a rule whose value cannot fit the question it points at.

    The caller has already established that ``target`` is an earlier item in
    the same version; what is left is whether the comparison makes sense.
    Raises :class:`RuleError` naming the problem in the therapist's terms,
    never the schema's.
    """
    _require(
        target.item_type not in DISPLAY_ONLY_TARGETS,
        f"'{target.key}' is not a question, so nothing about it can be true or false",
    )

    if rule.op in INSTRUMENT_OPS:
        _check_instrument_op(rule, target)
    elif rule.op != "answered":
        _check_answer_op(rule, target)


def _check_answer_op(rule: VisibleWhen, target: ReferencedItem) -> None:
    """Validate a comparison against the referenced item's own answer."""
    if target.option_keys:
        _check_choice_op(rule, target)
        return

    if target.item_type == "yes_no":
        _require(
            rule.op in {"eq", "neq"} and isinstance(rule.value, bool),
            f"'{target.key}' is answered yes or no, so compare it to yes or no",
        )
        return

    if target.item_type in {"number", "scale"}:
        _require(
            rule.op in _NUMERIC_OPS | {"eq", "neq"}
            and isinstance(rule.value, int | float)
            and not isinstance(rule.value, bool),
            f"'{target.key}' is answered with a number, so compare it to a number",
        )
        return

    if target.item_type == "date":
        _require(rule.op in _NUMERIC_OPS | {"eq", "neq"}, f"'{rule.op}' does not fit a date")
        _require(_is_date(rule.value), f"'{target.key}' is a date, so compare it to a date")
        return

    if target.item_type in {"free_text", "reason"}:
        _require(
            rule.op in {"eq", "neq"} and isinstance(rule.value, str),
            f"'{target.key}' is answered in words, so compare it to words",
        )
        return

    raise RuleError(f"'{target.key}' has no answer to compare against")


def _is_date(value: object) -> bool:
    if isinstance(value, date):
        return True
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


__all__ = [
    "ANSWER_OPS",
    "DISPLAY_ONLY_TARGETS",
    "INSTRUMENT_OPS",
    "ReferencedItem",
    "RuleError",
    "VisibleWhen",
    "check_rule",
]
