# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the chart says, handed to note generation beside the transcript.

The problem list and the allergy record are the clinician's own entries, so a
draft takes them as written: it names each listed diagnosis with its code,
never adds a diagnosis that is neither listed nor stated by the clinician,
and never lets the transcript overrule a recorded allergy. What the clinician
states that the chart does not have yet is marked "stated this visit" so it
can be added at review.

Read while the caller still holds its database connection, then passed in:
generation itself runs with nothing checked out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..problems.models import ProblemStatus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..models import Patient
    from ..problems.models import Problem

STATED_THIS_VISIT = "(stated this visit)"


@dataclass(frozen=True)
class ChartProblem:
    label: str
    icd10_code: str | None
    status: str


@dataclass(frozen=True)
class ChartContext:
    """The listed diagnoses (active and rule-out, in order) and the allergy record."""

    problems: tuple[ChartProblem, ...] = ()
    allergy_status: str = "not_recorded"
    allergies: tuple[dict[str, str], ...] = ()


def chart_context_for(patient: Patient, problems: Iterable[Problem]) -> ChartContext:
    return ChartContext(
        problems=tuple(
            ChartProblem(label=p.label, icd10_code=p.icd10_code, status=p.status)
            for p in problems
            if p.status in (ProblemStatus.ACTIVE, ProblemStatus.RULE_OUT)
        ),
        allergy_status=patient.allergy_status,
        allergies=tuple(patient.allergies),
    )


def _problem_line(problem: ChartProblem) -> str:
    named = f"{problem.icd10_code} {problem.label}" if problem.icd10_code else problem.label
    if problem.status == ProblemStatus.RULE_OUT:
        return f"  - {named} — rule-out, not a diagnosis"
    if problem.icd10_code is None:
        return f"  - {named} (no code recorded)"
    return f"  - {named}"


def allergies_line(chart: ChartContext) -> str:
    if chart.allergy_status == "nkda":
        return "No known drug allergies (NKDA)"
    if chart.allergy_status == "recorded" and chart.allergies:
        parts = []
        for entry in chart.allergies:
            detail = ", ".join(v for k in ("reaction", "severity") if (v := entry.get(k)))
            parts.append(f"{entry['substance']} ({detail})" if detail else entry["substance"])
        return "; ".join(parts)
    return "Not recorded"


def render_chart_block(chart: ChartContext, *, include_allergies: bool) -> str:
    """The chart as prompt text, with the rules for using it."""
    lines = ["Chart (entered by the clinician; use these values as written):"]
    if chart.problems:
        lines.append("- Problem list:")
        lines.extend(_problem_line(p) for p in chart.problems)
    else:
        lines.append("- Problem list: none recorded")
    if include_allergies:
        lines.append(f"- Allergies: {allergies_line(chart)}")
    lines.extend(
        [
            "",
            "Rules for the chart:",
            "- Where the note names diagnoses, name each active problem above with "
            "its code exactly as listed. Never assign a code the chart or the "
            "clinician did not give.",
            "- Never add a diagnosis that is neither on the problem list nor stated "
            "by the clinician in the transcript. If the clinician states one that "
            f'is not on the list, include it followed by "{STATED_THIS_VISIT}".',
            "- If the problem list is empty, write that no diagnoses are recorded "
            "rather than inferring one.",
            "- A rule-out is not a diagnosis; mention it only as a rule-out.",
        ]
    )
    if include_allergies:
        lines.append(
            "- Allergies come from the chart. When the chart records allergies or "
            "NKDA, write the chart's value even if the transcript differs. Only when "
            "the chart says not recorded and the client states an allergy, quote what "
            f'was said followed by "{STATED_THIS_VISIT}"; otherwise write "Not recorded".'
        )
    return "\n".join(lines)
