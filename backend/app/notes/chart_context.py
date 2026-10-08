# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the chart says, handed to note generation beside the transcript.

The problem list, the allergy record, the medication list and the chart
history are the clinician's own entries, so a draft takes them as written: it
names each listed diagnosis with its code, never adds a diagnosis that is
neither listed nor stated by the clinician, keeps a recorded allergy whatever
the transcript says, states the current medications as the chart lists them,
and writes each history field word for word. An allergy or a current
medication stated in the visit that the chart does not have is added after the
chart's value, marked "stated this visit", never in its place; a diagnosis
stated that way is marked the same, so it can be added at review. A
medication started, stopped or changed in the visit belongs to the plan, not
to the current list.

Read while the caller still holds its database connection, then passed in:
generation itself runs with nothing checked out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..chart_history.fields import HISTORY_KEYS, SUBSTANCE_KEYS, field_label
from ..problems.models import ProblemStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date

    from ..chart_history.models import HistoryEntry
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
class ChartHistoryField:
    key: str
    """The chart-history key, which is also the key of the note field it fills."""
    text: str
    recorded_on: date


@dataclass(frozen=True)
class ChartContext:
    """The listed diagnoses (active and rule-out, in order), the allergy record,
    the active medications and the recorded history fields (in chart order)."""

    problems: tuple[ChartProblem, ...] = ()
    allergy_status: str = "not_recorded"
    allergies: tuple[dict[str, str], ...] = ()
    medications: tuple[ChartMedication, ...] = ()
    history: tuple[ChartHistoryField, ...] = ()


def chart_context_for(
    patient: Patient,
    problems: Iterable[Problem],
    medications: Iterable[Mapping[str, object]] = (),
    history: Iterable[HistoryEntry] = (),
) -> ChartContext:
    """``medications`` are medication-repository rows; only active ones are current.
    ``history`` is the chart-history fields; a removed value is not passed on."""
    recorded = {e.field_key: e for e in history if e.text}
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
        history=tuple(
            ChartHistoryField(key, str(entry.text), entry.updated_at.date())
            for key in HISTORY_KEYS
            if (entry := recorded.get(key)) is not None
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


def _history_line(field: ChartHistoryField) -> str:
    head = f"  - {field.key} ({field_label(field.key)}, recorded {field.recorded_on.isoformat()}):"
    first, *rest = field.text.splitlines() or [""]
    return "\n".join([f"{head} {first}", *(f"    {line}" for line in rest)])


def _history_lines(chart: ChartContext) -> list[str]:
    """The recorded history, with the substance-use baseline listed apart; empty when none."""
    history = [f for f in chart.history if f.key not in SUBSTANCE_KEYS]
    baseline = [f for f in chart.history if f.key in SUBSTANCE_KEYS]
    lines: list[str] = []
    if history:
        lines.append("- Chart history:")
        lines.extend(_history_line(f) for f in history)
    if baseline:
        lines.append("- Substance use baseline (what the client uses, as recorded):")
        lines.extend(_history_line(f) for f in baseline)
    return lines


def render_chart_block(chart: ChartContext, *, full_chart: bool) -> str:
    """The chart as prompt text, with the rules for using it.

    ``full_chart`` adds the allergies, the current medications and the
    history, which only a note with a place for them is handed.
    """
    lines = ["Chart (entered by the clinician; use these values as written):"]
    if chart.problems:
        lines.append("- Problem list:")
        lines.extend(_problem_line(p) for p in chart.problems)
    else:
        lines.append("- Problem list: none recorded")
    if full_chart:
        lines.append(f"- Allergies: {allergies_line(chart)}")
        lines.extend(_medication_lines(chart))
        lines.extend(_history_lines(chart))
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
    if full_chart:
        lines.append(
            "- The allergies field always carries the chart's value as written above, "
            "even if the transcript differs: never drop it or contradict it. After it, "
            "add any allergy the client or clinician states in this visit, quoted, "
            f'followed by "{STATED_THIS_VISIT}", whatever the chart says, NKDA included. '
            "A statement that a recorded allergy was a mistake does not remove it: the "
            "chart's entry stays, and the statement may be quoted after it."
        )
        lines.append(
            "- The current medications field always carries the chart's list exactly as "
            'written above, or "None recorded". After it, add each medication the client '
            "reports currently taking that is not on the chart, quoted with the dose as "
            f'stated, followed by "{STATED_THIS_VISIT}". A medication the clinician starts, '
            "stops or changes in this visit is written in the plan, not in the current list."
        )
        lines.extend(_history_rules(chart))
    return "\n".join(lines)


def _history_rules(chart: ChartContext) -> list[str]:
    keys = {f.key for f in chart.history}
    rules = []
    if keys - set(SUBSTANCE_KEYS):
        rules.append(
            "- Where a field's instructions say it comes from the chart, write the chart "
            "history text for the field with the same key exactly as recorded: never "
            "rewrite, summarize, merge or drop it. A key with no chart history above is "
            '"Not recorded". What the client says this visit that differs is written in '
            "the visit's own fields, not in a history field."
        )
    if keys & set(SUBSTANCE_KEYS):
        rules.append(
            "- The substance use baseline is what the chart records. A substance field in "
            'the note is this visit\'s screen, not the baseline: "asked \u2014 no change" '
            "when the client was asked and described no change, the change as stated, or "
            '"Not asked" when it did not come up.'
        )
    return rules
