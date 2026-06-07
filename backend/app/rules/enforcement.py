# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Layer 3 of the rules engine — enforcement.

Layers 1+2 (:mod:`.engine`) decide which items *apply* to a context. This
layer decides, for each applicable item, whether it is *satisfied* and what
to do when it is not — the finalization gate a prescribing workflow checks
before letting a script go out.

The pieces:

* **Applicability resolves first** (layer 1). An opioid-only item never
  enforces against a stimulant prescription.
* **Status** — an applicable item is ``satisfied`` only when its evidence
  resolves (an ``evidence_link`` to a real record, or a signed clinician
  statement) *or* its computed ``satisfied_when`` check holds. A bare item
  with neither is ``missing`` — a checkbox with no record is worse than an
  empty field. A *conditional* item whose ``trigger`` has not fired is
  ``na`` (not applicable to this encounter).
* **Flag behavior** — ``hard_stop`` blocks finalization, ``soft_warn``
  allows a logged one-line override, ``info`` is advisory.

This module is pure: no database, no ORM, no I/O. The enforcement DATA
(``flag_behavior``, ``requirement_level``, ``trigger``, ``satisfied_when``,
``evidence``) rides in :attr:`RuleItem.metadata`, so a ruleset that carries
none of it (e.g. a credentialing ruleset) evaluates as a list of advisory
items rather than erroring.

Two inputs the evaluator needs beyond the :class:`RuleContext` used for
applicability:

* ``facts`` — a flat mapping of dotted field path -> value that the
  ``trigger`` / ``satisfied_when`` predicates read (e.g.
  ``{"prescription.days_supply": 5, "prescription.refills": 0}``). The
  caller assembles it from the encounter + prescription; the engine stays
  ORM-free. An absent key reads as ``None``.
* ``evidence`` — the set of item ids whose evidence has resolved (or a
  mapping of item id -> evidence link). Presence means the evidence-backed
  item is satisfied.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .engine import evaluate_applicability

if TYPE_CHECKING:
    from .models import RuleContext, RuleItem, Ruleset


class ItemStatus(StrEnum):
    """Whether an applicable item's requirement is met."""

    SATISFIED = "satisfied"
    MISSING = "missing"
    NA = "na"


class FlagBehavior(StrEnum):
    """What a ``missing`` item does to finalization."""

    HARD_STOP = "hard_stop"
    SOFT_WARN = "soft_warn"
    INFO = "info"


class RequirementLevel(StrEnum):
    """The item's requirement class (R / C / + in the attestation schema)."""

    REQUIRED = "required"
    CONDITIONAL = "conditional"
    RECOMMENDED = "recommended"


# Predicate comparison operators for the trigger / satisfied_when DSL.
# A predicate is ``{"field": path, "op": name, "value": v}`` or a boolean
# combinator ``{"all": [...]}`` / ``{"any": [...]}``.
def _op_eq(a: Any, b: Any) -> bool:
    return bool(a == b)


def _op_ne(a: Any, b: Any) -> bool:
    return bool(a != b)


def _op_in(a: Any, b: Any) -> bool:
    return isinstance(b, Iterable) and not isinstance(b, str | bytes) and a in b


def _numeric(a: Any, b: Any) -> tuple[float, float] | None:
    """Return ``(a, b)`` as floats, or ``None`` if either isn't comparable.

    ``bool`` is excluded deliberately — comparing a flag with ``>`` is a data
    error, not a quiet ``True``.
    """

    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if isinstance(a, int | float) and isinstance(b, int | float):
        return (float(a), float(b))
    return None


def _op_gt(a: Any, b: Any) -> bool:
    pair = _numeric(a, b)
    return pair is not None and pair[0] > pair[1]


def _op_gte(a: Any, b: Any) -> bool:
    pair = _numeric(a, b)
    return pair is not None and pair[0] >= pair[1]


def _op_lt(a: Any, b: Any) -> bool:
    pair = _numeric(a, b)
    return pair is not None and pair[0] < pair[1]


def _op_lte(a: Any, b: Any) -> bool:
    pair = _numeric(a, b)
    return pair is not None and pair[0] <= pair[1]


_OPERATORS = {
    "eq": _op_eq,
    "ne": _op_ne,
    "in": _op_in,
    "gt": _op_gt,
    "gte": _op_gte,
    "lt": _op_lt,
    "lte": _op_lte,
}


