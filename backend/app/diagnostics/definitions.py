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
EVALUATOR_CHECKLIST = "checklist"
"""No-verdict strategy: responses are recorded but no pass/fail is computed —
the clinician reviews the list and decides (see ``evaluator._evaluate_checklist``)."""


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


# --------------------------------------------------------------------------
# Optional prescribing-support data
#
# A definition may *optionally* carry reference material a prescriber can
# consult while documenting their reasoning: differentials to weigh, the
# medication rationale, and jurisdiction-configurable safeguards. This is
# decision-support reference data only — like the criteria above, it is
# authored as data on the definition, and the engine never asserts that a
# differential is present or that a particular agent should be chosen. The
# clinician decides. Definitions ship with none of this by default.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DifferentialCue:
    """A phrase a prescriber might hear/read that points toward a differential."""

    cue_text: str
    citation: str | None = None


@dataclass(frozen=True)
class Differential:
    """A condition to consider or rule out before treating the primary code."""

    icd_code: str
    mimics_how: str | None = None
    """How this condition can look like the primary diagnosis."""

    distinguish_how: str | None = None
    """What distinguishes it from the primary diagnosis."""

    transcript_cues: tuple[DifferentialCue, ...] = ()
    """Cues that may prompt the prescriber to consider it — never a verdict."""


@dataclass(frozen=True)
class PrescribingSafeguard:
    """A configurable-per-jurisdiction attestation (e.g. a registry check)."""

    key: str
    label: str
    applies_when: str | None = None
    """Human-readable scope (e.g. "Schedule III+ in states with a PDMP mandate")."""

    citation: str | None = None


@dataclass(frozen=True)
class MedicationRationale:
    """Reference treatment pathway: first-line, alternatives, and the rationale."""

    first_line: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    stepped_care: str | None = None
    this_agent_now: str | None = None
    citations: tuple[str, ...] = ()


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
    differentials: tuple[Differential, ...] = ()
    prescribing_safeguards: tuple[PrescribingSafeguard, ...] = ()
    medication_rationale: MedicationRationale | None = None

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

    ``params`` may also carry optional prescribing-support keys
    (``differentials``, ``prescribing_safeguards``, ``medication_rationale``);
    they default to empty when absent.

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
    differentials = tuple(
        Differential(
            icd_code=d["icd_code"],
            mimics_how=d.get("mimics_how"),
            distinguish_how=d.get("distinguish_how"),
            transcript_cues=tuple(
                DifferentialCue(cue_text=c["cue_text"], citation=c.get("citation"))
                for c in d.get("transcript_cues", [])
            ),
        )
        for d in params.get("differentials", [])
    )
    safeguards = tuple(
        PrescribingSafeguard(
            key=s["key"],
            label=s["label"],
            applies_when=s.get("applies_when"),
            citation=s.get("citation"),
        )
        for s in params.get("prescribing_safeguards", [])
    )
    rationale_raw = params.get("medication_rationale")
    medication_rationale = (
        MedicationRationale(
            first_line=tuple(rationale_raw.get("first_line", [])),
            alternatives=tuple(rationale_raw.get("alternatives", [])),
            stepped_care=rationale_raw.get("stepped_care"),
            this_agent_now=rationale_raw.get("this_agent_now"),
            citations=tuple(rationale_raw.get("citations", [])),
        )
        if rationale_raw
        else None
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
        differentials=differentials,
        prescribing_safeguards=safeguards,
        medication_rationale=medication_rationale,
    )
