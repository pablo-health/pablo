# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain models for the rules engine core.

The engine is pure data + logic: it has no database, framework, or
network dependencies. A *ruleset* is a versioned, effective-dated
collection of *rule items*. Each item declares the conditions under
which it applies via an ``AppliesWhen`` predicate evaluated against a
``RuleContext``.

The same shapes are intended to carry different kinds of rulesets
(e.g. credentialing cadences, prescribing checklists) — anything
item-specific lives in ``RuleItem.metadata`` as free-form data, so the
evaluator stays ruleset-agnostic. Concrete ruleset contents are supplied
as data at deployment time; nothing jurisdiction-specific is shipped in
this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True)
class RuleContext:
    """The facts an item's applicability is evaluated against.

    Every dimension is optional. ``None`` means "not known / not set" for
    that dimension, which is distinct from a value: an item gated on a
    dimension does not apply when that dimension is unset in the context.

    Fields are intentionally minimal for layers 1+2 (applicability over
    versioned data). ``schedule`` and ``drug_class`` are present so the
    context is forward-compatible with later, encounter-aware layers
    without changing this shape.
    """

    provider_type: str | None = None
    state: str | None = None
    schedule: str | None = None
    drug_class: str | None = None


@dataclass(frozen=True)
class AppliesWhen:
    """A predicate over context dimensions.

    Each dimension is a tuple of accepted values, or ``None`` meaning
    "any value (including unset) matches this dimension". A non-``None``
    dimension matches only when the context supplies a value for that
    dimension that is contained in the tuple.

    All supplied (non-``None``) dimensions must match for the predicate to
    apply (logical AND across dimensions).
    """

    provider_type: tuple[str, ...] | None = None
    state: tuple[str, ...] | None = None
    schedule: tuple[str, ...] | None = None
    drug_class: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RuleItem:
    """A single rule within a ruleset.

    ``applies_when`` decides whether the item is in force for a given
    context. ``authority_ref`` is an optional free-form citation supplied
    by the deployment's data (e.g. a statute or regulation reference).
    ``metadata`` carries free-form, item-specific data — cadence,
    reminder windows, labels, and the like — so credentialing and
    prescribing rulesets fit the same shape.

    A future ``flag_behavior`` (enforcement) field can be added without
    reworking callers; until then enforcement-related data, if any, lives
    in ``metadata``.
    """

    id: str
    applies_when: AppliesWhen
    authority_ref: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Ruleset:
    """A versioned, effective-dated collection of rule items.

    ``version`` is an opaque, deployment-supplied label; record it on
    anything stamped by this ruleset so the rules in force at the time can
    be reconstructed later. ``effective_date`` is the date from which the
    ruleset is considered active; selection logic picks the latest
    ruleset whose ``effective_date`` is on or before an "as of" date.
    """

    id: str
    version: str
    effective_date: date
    items: list[RuleItem] = field(default_factory=list)
