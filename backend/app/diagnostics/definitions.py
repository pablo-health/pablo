# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""In-memory shape of a diagnostic-criteria definition.

The authoritative definitions live as rows in the platform-schema
``diagnostic_definitions`` table (``code``, ``version``, ``evaluator_type``,
and a ``params`` JSON blob). This module defines the runtime objects the
evaluator operates on, plus :func:`definition_from_row` which builds one from
a stored row.

Definitions are *data*: adding a disorder (or a new version of one) is a new
row, never new code. The only thing that stays in code is the evaluator logic
(:mod:`app.diagnostics.evaluator`) and the small, fixed vocabulary of building
blocks below — deliberately *not* a stored expression language.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Supported evaluator strategies. The definition's ``evaluator_type`` selects
# one; the implementations live in code (see evaluator.py). This is a closed
# set on purpose — new *shapes* extend this vocabulary in reviewed code, they
# are not authored as free-form rules in the database.
EVALUATOR_CRITERIA = "criteria"


@dataclass(frozen=True)
class Criterion:
    """One symptom/criterion within a group."""

    key: str
    """Stable identifier (e.g. ``"A1"``); responses are keyed on this."""

    label: str
    """Self-authored, human-readable wording (clinical-review gated)."""

    cardinal: bool = False
    """Whether this is a required "core" symptom for a cardinal rule."""


@dataclass(frozen=True)
class CriterionGroup:
    """A set of criteria with a count threshold."""

    key: str
    label: str
    criteria: tuple[Criterion, ...]
    min_met: int
    """Minimum number of criteria in this group that must be met."""

    require_cardinal: bool = False
    """When true, at least one *cardinal* criterion must also be met."""


@dataclass(frozen=True)
class Gate:
    """A boolean attestation that must hold for the diagnosis (duration,
    impairment, exclusions)."""

    key: str
    label: str


@dataclass(frozen=True)
class ICD10Option:
    """A public-domain ICD-10-CM code the clinician may confirm."""

    code: str
    label: str


@dataclass(frozen=True)
class DiagnosticDefinition:
    """Runtime representation of a ``diagnostic_definitions`` row."""

    code: str
    version: int
    display_name: str
    evaluator_type: str
    criterion_groups: tuple[CriterionGroup, ...]
    gates: tuple[Gate, ...]
    icd10_options: tuple[ICD10Option, ...]
    suggested_icd10: str | None = None

    @property
    def criterion_keys(self) -> frozenset[str]:
        return frozenset(c.key for g in self.criterion_groups for c in g.criteria)

    @property
    def gate_keys(self) -> frozenset[str]:
        return frozenset(g.key for g in self.gates)

    @property
    def icd10_codes(self) -> frozenset[str]:
        return frozenset(o.code for o in self.icd10_options)


def definition_from_row(row: Mapping[str, Any]) -> DiagnosticDefinition:
    """Build a :class:`DiagnosticDefinition` from a stored definition row.

    *row* carries the definition's identity (``code``, ``version``,
    ``display_name``, ``evaluator_type``, ``suggested_icd10``) plus a ``params``
    blob::

        {
          "criterion_groups": [
            {"key": "A", "label": "...", "min_met": 5, "require_cardinal": true,
             "criteria": [{"key": "A1", "label": "...", "cardinal": true}, ...]}
          ],
          "gates": [{"key": "duration", "label": "..."}, ...],
          "icd10_options": [{"code": "...", "label": "..."}, ...]
        }

    The bundled baseline entries and the ORM-row dict both match this shape.
    """
    params = row["params"]
    groups = tuple(
        CriterionGroup(
            key=g["key"],
            label=g["label"],
            min_met=int(g["min_met"]),
            require_cardinal=bool(g.get("require_cardinal", False)),
            criteria=tuple(
                Criterion(
                    key=c["key"],
                    label=c["label"],
                    cardinal=bool(c.get("cardinal", False)),
                )
                for c in g.get("criteria", [])
            ),
        )
        for g in params.get("criterion_groups", [])
    )
    gates = tuple(Gate(key=gate["key"], label=gate["label"]) for gate in params.get("gates", []))
    options = tuple(
        ICD10Option(code=o["code"], label=o["label"]) for o in params.get("icd10_options", [])
    )
    return DiagnosticDefinition(
        code=row["code"],
        version=int(row["version"]),
        display_name=row["display_name"],
        evaluator_type=row["evaluator_type"],
        criterion_groups=groups,
        gates=gates,
        icd10_options=options,
        suggested_icd10=row.get("suggested_icd10"),
    )
