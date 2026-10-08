# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the chart says, handed to note generation beside the transcript.

The problem list, the allergy record and the medication list are the
clinician's own entries, so a draft takes them as written: it names each
listed diagnosis with its code, never adds a diagnosis that is neither listed
nor stated by the clinician, never lets the transcript overrule a recorded
allergy, and states the current medications as the chart lists them. What the
clinician states that the chart does not have yet is marked "stated this
visit" so it can be added at review; a medication started, stopped or changed
in the visit belongs to the plan, not to the current list.

Read while the caller still holds its database connection, then passed in:
generation itself runs with nothing checked out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..problems.models import ProblemStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..models import Patient
    from ..problems.models import Problem

STATED_THIS_VISIT = "(stated this visit)"


@dataclass(frozen=True)
class ChartProblem:
    label: str
    icd10_code: str | None
    status: str


@dataclass(frozen=True)
class ChartMedication:
    name: str
    dose: str
    frequency: str | None = None
    category: str | None = None
    """``psychiatric``, ``other``, or ``None`` when nobody has said which."""


@dataclass(frozen=True)
class ChartContext:
    """The listed diagnoses (active and rule-out, in order), the allergy record
    and the active medications."""

    problems: tuple[ChartProblem, ...] = ()
    allergy_status: str = "not_recorded"
    allergies: tuple[dict[str, str], ...] = ()
    medications: tuple[ChartMedication, ...] = ()


def chart_context_for(
    patient: Patient,
    problems: Iterable[Problem],
    medications: Iterable[Mapping[str, object]] = (),
) -> ChartContext:
    """``medications`` are medication-repository rows; only active ones are current."""
    return ChartContext(
        problems=tuple(
            ChartProblem(label=p.label, icd10_code=p.icd10_code, status=p.status)
            for p in problems
            if p.status in (ProblemStatus.ACTIVE, ProblemStatus.RULE_OUT)
        ),
        allergy_status=patient.allergy_status,
        allergies=tuple(patient.allergies),
        medications=tuple(
            ChartMedication(
                name=str(m["drug_name"]),
                dose=str(m["dose"]),
                frequency=str(m["frequency"]) if m.get("frequency") else None,
                category=str(m["category"]) if m.get("category") else None,
            )
            for m in medications
            if m.get("status") == "active"
        ),
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


def medication_line(medication: ChartMedication) -> str:
    named = f"{medication.name} {medication.dose}".strip()
    return f"{named}, {medication.frequency}" if medication.frequency else named


_MEDICATION_GROUPS = (("psychiatric", "Psychiatric"), ("other", "Other"), (None, "Not categorized"))


def _medication_lines(chart: ChartContext) -> list[str]:
    """The current list, split into psychiatric and other once any row says which."""
    if not chart.medications:
        return ["- Current medications: none recorded"]
    lines = ["- Current medications:"]
    if all(m.category is None for m in chart.medications):
        lines.extend(f"  - {medication_line(m)}" for m in chart.medications)
        return lines
    for category, heading in _MEDICATION_GROUPS:
        group = [m for m in chart.medications if m.category == category]
        if group:
            lines.append(f"  - {heading}:")
            lines.extend(f"    - {medication_line(m)}" for m in group)
    return lines


def render_chart_block(chart: ChartContext, *, include_prescribing: bool) -> str:
    """The chart as prompt text, with the rules for using it.

    ``include_prescribing`` adds the allergies and the current medications,
    which only a note with a place for them is handed.
    """
    lines = ["Chart (entered by the clinician; use these values as written):"]
    if chart.problems:
        lines.append("- Problem list:")
        lines.extend(_problem_line(p) for p in chart.problems)
    else:
        lines.append("- Problem list: none recorded")
    if include_prescribing:
        lines.append(f"- Allergies: {allergies_line(chart)}")
        lines.extend(_medication_lines(chart))
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
    if include_prescribing:
        lines.append(
            "- Allergies come from the chart. When the chart records allergies or "
            "NKDA, write the chart's value even if the transcript differs. Only when "
            "the chart says not recorded and the client states an allergy, quote what "
            f'was said followed by "{STATED_THIS_VISIT}"; otherwise write "Not recorded".'
        )
        lines.append(
            "- The medications field states the chart's list as given. A medication "
            "the clinician starts, stops or changes in this visit is written in the "
            "plan, not in the current list."
        )
    return "\n".join(lines)
