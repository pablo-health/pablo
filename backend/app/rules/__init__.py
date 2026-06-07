# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rules engine core — pure applicability + enforcement over versioned data.

A small, dependency-free engine with three layers:

1. **Applicability** — :func:`applies` / :func:`evaluate_applicability`
   decide which rule items are in force for a given :class:`RuleContext`.
2. **Versioned data** — rulesets are effective-dated data files (see
   :func:`load_ruleset`); :func:`select_active_ruleset` picks the one in
   force as of a date.
3. **Enforcement** — :func:`evaluate_enforcement` resolves each applicable
   item's status (satisfied / missing / na) from evidence or a computed
   check and applies its flag behavior (hard_stop / soft_warn / info) to
   produce a finalization gate.

Ruleset contents are supplied per-deployment as data; this package ships
only the engine, the loader, and a generic illustrative example.
"""

from __future__ import annotations

from .enforcement import (
    EnforcementReport,
    EnforcementSpec,
    FlagBehavior,
    ItemEvaluation,
    ItemStatus,
    RequirementLevel,
    enforcement_spec,
    evaluate_enforcement,
    evaluate_item,
    evaluate_predicate,
)
from .engine import applies, evaluate_applicability, select_active_ruleset
from .loader import dump_ruleset, load_ruleset
from .models import AppliesWhen, RuleContext, RuleItem, Ruleset

__all__ = [
    "AppliesWhen",
    "EnforcementReport",
    "EnforcementSpec",
    "FlagBehavior",
    "ItemEvaluation",
    "ItemStatus",
    "RequirementLevel",
    "RuleContext",
    "RuleItem",
    "Ruleset",
    "applies",
    "dump_ruleset",
    "enforcement_spec",
    "evaluate_applicability",
    "evaluate_enforcement",
    "evaluate_item",
    "evaluate_predicate",
    "load_ruleset",
    "select_active_ruleset",
]
