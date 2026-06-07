# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rules engine core — pure applicability evaluation over versioned data.

A small, dependency-free engine with two layers:

1. **Applicability** — :func:`applies` / :func:`evaluate_applicability`
   decide which rule items are in force for a given :class:`RuleContext`.
2. **Versioned data** — rulesets are effective-dated data files (see
   :func:`load_ruleset`); :func:`select_active_ruleset` picks the one in
   force as of a date.

Ruleset contents are supplied per-deployment as data; this package ships
only the engine, the loader, and a generic illustrative example.
"""

from __future__ import annotations

from .engine import applies, evaluate_applicability, select_active_ruleset
from .loader import dump_ruleset, load_ruleset
from .models import AppliesWhen, RuleContext, RuleItem, Ruleset

__all__ = [
    "AppliesWhen",
    "RuleContext",
    "RuleItem",
    "Ruleset",
    "applies",
    "dump_ruleset",
    "evaluate_applicability",
    "load_ruleset",
    "select_active_ruleset",
]
