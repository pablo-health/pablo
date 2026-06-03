# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The single, metadata-driven diagnostic evaluator.

One entry point — :func:`evaluate` — dispatches on the definition's
``evaluator_type`` to a closed set of strategy functions. Adding a disorder is
data (a new definition row); only a genuinely new *shape* of rule adds a
strategy here, in reviewed code. This is intentionally not a stored expression
engine: the parameters live in the database, the small set of strategies does
not.

For the diagnostic engine (PABLO-6xj) the only strategy is ``"criteria"``:
every criterion group must reach its ``min_met`` (and satisfy any cardinal
requirement), and every gate must be attested true.
"""

from __future__ import annotations

from dataclasses import dataclass

from .definitions import EVALUATOR_CRITERIA, DiagnosticDefinition


@dataclass(frozen=True)
class DiagnosticOutcome:
    """Result of evaluating responses against a definition."""

    meets_criteria: bool
    unmet_reasons: tuple[str, ...]
    """Human-readable reasons the criteria are not met (empty when met)."""

    suggested_icd10: str | None
    """The definition's suggested code when criteria are met, else ``None``.

    Only a *suggestion* — the clinician confirms or picks a specifier."""


class UnknownEvaluatorTypeError(ValueError):
    """Raised when a definition names an evaluator strategy we don't implement."""


def evaluate(
    definition: DiagnosticDefinition,
    criterion_responses: dict[str, bool],
    gate_responses: dict[str, bool],
) -> DiagnosticOutcome:
    """Evaluate *criterion_responses* / *gate_responses* against *definition*."""
    if definition.evaluator_type == EVALUATOR_CRITERIA:
        return _evaluate_criteria(definition, criterion_responses, gate_responses)
    raise UnknownEvaluatorTypeError(
        f"Unsupported evaluator_type {definition.evaluator_type!r} "
        f"for definition {definition.code!r}"
    )


def _evaluate_criteria(
    definition: DiagnosticDefinition,
    criterion_responses: dict[str, bool],
    gate_responses: dict[str, bool],
) -> DiagnosticOutcome:
    """Count-threshold + cardinal + gate strategy.

    A criterion counts as met only when its response is explicitly ``True``;
    missing or ``False`` responses do not count. All gates must be explicitly
    ``True``.
    """
    reasons: list[str] = []

    for group in definition.criterion_groups:
        met = [c for c in group.criteria if criterion_responses.get(c.key) is True]
        if len(met) < group.min_met:
            reasons.append(f"{group.label}: needs at least {group.min_met}, {len(met)} met")
        if group.require_cardinal and not any(c.cardinal for c in met):
            reasons.append(f"{group.label}: requires at least one core symptom")

    for gate in definition.gates:
        if gate_responses.get(gate.key) is not True:
            reasons.append(f"Not met: {gate.label}")

    meets = not reasons
    return DiagnosticOutcome(
        meets_criteria=meets,
        unmet_reasons=tuple(reasons),
        suggested_icd10=definition.suggested_icd10 if meets else None,
    )