def evaluate_predicate(predicate: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    """Evaluate a trigger / satisfied_when predicate against ``facts``.

    Forms:

    * ``{"all": [p, ...]}`` -> logical AND (empty -> ``True``)
    * ``{"any": [p, ...]}`` -> logical OR (empty -> ``False``)
    * ``{"field": path, "op": name, "value": v}`` -> compare
      ``facts.get(path)`` with ``v`` using the named operator.

    An absent field reads as ``None``. Numeric comparisons against a missing
    or non-numeric fact are ``False`` (the requirement can't be confirmed),
    which keeps a ``satisfied_when`` check fail-closed. An unknown operator
    raises ``ValueError`` — a malformed ruleset should fail loudly, not pass.
    """

    if "all" in predicate:
        return all(evaluate_predicate(p, facts) for p in predicate["all"])
    if "any" in predicate:
        return any(evaluate_predicate(p, facts) for p in predicate["any"])

    op_name = predicate["op"]
    operator = _OPERATORS.get(op_name)
    if operator is None:
        msg = f"Unknown predicate operator: {op_name!r}"
        raise ValueError(msg)
    return operator(facts.get(predicate["field"]), predicate["value"])


@dataclass(frozen=True)
class EnforcementSpec:
    """The layer-3 enforcement data parsed from a :attr:`RuleItem.metadata`.

    ``trigger`` gates a *conditional* item: when present and unmet the item is
    ``na``. ``satisfied_when`` is a computed check (e.g. ``refills == 0``);
    when present it decides ``satisfied`` vs ``missing`` without external
    evidence. ``requires_evidence`` means the item is satisfied by a resolving
    ``evidence_link`` (or signed statement) rather than a computed check.
    """

    flag_behavior: FlagBehavior = FlagBehavior.INFO
    requirement_level: RequirementLevel = RequirementLevel.RECOMMENDED
    trigger: Mapping[str, Any] | None = None
    satisfied_when: Mapping[str, Any] | None = None
    requires_evidence: bool = False


def enforcement_spec(item: RuleItem) -> EnforcementSpec:
    """Parse the enforcement spec from ``item``'s metadata, with safe defaults.

    A ruleset that carries no enforcement metadata (e.g. a credentialing
    ruleset) yields an advisory ``info`` / ``recommended`` spec, so the same
    evaluator works for both ruleset kinds.
    """

    meta = item.metadata or {}

    flag_raw = meta.get("flag_behavior")
    flag = FlagBehavior(flag_raw) if flag_raw is not None else FlagBehavior.INFO

    level_raw = meta.get("requirement_level")
    level = RequirementLevel(level_raw) if level_raw is not None else RequirementLevel.RECOMMENDED

    return EnforcementSpec(
        flag_behavior=flag,
        requirement_level=level,
        trigger=meta.get("trigger"),
        satisfied_when=meta.get("satisfied_when"),
        requires_evidence="evidence" in meta,
    )


@dataclass(frozen=True)
class ItemEvaluation:
    """The enforcement outcome for a single applicable item."""

    item_id: str
    status: ItemStatus
    flag_behavior: FlagBehavior
    requirement_level: RequirementLevel
    authority_ref: str | None = None

    @property
    def blocking(self) -> bool:
        """A hard-stop item that is missing blocks finalization."""

        return self.status is ItemStatus.MISSING and self.flag_behavior is FlagBehavior.HARD_STOP

    @property
    def warning(self) -> bool:
        """A soft-warn item that is missing prompts a logged override."""

        return self.status is ItemStatus.MISSING and self.flag_behavior is FlagBehavior.SOFT_WARN


@dataclass(frozen=True)
class EnforcementReport:
    """The result of enforcing a ruleset against one encounter + prescription.

    ``items`` holds every *applicable* item's evaluation (non-applicable items
    are omitted — applicability resolves first). ``can_finalize`` is ``True``
    only when no applicable hard-stop item is missing; soft warnings do not
    block but should be acknowledged with a logged reason.
    """

    items: list[ItemEvaluation] = field(default_factory=list)

    @property
    def blocking_items(self) -> list[ItemEvaluation]:
        return [e for e in self.items if e.blocking]

    @property
    def warnings(self) -> list[ItemEvaluation]:
        return [e for e in self.items if e.warning]

    @property
    def can_finalize(self) -> bool:
        return not self.blocking_items


def _has_evidence(item_id: str, evidence: Iterable[str] | Mapping[str, Any]) -> bool:
    if isinstance(evidence, Mapping):
        return evidence.get(item_id) is not None
    return item_id in set(evidence)


def evaluate_item(
    item: RuleItem,
    facts: Mapping[str, Any],
    evidence: Iterable[str] | Mapping[str, Any],
) -> ItemEvaluation:
    """Evaluate one (already-applicable) item's status + flag behavior."""

    spec = enforcement_spec(item)

    if spec.trigger is not None and not evaluate_predicate(spec.trigger, facts):
        status = ItemStatus.NA
    elif spec.satisfied_when is not None:
        status = (
            ItemStatus.SATISFIED
            if evaluate_predicate(spec.satisfied_when, facts)
            else ItemStatus.MISSING
        )
    elif _has_evidence(item.id, evidence):
        status = ItemStatus.SATISFIED
    else:
        status = ItemStatus.MISSING

    return ItemEvaluation(
        item_id=item.id,
        status=status,
        flag_behavior=spec.flag_behavior,
        requirement_level=spec.requirement_level,
        authority_ref=item.authority_ref,
    )


def evaluate_enforcement(
    ruleset: Ruleset,
    context: RuleContext,
    facts: Mapping[str, Any],
    evidence: Iterable[str] | Mapping[str, Any] = (),
) -> EnforcementReport:
    """Enforce ``ruleset`` against an encounter.

    Resolves applicability first (layer 1), then evaluates each applicable
    item's status + flag behavior (layer 3). Item order follows the ruleset.
    """

    applicable = evaluate_applicability(ruleset, context)
    return EnforcementReport(items=[evaluate_item(item, facts, evidence) for item in applicable])
