# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pure evaluation logic for the rules engine.

This module implements layers 1+2 of the engine: *applicability*
(which items apply to a given context) and *versioned-data selection*
(which effective-dated ruleset is in force as of a date). It is a pure
function library — no I/O, no global state.

Enforcement (item status / flag behavior) is intentionally out of scope
here; the interfaces are shaped so it can be layered on later without
changing these signatures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date

    from .models import AppliesWhen, RuleContext, RuleItem, Ruleset


def applies(applies_when: AppliesWhen, context: RuleContext) -> bool:
    """Return whether ``applies_when`` is satisfied by ``context``.

    For each predicate dimension:

    - ``None`` predicate → matches anything (including an unset context
      value).
    - non-``None`` predicate → matches only if the context supplies a
      value for that dimension *and* that value is in the predicate tuple.

    All dimensions are combined with logical AND.
    """

    dimensions = ("provider_type", "state", "schedule", "drug_class")
    for dimension in dimensions:
        predicate: tuple[str, ...] | None = getattr(applies_when, dimension)
        if predicate is None:
            continue
        context_value: str | None = getattr(context, dimension)
        if context_value is None or context_value not in predicate:
            return False
    return True


def evaluate_applicability(ruleset: Ruleset, context: RuleContext) -> list[RuleItem]:
    """Return the items of ``ruleset`` that apply to ``context``.

    Order is preserved from the ruleset's ``items`` list.
    """

    return [item for item in ruleset.items if applies(item.applies_when, context)]


def select_active_ruleset(rulesets: list[Ruleset], as_of: date) -> Ruleset | None:
    """Return the ruleset in force as of ``as_of``.

    The active ruleset is the one with the latest ``effective_date`` that
    is on or before ``as_of``. Returns ``None`` if no ruleset has yet
    taken effect by that date. If two rulesets share the same
    ``effective_date`` the last one in the list wins.
    """

    active: Ruleset | None = None
    for ruleset in rulesets:
        if ruleset.effective_date > as_of:
            continue
        if active is None or ruleset.effective_date >= active.effective_date:
            active = ruleset
    return active
